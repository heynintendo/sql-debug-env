"""Pydantic models for the SQL Debug Environment.

Uses a dual-import pattern so the module works both inside the Docker image
(where ``openenv-core`` is installed) and in local dev without it.
"""
from __future__ import annotations

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
    """A single repair attempt: the agent submits a replacement SQL query."""

    query: str = Field(..., description="The corrected SQL query")


class SqlDebugObservation(Observation):
    """Observation returned after reset() or step().

    Fields cover the full debugging context the agent needs: schema, the
    original buggy query, the target output, a hint, and feedback from the
    most recent attempt.
    """

    task_id: str = ""
    difficulty: str = ""
    schema_sql: str = ""
    buggy_query: str = ""
    expected_output: str = ""
    hint: str = ""
    query_result: str = ""
    is_error: bool = False
    last_action_error: str | None = None
    steps_taken: int = 0
    max_steps: int = 10
