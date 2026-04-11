"""Shared utilities for sql-debug-env.

Contains helpers that are used by both the client-side code
(``inference.py``, ``client.py``) and any external consumer that
talks to the HTTP server.
"""
from __future__ import annotations

from typing import Any, Dict


def flatten_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Merge an openenv-core ``{observation, reward, done}`` envelope
    into a single flat dict suitable for constructing a
    ``SqlDebugObservation``.

    openenv-core's ``HTTPEnvServer`` serializes responses as
    ``{"observation": {...custom fields...}, "reward": r, "done": d}``
    - i.e. ``reward`` and ``done`` live at the top level, NOT inside
    ``observation``. This helper hoists them into the observation dict
    so callers don't have to care about the envelope shape.

    Accepts both envelope-shaped and flat responses.
    """
    obs = data.get("observation")
    if not isinstance(obs, dict):
        return data
    merged = dict(obs)
    if "reward" in data and data["reward"] is not None:
        merged["reward"] = data["reward"]
    if "done" in data and data["done"] is not None:
        merged["done"] = data["done"]
    return merged
