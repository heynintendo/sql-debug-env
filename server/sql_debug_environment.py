"""SQL Debug Environment - OpenEnv-compliant environment class.

Each episode presents one buggy SQL query over a freshly-built in-memory
SQLite database. The agent interacts via five action types:

    fix        - submit a corrected query for grading (only graded action)
    check      - test a query against the hidden oracle (pass/fail summary
                 only). LIMITED to CHECK_LIMIT uses per episode to prevent
                 binary-search reverse-engineering of the expected output.
    describe   - inspect a table's structure (PRAGMA table_info + COUNT)
    diagnostic - run a read-only SELECT to investigate the data
    explain    - get EXPLAIN QUERY PLAN for a query

Info-gathering actions return differentiated rewards based on how
productive they tend to be: describe/diagnostic 0.03, explain 0.02,
check (FAIL) 0.03, check (PASS) 0.05. All inside (0.01, 0.99).

The database is built ONCE per episode and persisted across actions.
Write operations are rejected in fix/diagnostic/explain to keep the
shared DB clean. This is much faster than rebuilding the 200-500 row
tables on every action.
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


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Info action rewards (differentiated by usefulness, all in (0.01, 0.99))
REWARD_DESCRIBE = 0.03
REWARD_DIAGNOSTIC = 0.03
REWARD_EXPLAIN = 0.02
REWARD_CHECK_FAIL = 0.03
REWARD_CHECK_PASS = 0.05

# Max rows shown by a diagnostic action
DIAGNOSTIC_MAX_ROWS = 50

# Maximum number of check actions per episode. Prevents an agent from
# binary-searching the hidden oracle by hammering /step with different
# candidate queries.
CHECK_LIMIT = 2

# Keywords that are forbidden in fix and diagnostic actions. This keeps
# the shared per-episode database clean and prevents an agent from
# corrupting state between steps.
WRITE_FORBIDDEN = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "REPLACE", "TRUNCATE", "ATTACH", "DETACH", "PRAGMA",
    "VACUUM", "REINDEX", "ANALYZE",
}
VALID_ACTION_TYPES = {"fix", "check", "describe", "diagnostic", "explain"}


def _first_keyword(query: str) -> str:
    """Extract the first SQL keyword after any leading comments/whitespace."""
    if not query:
        return ""
    q = re.sub(r"/\*.*?\*/", " ", query, flags=re.DOTALL)
    q = re.sub(r"--[^\n]*", " ", q)
    q = q.strip()
    m = re.match(r"(\w+)", q)
    return m.group(1).upper() if m else ""


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
    # Persistent DB connection. One per episode, reused across actions.
    # Writes are rejected in fix/diagnostic/explain so this stays clean.
    db_conn: Optional[sqlite3.Connection] = None
    # Per-episode counters
    checks_used: int = 0
    # Last action feedback fields
    last_query_result: str = ""
    last_check_result: str = ""
    last_diagnostic_result: str = ""
    last_action_type: str = ""
    last_is_error: bool = False
    last_error_msg: Optional[str] = None
    last_breakdown: Dict[str, Any] = field(default_factory=dict)


class SqlDebugEnvironment(Environment):
    """OpenEnv Environment implementation for SQL query debugging."""

    def __init__(self) -> None:
        self._rng = random.Random(0xC0FFEE)
        self._episode: Optional[_EpisodeState] = None

    # ------------------------------------------------------------------
    # Query execution helpers
    # ------------------------------------------------------------------

    def _run_query(
        self, query: str
    ) -> Tuple[Optional[List[str]], Optional[List[Tuple]], Optional[str]]:
        """Execute ``query`` against the PERSISTENT per-episode DB.

        Returns (columns, rows, error). On error, columns and rows are
        None and error is the exception message.
        """
        assert self._episode is not None
        con = self._episode.db_conn
        if con is None:
            return None, None, "EnvironmentError: database not initialized"
        try:
            cur = con.execute(query)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            return cols, rows, None
        except Exception as e:
            return None, None, f"{type(e).__name__}: {e}"

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _handle_fix(self, query: str) -> None:
        ep = self._episode
        assert ep is not None

        # Reject write statements so the persistent DB stays clean.
        first = _first_keyword(query)
        if first in WRITE_FORBIDDEN:
            err = f"fix queries must be read-only SELECT; '{first}' is not allowed"
            ep.last_query_result = f"ERROR: {err}"
            ep.last_is_error = True
            ep.last_error_msg = err
            ep.last_reward = clamp_score(0.05)
            ep.last_breakdown = {
                "syntax_valid": 0.0,
                "column_match": 0.0,
                "row_count_match": 0.0,
                "value_match": 0.0,
                "order_match": 0.0,
                "raw_score": 0.05,
                "step_penalty_factor": 1.0,
                "error": err,
            }
            return

        cols, rows, err = self._run_query(query)

        if err is not None:
            ep.last_query_result = f"ERROR: {err}"
            ep.last_is_error = True
            ep.last_error_msg = err
        else:
            assert cols is not None and rows is not None
            preview_rows = list(rows[:DIAGNOSTIC_MAX_ROWS])
            ep.last_query_result = format_result(cols, preview_rows)
            if len(rows) > DIAGNOSTIC_MAX_ROWS:
                ep.last_query_result += f"\n(... {len(rows) - DIAGNOSTIC_MAX_ROWS} more rows truncated)"
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
            difficulty=ep.difficulty,
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
        ep = self._episode
        assert ep is not None

        # Enforce check limit to prevent binary-search exploit.
        if ep.checks_used >= CHECK_LIMIT:
            ep.last_check_result = (
                f"Check limit reached ({CHECK_LIMIT} per episode). "
                "Use 'fix' to submit your answer or 'diagnostic' to "
                "investigate further."
            )
            ep.last_reward = clamp_score(REWARD_CHECK_FAIL)
            ep.last_breakdown = {
                "action_type": "check",
                "info_only": True,
                "check_limit_reached": True,
            }
            return

        ep.checks_used += 1

        cols, rows, err = self._run_query(query)
        if err is not None:
            ep.last_check_result = f"FAIL: query errored: {err}"
            ep.last_reward = clamp_score(REWARD_CHECK_FAIL)
        else:
            assert cols is not None and rows is not None
            n_actual = len(rows)
            n_expected = len(ep.expected_rows)
            if list(cols) != list(ep.expected_columns):
                ep.last_check_result = (
                    f"FAIL: column mismatch. Got {len(cols)} column(s), "
                    f"expected {len(ep.expected_columns)}."
                )
                ep.last_reward = clamp_score(REWARD_CHECK_FAIL)
            elif n_actual != n_expected:
                ep.last_check_result = (
                    f"FAIL: row count mismatch. Got {n_actual} row(s), "
                    f"expected {n_expected}."
                )
                ep.last_reward = clamp_score(REWARD_CHECK_FAIL)
            elif list(rows) == list(ep.expected_rows):
                ep.last_check_result = (
                    f"PASS: all {n_actual} row(s) match the expected output "
                    "exactly."
                )
                ep.last_reward = clamp_score(REWARD_CHECK_PASS)
            else:
                # Same shape, different content. Report positional match
                # count only (not WHICH rows match). This is informative
                # enough to guide an iterating agent but not enough to
                # binary-search the expected output.
                matching = sum(
                    1 for i, r in enumerate(rows)
                    if i < len(ep.expected_rows) and tuple(r) == tuple(ep.expected_rows[i])
                )
                ep.last_check_result = (
                    f"FAIL: same shape but content differs. "
                    f"{matching} of {n_actual} row(s) match at the correct position."
                )
                ep.last_reward = clamp_score(REWARD_CHECK_FAIL)
        ep.last_breakdown = {
            "action_type": "check",
            "info_only": True,
            "checks_used": ep.checks_used,
            "checks_remaining": CHECK_LIMIT - ep.checks_used,
        }

    def _handle_describe(self, table_name: str) -> None:
        ep = self._episode
        assert ep is not None
        if not table_name:
            ep.last_diagnostic_result = "ERROR: describe action requires 'table_name'."
            ep.last_is_error = True
            ep.last_error_msg = "missing table_name"
            ep.last_reward = clamp_score(REWARD_DESCRIBE)
            ep.last_breakdown = {"action_type": "describe", "info_only": True}
            return

        con = ep.db_conn
        try:
            rows = con.execute(f"PRAGMA table_info({table_name})").fetchall()
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
                    lines.append(f"  {name:<18s} {ctype:<10s}{flag_str}")
                ep.last_diagnostic_result = "\n".join(lines)
                ep.last_is_error = False
                ep.last_error_msg = None
        except Exception as e:
            ep.last_diagnostic_result = f"ERROR: {type(e).__name__}: {e}"
            ep.last_is_error = True
            ep.last_error_msg = str(e)
        ep.last_reward = clamp_score(REWARD_DESCRIBE)
        ep.last_breakdown = {"action_type": "describe", "info_only": True}

    def _handle_diagnostic(self, query: str) -> None:
        ep = self._episode
        assert ep is not None
        if not query.strip():
            ep.last_diagnostic_result = "ERROR: diagnostic action requires a query."
            ep.last_is_error = True
            ep.last_error_msg = "missing query"
            ep.last_reward = clamp_score(REWARD_DIAGNOSTIC)
            ep.last_breakdown = {"action_type": "diagnostic", "info_only": True}
            return

        first = _first_keyword(query)
        if first in WRITE_FORBIDDEN:
            ep.last_diagnostic_result = (
                f"ERROR: diagnostic queries must be read-only SELECT statements. "
                f"'{first}' is not allowed."
            )
            ep.last_is_error = True
            ep.last_error_msg = f"forbidden keyword: {first}"
            ep.last_reward = clamp_score(REWARD_DIAGNOSTIC)
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
        ep.last_reward = clamp_score(REWARD_DIAGNOSTIC)
        ep.last_breakdown = {"action_type": "diagnostic", "info_only": True}

    def _handle_explain(self, query: str) -> None:
        ep = self._episode
        assert ep is not None
        if not query.strip():
            ep.last_diagnostic_result = "ERROR: explain action requires a query."
            ep.last_is_error = True
            ep.last_error_msg = "missing query"
            ep.last_reward = clamp_score(REWARD_EXPLAIN)
            ep.last_breakdown = {"action_type": "explain", "info_only": True}
            return

        first = _first_keyword(query)
        if first in WRITE_FORBIDDEN:
            ep.last_diagnostic_result = (
                f"ERROR: explain only supports read-only SELECT queries. "
                f"'{first}' is not allowed."
            )
            ep.last_is_error = True
            ep.last_error_msg = f"forbidden keyword: {first}"
            ep.last_reward = clamp_score(REWARD_EXPLAIN)
            ep.last_breakdown = {"action_type": "explain", "info_only": True}
            return

        con = ep.db_conn
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
        ep.last_reward = clamp_score(REWARD_EXPLAIN)
        ep.last_breakdown = {"action_type": "explain", "info_only": True}

    # ------------------------------------------------------------------
    # Observation builder
    # ------------------------------------------------------------------

    def _build_observation(self) -> SqlDebugObservation:
        ep = self._episode
        assert ep is not None
        return SqlDebugObservation(
            done=ep.done,
            reward=ep.last_reward,
            metadata={
                "task_id": ep.task_id,
                "difficulty": ep.difficulty,
                "steps_taken": ep.steps_taken,
                "max_steps": ep.max_steps,
                "checks_used": ep.checks_used,
                "checks_remaining": CHECK_LIMIT - ep.checks_used,
            },
            task_id=ep.task_id,
            difficulty=ep.difficulty,
            schema_sql=ep.schema_sql,
            buggy_query=ep.buggy_query,
            # NOTE: expected_output is NOT exposed to the agent.
            hint=ep.hint,
            query_result=ep.last_query_result,
            check_result=ep.last_check_result,
            diagnostic_result=ep.last_diagnostic_result,
            action_type=ep.last_action_type,
            is_error=ep.last_is_error,
            last_action_error=ep.last_error_msg,
            steps_taken=ep.steps_taken,
            max_steps=ep.max_steps,
            checks_remaining=max(0, CHECK_LIMIT - ep.checks_used),
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
        """Start a new episode.

        Builds the SQLite database ONCE and stores the connection on
        the episode state. Subsequent actions in the same episode reuse
        this connection, which is much faster than rebuilding 200-500
        row tables on every step.
        """
        if seed is not None:
            self._rng = random.Random(seed)

        task = self._rng.choice(TASKS) if task_id is None else get_task(task_id)

        # Close the previous episode's DB if we have one.
        if self._episode is not None and self._episode.db_conn is not None:
            try:
                self._episode.db_conn.close()
            except Exception:
                pass

        # Build the persistent per-episode database.
        con = sqlite3.connect(":memory:")
        con.executescript(task["schema_sql"])
        try:
            cur = con.execute(task["correct_query"])
            exp_rows = cur.fetchall()
            exp_cols = [d[0] for d in cur.description] if cur.description else []
        except Exception as e:
            # If the gold query is broken, surface that loudly. Shouldn't
            # happen in practice - all tasks are verified at build time.
            con.close()
            raise RuntimeError(f"gold query failed for {task['task_id']}: {e}")

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
            db_conn=con,
            checks_used=0,
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
            ep.last_reward = SCORE_MIN
            return self._build_observation()

        ep.steps_taken += 1
        action_type = (getattr(action, "action_type", None) or "fix").lower()
        ep.last_action_type = action_type

        # Clear previous per-action fields.
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
            ep.last_reward = clamp_score(REWARD_DIAGNOSTIC)
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
        # No-op to avoid breaking the HTTP REST singleton-factory flow.
        # The per-episode DB connection is closed on the next reset().
        pass

    @staticmethod
    def available_task_ids() -> List[str]:
        return list_task_ids()
