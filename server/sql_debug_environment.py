"""SQL Debug Environment - OpenEnv-compliant environment class.

Each episode presents one buggy SQL query over a freshly-built in-memory
SQLite database. The agent interacts with the environment through five
action types:

    fix        - submit a corrected query for grading
    check      - test a query against the hidden expected output and get
                 a pass/fail summary (never reveals the actual expected rows)
    describe   - inspect a table's structure via PRAGMA table_info
    diagnostic - run a read-only SELECT to investigate the data
    explain    - get EXPLAIN QUERY PLAN for a query

Only ``fix`` actions get graded. The other four all cost a step and return
a flat information reward (``INFO_REWARD`` = 0.02). This is the same step
budget as a fix attempt, so an agent can't infinitely spam diagnostics.
Episodes end when a fix produces the exact gold result set or when
``steps_taken`` hits ``max_steps``.

The agent NEVER sees the gold expected_output. Fix or check actions
return a pass/fail summary against the hidden answer. This is the key
change vs the previous iteration of the environment, which leaked the
gold output and collapsed the debugging task into reverse-engineering.
"""
from __future__ import annotations

import random
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from ..models import SqlDebugAction, SqlDebugObservation  # type: ignore
except ImportError:
    from models import SqlDebugAction, SqlDebugObservation  # type: ignore

try:
    from openenv.core.env_server import Environment  # type: ignore
except ImportError:
    class Environment:  # minimal stand-in
        pass

try:
    from .grader import SCORE_MAX, SCORE_MIN, clamp_score, format_result, grade
    from .tasks import TASKS, get_task, list_task_ids
except ImportError:
    from grader import SCORE_MAX, SCORE_MIN, clamp_score, format_result, grade  # type: ignore
    from tasks import TASKS, get_task, list_task_ids  # type: ignore


# Reward for information-gathering actions (describe/diagnostic/explain/check).
# Must be strictly inside (0, 1). Acts as a floor that distinguishes an
# info action from an error (0.05) or a near-perfect fix (~0.98).
INFO_REWARD = 0.02
# Max rows shown by the diagnostic action (to keep the observation tidy).
DIAGNOSTIC_MAX_ROWS = 20
# Keywords that disqualify a diagnostic query - we only allow read-only
# SELECT statements. The check happens on the first non-whitespace token
# so comments before a CTE are fine.
DIAGNOSTIC_FORBIDDEN = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "REPLACE", "TRUNCATE", "ATTACH", "DETACH", "PRAGMA",
    "VACUUM", "REINDEX", "ANALYZE",
}
VALID_ACTION_TYPES = {"fix", "check", "describe", "diagnostic", "explain"}


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
    schema_sql: str
    buggy_query: str
    correct_query: str
    hint: str
    # last-action feedback fields
    last_query_result: str = ""
    last_check_result: str = ""
    last_diagnostic_result: str = ""
    last_action_type: str = ""
    last_is_error: bool = False
    last_error_msg: Optional[str] = None
    last_breakdown: Dict[str, Any] = field(default_factory=dict)


def _first_keyword(query: str) -> str:
    """Extract the first SQL keyword after any leading comments/whitespace."""
    if not query:
        return ""
    # Strip /* ... */ and -- ... line comments
    q = re.sub(r"/\*.*?\*/", " ", query, flags=re.DOTALL)
    q = re.sub(r"--[^\n]*", " ", q)
    q = q.strip()
    m = re.match(r"(\w+)", q)
    return m.group(1).upper() if m else ""


