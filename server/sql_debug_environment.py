"""SQL Debug Environment — OpenEnv-compliant environment class.

Each episode presents one buggy SQL query over a freshly-built in-memory
SQLite database. The agent submits replacement queries; each attempt is
executed and graded for partial credit. Episodes end when the agent earns
a near-perfect reward or runs out of the per-task step budget.
"""
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from ..models import SqlDebugAction, SqlDebugObservation  # type: ignore
except ImportError:  # running with PYTHONPATH=/app inside Docker
    from models import SqlDebugAction, SqlDebugObservation  # type: ignore

try:
    from openenv.core.env_server import Environment  # type: ignore
except ImportError:  # local dev without openenv-core installed
    class Environment:  # minimal stand-in; real base class has same surface
        pass

try:
    from .grader import SCORE_MAX, SCORE_MIN, clamp_score, format_result, grade
    from .tasks import TASKS, get_task, list_task_ids
except ImportError:  # when run as top-level module inside Docker
    from grader import SCORE_MAX, SCORE_MIN, clamp_score, format_result, grade  # type: ignore
    from tasks import TASKS, get_task, list_task_ids  # type: ignore


def _safe_reward(value: Any) -> float:
    """Coerce any value into a clamped reward in [SCORE_MIN, SCORE_MAX].

    This is the single choke point every observation reward passes through
    before being serialized to the wire. Handles None, non-numeric, NaN,
    +/-inf, and out-of-range values by falling back to SCORE_MIN. The Phase
    2 validator rejects exact 0.0 or 1.0 anywhere in the response, so we
    refuse to emit them from this function.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return SCORE_MIN
    if f != f or f in (float("inf"), float("-inf")):
        return SCORE_MIN
    return clamp_score(f)


@dataclass
class _EpisodeState:
    task_id: str
    difficulty: str
    max_steps: int
    steps_taken: int
    done: bool
    last_reward: float
    expected_columns: List[str]
    expected_rows: List[Tuple]
    expected_output: str
    schema_sql: str
    buggy_query: str
    hint: str
    # last attempt feedback (so observations after step reflect the attempt)
    last_query_result: str = ""
    last_is_error: bool = False
    last_error_msg: Optional[str] = None
    # Component-level grader breakdown for the last step. Exposed in the
    # observation's ``metadata`` field so agents can see exactly which part
    # of the reward they lost and learn a more targeted policy. Empty dict
    # on reset (no step has been graded yet).
    last_breakdown: Dict[str, Any] = field(default_factory=dict)


class SqlDebugEnvironment(Environment):
    """OpenEnv Environment implementation for SQL query debugging."""

    def __init__(self) -> None:
        self._rng = random.Random(0xC0FFEE)
        self._episode: Optional[_EpisodeState] = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run_query(
        self, query: str
    ) -> Tuple[Optional[List[str]], Optional[List[Tuple]], Optional[str]]:
        """Execute ``query`` against a freshly-built DB for the current task.

        Returns (columns, rows, error). On error, columns and rows are None
        and error is the exception message.
        """
        assert self._episode is not None
        con = sqlite3.connect(":memory:")
        try:
            con.executescript(self._episode.schema_sql)
            cur = con.execute(query)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            return cols, rows, None
        except Exception as e:  # sqlite3.Error + syntax errors
            return None, None, f"{type(e).__name__}: {e}"
        finally:
            con.close()

    def _build_observation(self) -> SqlDebugObservation:
        ep = self._episode
        assert ep is not None
        # Double-clamp on the way out. ep.last_reward is already clamped in
        # step(), but routing every outgoing observation through _safe_reward
        # means the serializer can NEVER see None, NaN, 0.0, or 1.0 on the
        # reward field even if a future edit introduces a bug upstream.
        safe_reward = _safe_reward(ep.last_reward)
        ep.last_reward = safe_reward
        return SqlDebugObservation(
            done=ep.done,
            reward=safe_reward,
            metadata={
                "task_id": ep.task_id,
                "difficulty": ep.difficulty,
                "steps_taken": ep.steps_taken,
                "max_steps": ep.max_steps,
            },
            # FIX 4: per-component grader output surfaced as a top-level
            # observation field. openenv-core's serializer strips the
            # ``metadata`` dict from the HTTP response, so the breakdown
            # has to live here where it'll actually make it onto the wire.
            grader_breakdown=dict(ep.last_breakdown),
            task_id=ep.task_id,
            difficulty=ep.difficulty,
            schema_sql=ep.schema_sql,
            buggy_query=ep.buggy_query,
            expected_output=ep.expected_output,
            hint=ep.hint,
            query_result=ep.last_query_result,
            is_error=ep.last_is_error,
            last_action_error=ep.last_error_msg,
            steps_taken=ep.steps_taken,
            max_steps=ep.max_steps,
        )

    # ------------------------------------------------------------------
    # OpenEnv API
    # ------------------------------------------------------------------

    def reset(
        self,
        task_id: Optional[str] = None,
        seed: Optional[int] = None,
        **_: Any,
    ) -> SqlDebugObservation:
        """Start a new episode. If ``task_id`` is given, load that task;
        otherwise sample one deterministically from the task list.
        """
        if seed is not None:
            self._rng = random.Random(seed)

        if task_id is None:
            task = self._rng.choice(TASKS)
        else:
            task = get_task(task_id)

        # Build expected result by running the gold query.
        con = sqlite3.connect(":memory:")
        try:
            con.executescript(task["schema_sql"])
            cur = con.execute(task["correct_query"])
            exp_rows = cur.fetchall()
            exp_cols = [d[0] for d in cur.description] if cur.description else []
        finally:
            con.close()
        expected_output = format_result(exp_cols, exp_rows)

        self._episode = _EpisodeState(
            task_id=task["task_id"],
            difficulty=task["difficulty"],
            max_steps=int(task["max_steps"]),
            steps_taken=0,
            done=False,
            # Initial reward must be strictly > 0 (never exactly 0.0 — the
            # Phase 2 validator rejects 0.0). SCORE_MIN (0.01) is the floor
            # we use everywhere rewards are emitted.
            last_reward=SCORE_MIN,
            expected_columns=list(exp_cols),
            expected_rows=list(exp_rows),
            expected_output=expected_output,
            schema_sql=task["schema_sql"],
            buggy_query=task["buggy_query"],
            hint=task["hint"],
            last_query_result="",
            last_is_error=False,
            last_error_msg=None,
            last_breakdown={},
        )
        return self._build_observation()

    def step(self, action: SqlDebugAction) -> SqlDebugObservation:
        if self._episode is None:
            raise RuntimeError("step() called before reset()")
        ep = self._episode
        if ep.done:
            # No-op once done; return current observation with the floor
            # reward (must stay strictly above 0).
            ep.last_reward = SCORE_MIN
            return self._build_observation()

        ep.steps_taken += 1
        cols, rows, err = self._run_query(action.query)

        if err is not None:
            ep.last_query_result = f"ERROR: {err}"
            ep.last_is_error = True
            ep.last_error_msg = err
        else:
            assert cols is not None and rows is not None
            ep.last_query_result = format_result(cols, rows)
            ep.last_is_error = False
            ep.last_error_msg = None

        reward, breakdown = grade(
            query_error=err,
            actual_columns=cols or [],
            actual_rows=rows or [],
            expected_columns=ep.expected_columns,
            expected_rows=ep.expected_rows,
            steps_taken=ep.steps_taken,
        )
        ep.last_reward = _safe_reward(reward)
        ep.last_breakdown = breakdown

        # An exact result-set match counts as a solve — even though the
        # emitted reward is clamped to SCORE_MAX (0.99) it's still the
        # highest signal the grader can produce, and we want to end the
        # episode there instead of wasting the remaining step budget.
        perfect = (
            err is None
            and cols is not None
            and rows is not None
            and list(cols) == list(ep.expected_columns)
            and list(rows) == list(ep.expected_rows)
        )
        if perfect or ep.steps_taken >= ep.max_steps:
            ep.done = True

        return self._build_observation()

    @property
    def state(self) -> Dict[str, Any]:
        if self._episode is None:
            return {
                "task_id": None,
                "steps_taken": 0,
                "done": False,
                # Never emit exactly 0.0 anywhere — the Phase 2 validator
                # treats it as a forbidden boundary value. Use SCORE_MIN even
                # in the "no episode yet" fallback for consistency with the
                # rest of the reward path.
                "last_reward": SCORE_MIN,
            }
        ep = self._episode
        return {
            "task_id": ep.task_id,
            "steps_taken": ep.steps_taken,
            "done": ep.done,
            "last_reward": ep.last_reward,
        }

    def close(self) -> None:
        # No-op: the openenv-core HTTP handlers call close() in a finally
        # block after every /reset and /step request. Clearing state here
        # would break the HTTP REST flow (state must survive across the
        # reset->step sequence when the same singleton instance is reused
        # via the env factory). WebSocket sessions manage isolation
        # externally, so we leave teardown to the garbage collector.
        pass

    # Handy for the inference script / debugging.
    @staticmethod
    def available_task_ids() -> List[str]:
        return list_task_ids()
