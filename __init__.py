"""SQL Debug Environment — OpenEnv-compliant RL environment for SQL debugging.

Exposes the action/observation models and a thin client. The server-side
environment class lives in ``server.sql_debug_environment`` and is run by
``server.app`` via uvicorn / the OpenEnv runtime.
"""
from .models import SqlDebugAction, SqlDebugObservation

try:
    from .client import SqlDebugEnv  # re-exported for convenience
except Exception:  # pragma: no cover - client is optional at import time
    SqlDebugEnv = None  # type: ignore

__all__ = ["SqlDebugAction", "SqlDebugObservation", "SqlDebugEnv"]