class SqlDebugEnvironment(Environment):
    """OpenEnv Environment implementation for SQL query debugging."""

    def __init__(self) -> None:
        self._rng = random.Random(0xC0FFEE)
        self._episode: Optional[_EpisodeState] = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fresh_conn(self) -> sqlite3.Connection:
        """Build a fresh :memory: SQLite database pre-seeded with the
        current task's schema. Every action rebuilds this - since our
        supported actions are read-only (we reject writes in diagnostic),
        there's no state to preserve between actions within an episode,
        and rebuilding is simpler than cloning a connection."""
        assert self._episode is not None
        con = sqlite3.connect(":memory:")
        con.executescript(self._episode.schema_sql)
        return con

    def _run_query(
        self, query: str
    ) -> Tuple[Optional[List[str]], Optional[List[Tuple]], Optional[str]]:
        """Execute ``query`` against a fresh DB for the current task.

        Returns (columns, rows, error). On error, columns and rows are
        None and error is the exception message.
        """
        assert self._episode is not None
        con = self._fresh_conn()
        try:
            cur = con.execute(query)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            return cols, rows, None
        except Exception as e:
            return None, None, f"{type(e).__name__}: {e}"
        finally:
            con.close()

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _handle_fix(self, query: str) -> None:
        """Grade a fix attempt against the hidden gold answer."""
        ep = self._episode
        assert ep is not None

        cols, rows, err = self._run_query(query)

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
            correct_query=ep.correct_query,
        )
        ep.last_reward = clamp_score(reward)
        ep.last_breakdown = breakdown

        perfect = (
            err is None
            and cols is not None
            and rows is not None
            and list(cols) == list(ep.expected_columns)
            and list(rows) == list(ep.expected_rows)
        )
        if perfect:
            ep.done = True

    def _handle_check(self, query: str) -> None:
        """Test a query and return a pass/fail summary without revealing
        the actual expected rows."""
        ep = self._episode
        assert ep is not None

        cols, rows, err = self._run_query(query)
        if err is not None:
            ep.last_check_result = f"FAIL: query errored: {err}"
        else:
            assert cols is not None and rows is not None
            n_actual = len(rows)
            n_expected = len(ep.expected_rows)
            if list(cols) != list(ep.expected_columns):
                ep.last_check_result = (
                    f"FAIL: column mismatch. Got {len(cols)} column(s), "
                    f"expected {len(ep.expected_columns)}."
                )
            elif n_actual != n_expected:
                ep.last_check_result = (
                    f"FAIL: row count mismatch. Got {n_actual} row(s), "
                    f"expected {n_expected}."
                )
            elif list(rows) == list(ep.expected_rows):
                ep.last_check_result = (
                    f"PASS: all {n_actual} row(s) match the expected output "
                    "exactly."
                )
            else:
                # Same shape, but content differs. Count how many rows
                # appear at the right position.
                matching = sum(
                    1 for i, r in enumerate(rows)
                    if i < len(ep.expected_rows) and tuple(r) == tuple(ep.expected_rows[i])
                )
                ep.last_check_result = (
                    f"FAIL: same shape but content differs. "
                    f"{matching} of {n_actual} row(s) match at the correct position."
                )
        ep.last_reward = clamp_score(INFO_REWARD)
        ep.last_breakdown = {"action_type": "check", "info_only": True}

    def _handle_describe(self, table_name: str) -> None:
        """Describe a table's schema via PRAGMA table_info + COUNT(*)."""
        ep = self._episode
        assert ep is not None
        if not table_name:
            ep.last_diagnostic_result = "ERROR: describe action requires 'table_name'."
            ep.last_is_error = True
            ep.last_error_msg = "missing table_name"
            ep.last_reward = clamp_score(INFO_REWARD)
            ep.last_breakdown = {"action_type": "describe", "info_only": True}
            return

        con = self._fresh_conn()
        try:
            rows = con.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()
            if not rows:
                ep.last_diagnostic_result = (
                    f"ERROR: table '{table_name}' not found in this database."
                )
                ep.last_is_error = True
                ep.last_error_msg = f"unknown table: {table_name}"
            else:
                try:
                    count = con.execute(
                        f"SELECT COUNT(*) FROM {table_name}"
                    ).fetchone()[0]
                except Exception:
                    count = "?"
                lines = [f"Table: {table_name} ({count} rows)", "Columns:"]
                for cid, name, ctype, notnull, dflt, pk in rows:
                    flags = []
                    if pk:
                        flags.append("PRIMARY KEY")
                    if notnull:
                        flags.append("NOT NULL")
                    flag_str = "  " + " ".join(flags) if flags else ""
                    lines.append(f"  {name:<16s} {ctype:<10s}{flag_str}")
                ep.last_diagnostic_result = "\n".join(lines)
                ep.last_is_error = False
                ep.last_error_msg = None
        except Exception as e:
            ep.last_diagnostic_result = f"ERROR: {type(e).__name__}: {e}"
            ep.last_is_error = True
            ep.last_error_msg = str(e)
        finally:
            con.close()
        ep.last_reward = clamp_score(INFO_REWARD)
        ep.last_breakdown = {"action_type": "describe", "info_only": True}

    def _handle_diagnostic(self, query: str) -> None:
        """Run a read-only SELECT to investigate the data."""
        ep = self._episode
        assert ep is not None
        if not query.strip():
            ep.last_diagnostic_result = "ERROR: diagnostic action requires a query."
            ep.last_is_error = True
            ep.last_error_msg = "missing query"
            ep.last_reward = clamp_score(INFO_REWARD)
            ep.last_breakdown = {"action_type": "diagnostic", "info_only": True}
            return

        first = _first_keyword(query)
        if first in DIAGNOSTIC_FORBIDDEN:
            ep.last_diagnostic_result = (
                f"ERROR: diagnostic queries must be read-only SELECT statements. "
                f"'{first}' is not allowed."
            )
            ep.last_is_error = True
            ep.last_error_msg = f"forbidden keyword: {first}"
            ep.last_reward = clamp_score(INFO_REWARD)
            ep.last_breakdown = {"action_type": "diagnostic", "info_only": True}
            return

        cols, rows, err = self._run_query(query)
        if err is not None:
            ep.last_diagnostic_result = f"ERROR: {err}"
            ep.last_is_error = True
            ep.last_error_msg = err
        else:
            assert cols is not None and rows is not None
            truncated = list(rows[:DIAGNOSTIC_MAX_ROWS])
            body = format_result(cols, truncated)
            if len(rows) > DIAGNOSTIC_MAX_ROWS:
                body += f"\n(... {len(rows) - DIAGNOSTIC_MAX_ROWS} more rows truncated)"
            ep.last_diagnostic_result = body
            ep.last_is_error = False
            ep.last_error_msg = None
        ep.last_reward = clamp_score(INFO_REWARD)
        ep.last_breakdown = {"action_type": "diagnostic", "info_only": True}

    def _handle_explain(self, query: str) -> None:
        """Run EXPLAIN QUERY PLAN on the given query."""
        ep = self._episode
        assert ep is not None
        if not query.strip():
            ep.last_diagnostic_result = "ERROR: explain action requires a query."
            ep.last_is_error = True
            ep.last_error_msg = "missing query"
            ep.last_reward = clamp_score(INFO_REWARD)
            ep.last_breakdown = {"action_type": "explain", "info_only": True}
            return

        con = self._fresh_conn()
        try:
            rows = con.execute(f"EXPLAIN QUERY PLAN {query}").fetchall()
            lines = ["Query plan:"]
            for r in rows:
                lines.append("  " + " | ".join(str(x) for x in r))
            ep.last_diagnostic_result = "\n".join(lines)
            ep.last_is_error = False
            ep.last_error_msg = None
        except Exception as e:
            ep.last_diagnostic_result = f"ERROR: {type(e).__name__}: {e}"
            ep.last_is_error = True
            ep.last_error_msg = str(e)
        finally:
            con.close()
        ep.last_reward = clamp_score(INFO_REWARD)
        ep.last_breakdown = {"action_type": "explain", "info_only": True}

    # ------------------------------------------------------------------
    # Observation builder
    # ------------------------------------------------------------------

    def _build_observation(self) -> SqlDebugObservation:
        ep = self._episode
        assert ep is not None
        # Reward is already clamped at the grader boundary. This is a
        # defensive assignment to make the value explicit; trust the
        # grader, don't re-clamp.
        return SqlDebugObservation(
            done=ep.done,
            reward=ep.last_reward,
            metadata={
                "task_id": ep.task_id,
                "difficulty": ep.difficulty,
                "steps_taken": ep.steps_taken,
                "max_steps": ep.max_steps,
            },
            task_id=ep.task_id,
            difficulty=ep.difficulty,
            schema_sql=ep.schema_sql,
            buggy_query=ep.buggy_query,
            # NOTE: expected_output is NOT exposed to the agent. The
            # grader uses ep.expected_columns/rows internally.
            hint=ep.hint,
            query_result=ep.last_query_result,
            check_result=ep.last_check_result,
            diagnostic_result=ep.last_diagnostic_result,
            action_type=ep.last_action_type,
            is_error=ep.last_is_error,
            last_action_error=ep.last_error_msg,
            steps_taken=ep.steps_taken,
            max_steps=ep.max_steps,
            grader_breakdown=dict(ep.last_breakdown),
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
        """Start a new episode."""
        if seed is not None:
            self._rng = random.Random(seed)

        task = self._rng.choice(TASKS) if task_id is None else get_task(task_id)

        # Precompute the gold result set for grading. This is the only
        # place we actually evaluate the correct_query.
        con = sqlite3.connect(":memory:")
        try:
            con.executescript(task["schema_sql"])
            cur = con.execute(task["correct_query"])
            exp_rows = cur.fetchall()
            exp_cols = [d[0] for d in cur.description] if cur.description else []
        finally:
            con.close()

        self._episode = _EpisodeState(
            task_id=task["task_id"],
            difficulty=task["difficulty"],
            max_steps=int(task["max_steps"]),
            steps_taken=0,
            done=False,
            last_reward=SCORE_MIN,
            expected_columns=list(exp_cols),
            expected_rows=list(exp_rows),
            schema_sql=task["schema_sql"],
            buggy_query=task["buggy_query"],
            correct_query=task["correct_query"],
            hint=task["hint"],
            last_query_result="",
            last_check_result="",
            last_diagnostic_result="",
            last_action_type="",
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
            # No-op once done; return floor reward.
            ep.last_reward = SCORE_MIN
            return self._build_observation()

        ep.steps_taken += 1
        action_type = (getattr(action, "action_type", None) or "fix").lower()
        ep.last_action_type = action_type

        # Clear previous per-action output so each step's observation
        # only reflects the most recent action.
        ep.last_query_result = ""
        ep.last_check_result = ""
        ep.last_diagnostic_result = ""
        ep.last_is_error = False
        ep.last_error_msg = None
        ep.last_breakdown = {}

        query = getattr(action, "query", "") or ""
        table_name = getattr(action, "table_name", "") or ""

        if action_type == "fix":
            self._handle_fix(query)
        elif action_type == "check":
            self._handle_check(query)
        elif action_type == "describe":
            self._handle_describe(table_name)
        elif action_type == "diagnostic":
            self._handle_diagnostic(query)
        elif action_type == "explain":
            self._handle_explain(query)
        else:
            ep.last_diagnostic_result = (
                f"ERROR: unknown action_type '{action_type}'. "
                f"Valid types: {sorted(VALID_ACTION_TYPES)}"
            )
            ep.last_is_error = True
            ep.last_error_msg = f"unknown action_type: {action_type}"
            ep.last_reward = clamp_score(INFO_REWARD)
            ep.last_breakdown = {"action_type": action_type, "info_only": True}

        if ep.steps_taken >= ep.max_steps:
            ep.done = True

        return self._build_observation()

    @property
    def state(self) -> Dict[str, Any]:
        if self._episode is None:
            return {
                "task_id": None,
                "steps_taken": 0,
                "done": False,
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
        # No-op: openenv-core HTTP handlers call close() in a finally
        # block after every request. Clearing state here would break
        # the HTTP REST flow when a singleton factory reuses the instance
        # across reset -> step.
        pass

    @staticmethod
    def available_task_ids() -> List[str]:
        return list_task_ids()
