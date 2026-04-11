"""FastAPI entrypoint for the SQL Debug Environment.

When ``openenv-core`` is installed we hand the environment to ``create_app``
and inherit its WebSocket-first routing (``/reset``, ``/step``, ``/state``,
``/health``). We then additively register:

    * GET  ``/``       — human-readable landing page judges see on HF Spaces.
    * GET  ``/tasks``  — list of available task ids.

When ``openenv-core`` isn't installed (local dev) we fall back to a tiny
hand-rolled FastAPI app exposing the same HTTP surface so the validator,
the baseline inference script and curl-based smoke tests all work.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from ..models import SqlDebugAction, SqlDebugObservation  # type: ignore
except ImportError:
    from models import SqlDebugAction, SqlDebugObservation  # type: ignore

try:
    from .sql_debug_environment import SqlDebugEnvironment
except ImportError:
    from sql_debug_environment import SqlDebugEnvironment  # type: ignore

try:
    from .tasks import TASKS
except ImportError:
    from tasks import TASKS  # type: ignore


# ---------------------------------------------------------------------------
# Shared environment instance (see long comment in create_app branch below).
# ---------------------------------------------------------------------------

_SHARED_ENV = SqlDebugEnvironment()


# ---------------------------------------------------------------------------
# Landing page — rendered at GET / for judges browsing the HF Space URL.
# ---------------------------------------------------------------------------

def _render_landing_html() -> str:
    task_rows = "\n".join(
        f"        <tr><td><code>{t['task_id']}</code></td>"
        f"<td>{t['difficulty']}</td>"
        f"<td>{t['hint']}</td></tr>"
        for t in TASKS
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>sql-debug-env — OpenEnv</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       max-width: 920px; margin: 2em auto; padding: 0 1em; color: #222; }}
h1 {{ border-bottom: 2px solid #333; padding-bottom: 0.3em; }}
h2 {{ margin-top: 1.6em; }}
code {{ background: #f4f4f4; padding: 0.1em 0.4em; border-radius: 3px; }}
pre {{ background: #f4f4f4; padding: 1em; border-radius: 6px; overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 0.5em; }}
th, td {{ border: 1px solid #ddd; padding: 0.4em 0.7em; text-align: left; vertical-align: top; }}
th {{ background: #fafafa; }}
.badge {{ display: inline-block; padding: 0.15em 0.6em; border-radius: 10px;
         background: #eef; color: #225; font-size: 0.85em; }}
</style>
</head>
<body>
<h1>sql-debug-env <span class="badge">OpenEnv · v1.0.0</span></h1>
<p>An OpenEnv-compliant RL environment where an agent is handed a real SQLite
schema, a buggy SQL query, the expected output and a hint, and must iteratively
repair the query. Each attempt runs against a fresh in-memory database and is
graded with a smooth, partial-credit reward.</p>

<h2>HTTP endpoints</h2>
<ul>
  <li><code>GET  /</code>         — this landing page</li>
  <li><code>GET  /health</code>   — liveness check</li>
  <li><code>POST /reset</code>    — start a new episode; body <code>{{}}</code> or <code>{{"task_id": "..."}}</code></li>
  <li><code>POST /step</code>     — submit a corrected query; body <code>{{"action": {{"query": "SELECT ..."}}}}</code></li>
  <li><code>GET  /state</code>    — current episode state</li>
  <li><code>GET  /tasks</code>    — list of available task ids</li>
</ul>

<h2>Quick test</h2>
<pre>curl -s -X POST $URL/reset -H 'Content-Type: application/json' -d '{{}}'
curl -s -X POST $URL/step  -H 'Content-Type: application/json' \\
     -d '{{"action": {{"query": "SELECT 1;"}}}}'</pre>

<h2>Reward function</h2>
<p>Weighted: 0.15 syntax_valid + 0.25 column_match (Jaccard) +
0.20 row_count_match + 0.40 value_match. Times a step penalty of
<code>(1 - 0.02·steps)</code>. Error floor 0.05. All rewards are clamped to
<code>[0.01, 0.99]</code>.</p>

<h2>Task catalog</h2>
<table>
  <thead><tr><th>task_id</th><th>difficulty</th><th>bug / hint</th></tr></thead>
  <tbody>
{task_rows}
  </tbody>
</table>

<p style="margin-top: 2em; color: #888; font-size: 0.85em;">
Built for the OpenEnv hackathon. See the repo for full docs and the baseline
inference agent in <code>inference.py</code>.</p>
</body>
</html>"""


