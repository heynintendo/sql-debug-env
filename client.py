"""Thin HTTP client for the SQL Debug Environment server.

Mirrors the OpenEnv ``EnvClient`` shape when that package is available, and
otherwise provides a dependency-light ``requests``-based client so that
local scripts (including ``inference.py``) can talk to the running server.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .models import SqlDebugAction, SqlDebugObservation

try:
    from openenv.core.client import EnvClient  # type: ignore

    _HAS_OPENENV = True
except ImportError:  # pragma: no cover - local dev path
    _HAS_OPENENV = False

    class EnvClient:  # minimal stand-in
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url.rstrip("/")


import requests


class SqlDebugEnv(EnvClient):
    """Client wrapper exposing reset/step/state against a running server."""

    def __init__(self, base_url: str = "http://localhost:7860") -> None:
        super().__init__(base_url)
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def _flatten(data: Dict[str, Any]) -> Dict[str, Any]:
        """Merge an openenv-core ``{observation, reward, done}`` envelope
        into a single flat dict for our Pydantic ``SqlDebugObservation``.
        Handles both the openenv-core shape and the local fallback shape.
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

    # ------------------------------------------------------------------
    def reset(
        self,
        task_id: Optional[str] = None,
        seed: Optional[int] = None,
        timeout: float = 30.0,
    ) -> SqlDebugObservation:
        payload: Dict[str, Any] = {}
        if task_id is not None:
            payload["task_id"] = task_id
        if seed is not None:
            payload["seed"] = seed
        r = requests.post(f"{self.base_url}/reset", json=payload, timeout=timeout)
        r.raise_for_status()
        return SqlDebugObservation(**self._flatten(r.json()))

    def step(
        self, action: SqlDebugAction, timeout: float = 30.0
    ) -> SqlDebugObservation:
        action_payload = (
            action.model_dump() if hasattr(action, "model_dump") else action.dict()  # type: ignore[attr-defined]
        )
        r = requests.post(
            f"{self.base_url}/step",
            json={"action": action_payload},
            timeout=timeout,
        )
        r.raise_for_status()
        return SqlDebugObservation(**self._flatten(r.json()))

    def state(self, timeout: float = 10.0) -> Dict[str, Any]:
        r = requests.get(f"{self.base_url}/state", timeout=timeout)
        r.raise_for_status()
        data = r.json()
        return data.get("state", data)

    def close(self) -> None:
        # Nothing to tear down for an HTTP client.
        pass
