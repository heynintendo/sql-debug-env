"""Pydantic models for the SQL Debug Environment.

Uses a dual-import pattern so the module works both inside the Docker image
(where ``openenv-core`` is installed) and in local dev without it.
"""
from __future__ import annotations

from typing import Any, Dict

from pydantic import Field

try:
    from openenv.core.env_server.types import Action, Observation  # type: ignore
except ImportError:  # pragma: no cover - fallback for local dev
    from pydantic import BaseModel

    class Action(BaseModel):
        pass

    class Observation(BaseModel):
        done: bool = False
        reward: float | None = None
        metadata: dict = Field(default_factory=dict)


class SqlDebugAction(Action):
    """A single action submitted by the agent.

    The environment supports five action types:

    * ``fix``       - submit a corrected SQL query for grading (the only
                      action that gets a real reward)
    * ``check``     - test a query against the hidden expected output and
                      get a pass/fail summary (does NOT reveal expected rows)
    * ``describe``  - inspect a table's structure via PRAGMA table_info
    * ``diagnostic``- run a read-only SELECT to investigate the data
    * ``explain``   - get EXPLAIN QUERY PLAN for a query

    The old single-field action format ``{"query": "..."}`` is still
    accepted - ``action_type`` defaults to ``"fix"`` and ``query`` defaults
    to an empty string so old clients continue to work without changes.
    """

    action_type: str = Field(
        default="fix",
        description=(
            "One of: 'fix' (submit corrected query for grading), "
            "'check' (test a query and get pass/fail summary), "
            "'describe' (inspect a table structure), "
            "'diagnostic' (run a read-only SELECT to investigate the data), "
            "'explain' (get the query execution plan)."
        ),
    )
    query: str = Field(
        default="",
        description="SQL query for fix/check/diagnostic/explain actions.",
    )
    table_name: str = Field(
        default="",
        description="Table name for describe actions.",
    )


class SqlDebugObservation(Observation):
    """Observation returned after reset() or step().

    The agent does NOT see the gold expected output. It sees the schema,
    the buggy query, a vague hint, and (after a step) the result of its
    most recent action. To compare a candidate result against the hidden
    gold answer, the agent must submit a ``check`` action, which returns
    only a pass/fail summary.
    """

    task_id: str = ""
    difficulty: str = ""
    schema_sql: str = ""
    buggy_query: str = ""
    hint: str = ""
    # Result of the most recent ``fix`` action (or empty if last action
    # wasn't a fix). Formatted as a text table or an error message.
    query_result: str = ""
    # Result of the most recent ``check`` action. A short pass/fail
    # summary - it never reveals the actual expected rows, only whether
    # they match.
    check_result: str = ""
    # Result of the most recent describe/diagnostic/explain action. A
    # formatted text table, query plan, or error message.
    diagnostic_result: str = ""
    # Type of the action that produced this observation
    # ("fix"/"check"/"describe"/"diagnostic"/"explain", or empty on reset).
    action_type: str = ""
    is_error: bool = False
    last_action_error: str | None = None
    steps_taken: int = 0
    max_steps: int = 10
    # Per-component grader output from the most recent fix action. Empty
    # on reset and on non-fix actions. Contains ``syntax_valid``,
    # ``column_match``, ``row_count_match``, ``value_match``,
    # ``order_match``, ``raw_score``, ``step_penalty_factor``, ``error``.
    grader_breakdown: Dict[str, Any] = Field(default_factory=dict)