LANDING_HTML = _render_landing_html()
TASK_IDS: List[str] = [t["task_id"] for t in TASKS]


# ---------------------------------------------------------------------------
# Primary path: wrap openenv-core's create_app and add our extras.
# ---------------------------------------------------------------------------

try:
    from fastapi import FastAPI  # noqa: F401 (used in fallback branch)
    from fastapi.responses import HTMLResponse
    from openenv.core.env_server import create_app  # type: ignore

    # openenv-core calls the ``env`` argument as a factory on every HTTP
    # request and calls ``close()`` on the returned instance in a finally
    # block. For the stateless HTTP REST flow used by the validator and the
    # baseline inference script we want a single shared instance so that
    # state survives across /reset -> /step calls; ``close()`` is a no-op on
    # our Environment so this is safe.
    app = create_app(
        lambda: _SHARED_ENV,
        SqlDebugAction,
        SqlDebugObservation,
        env_name="sql-debug-env",
    )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def _landing() -> str:
        return LANDING_HTML

    @app.get("/tasks")
    def _list_tasks() -> Dict[str, Any]:
        return {"task_ids": TASK_IDS}

except ImportError:
    # ------------------------------------------------------------------
    # Fallback FastAPI surface — matches the OpenEnv HTTP contract so the
    # same client / validator / inference script work against both.
    # ------------------------------------------------------------------
    from fastapi import Body, FastAPI
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel

    app = FastAPI(title="sql-debug-env")

    class ResetRequest(BaseModel):
        task_id: Optional[str] = None
        seed: Optional[int] = None

    class StepRequest(BaseModel):
        action: Dict[str, Any]

    def _obs_to_dict(obs: SqlDebugObservation) -> Dict[str, Any]:
        if hasattr(obs, "model_dump"):
            return obs.model_dump()
        return obs.dict()  # type: ignore[attr-defined]

    def _envelope(obs: SqlDebugObservation) -> Dict[str, Any]:
        d = _obs_to_dict(obs)
        # Match the openenv-core envelope shape so clients don't care which
        # server is running: reward/done are lifted to the top level.
        return {
            "observation": d,
            "reward": d.get("reward"),
            "done": d.get("done", False),
        }

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def landing() -> str:
        return LANDING_HTML

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "healthy", "env": "sql-debug-env"}

    @app.post("/reset")
    def reset(body: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
        # Accept both an empty body {} and a body with task_id/seed.
        task_id = body.get("task_id") if isinstance(body, dict) else None
        seed = body.get("seed") if isinstance(body, dict) else None
        obs = _SHARED_ENV.reset(task_id=task_id, seed=seed)
        return _envelope(obs)

    @app.post("/step")
    def step(req: StepRequest) -> Dict[str, Any]:
        action = SqlDebugAction(**req.action)
        obs = _SHARED_ENV.step(action)
        return _envelope(obs)

    @app.get("/state")
    def state() -> Dict[str, Any]:
        return {"state": _SHARED_ENV.state}

    @app.get("/tasks")
    def tasks() -> Dict[str, Any]:
        return {"task_ids": TASK_IDS}


# ---------------------------------------------------------------------------
# Process entrypoint. The `[project.scripts]` table maps `server` to this
# function so `uv run server` and `openenv validate` both recognise it.
# ---------------------------------------------------------------------------

def main() -> None:
    """Start uvicorn on port 7860 (matches openenv.yaml and the Dockerfile).

    The HOST / PORT env vars override the defaults so operators can
    redirect without touching the code.
    """
    import os

    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "7860"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
