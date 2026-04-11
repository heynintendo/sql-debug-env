---
title: sql-debug-env
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: apache-2.0
short_description: OpenEnv SQL debugging env with partial credit
tags:
  - openenv
  - reinforcement-learning
  - sql
  - debugging
---

# sql-debug-env

An OpenEnv environment for SQL query debugging. The agent is handed a
SQLite schema, a broken query, and a short hint that describes the
symptom a user would notice (not the cause or the fix). The agent then
uses a five-verb action language to investigate the data, check
candidate fixes, and eventually submit a corrected query. Every fix
attempt is executed against a fresh in-memory SQLite database and
graded with a smooth, partial-credit reward.

Crucially, the agent does NOT see the gold expected output. It has to
reason about what the query should produce from the schema and the
hint, then fix the query. This is the same position an engineer is in
when they're staring at a broken query in a production system.

## Why SQL debugging

- Fixing broken SQL is something engineers and analysts do every day,
  and there's no existing OpenEnv environment for it.
- The bugs cover a real difficulty range. The easy ones are
  one-character typos. The hard ones include window PARTITION BY bugs,
  NULL-skip COUNT semantics, self-join aliasing, and HAVING vs WHERE
  scoping. The expert ones chain 2-3 compounding bugs in real-world
  query patterns (CTEs, correlated subqueries, window functions,
  LEFT JOIN + aggregate propagation).
- The reward signal is smooth across five components (syntax, column
  match, row count, per-column value overlap, positional row order) so
  an agent gets useful gradient even before it fixes every bug.

## Action space

Every action includes an `action_type` field. The old single-field
format `{"query": "..."}` still works and defaults to `action_type:
"fix"` for backward compatibility.

| `action_type` | Required fields        | What it does                                                                       |
|---------------|------------------------|------------------------------------------------------------------------------------|
| `fix`         | `query`                | Submit a corrected SQL query. This is the only action that gets a real reward.    |
| `check`       | `query`                | Test a query against the hidden expected output. Returns a pass/fail summary only. |
| `describe`    | `table_name`           | Inspect a table via `PRAGMA table_info` + `COUNT(*)`.                              |
| `diagnostic`  | `query` (SELECT only)  | Run a read-only SELECT to investigate the data. Output capped at 20 rows.          |
| `explain`     | `query`                | Run `EXPLAIN QUERY PLAN` and return the plan.                                      |

Every action costs 1 step from the per-task budget, including
information-gathering ones. The `check` action returns a summary like
"PASS: all 5 rows match", "FAIL: column mismatch", or "FAIL: same shape
but content differs. 3 of 5 rows match at the correct position". It
never reveals the actual expected rows.

Only `fix` actions are graded. The other four return a flat `0.02`
information reward. The episode ends when a `fix` action produces an
exact row-for-row match or when the step budget is exhausted.

## Observation space

The observation intentionally does NOT include an expected-output
field. An agent that wants to know what the query should return has to
work it out from the schema, the hint, and its own `check`/`diagnostic`
actions.

| Field               | Type        | Description                                                                  |
|---------------------|-------------|------------------------------------------------------------------------------|
| `done`              | bool        | True when the episode is over.                                               |
| `reward`            | float       | Reward for the latest step, strictly inside (0, 1).                          |
| `task_id`           | str         | Identifier of the current task.                                              |
| `difficulty`        | str         | `"easy"`, `"medium"`, `"hard"`, or `"expert"`.                               |
| `schema_sql`        | str         | Full `CREATE TABLE` + `INSERT` script for this task.                         |
| `buggy_query`       | str         | The original broken query to repair.                                         |
| `hint`              | str         | Short symptom-based hint. Does not describe the fix.                         |
| `query_result`      | str         | Result of the most recent `fix` attempt, or error message.                   |
| `check_result`      | str         | Pass/fail summary of the most recent `check` action.                         |
| `diagnostic_result` | str         | Output of the most recent `describe`/`diagnostic`/`explain` action.          |
| `action_type`       | str         | Type of the action that produced this observation.                           |
| `is_error`          | bool        | Whether the last action raised a runtime error.                              |
| `last_action_error` | str \| None | Raw error message for the last action, or `None`.                            |
| `steps_taken`       | int         | Steps used in the current episode.                                           |
| `max_steps`         | int         | Per-task step budget (5 / 8 / 10 / 12 for easy / medium / hard / expert).    |
| `grader_breakdown`  | dict        | Per-component grader scores after a `fix` action (see Reward function).      |

## Task catalog

22 tasks across four difficulty tiers and eight schemas.

### Easy (max 5 steps): syntax and typo bugs

| task_id                  | schema     | bug                                                           |
|--------------------------|------------|---------------------------------------------------------------|
| `easy_01_typo`           | employees  | `SELCT` misspelling of `SELECT`.                              |
| `easy_02_wrong_column`   | employees  | References a column that doesn't exist in the schema.         |
| `easy_03_string_quotes`  | orders     | Unquoted string literal in `WHERE`.                           |
| `easy_04_trailing_comma` | sales      | Stray trailing comma in the `SELECT` list.                    |

### Medium (max 8 steps): join / grouping / filter bugs

| task_id                           | schema     | bug                                                        |
|-----------------------------------|------------|------------------------------------------------------------|
| `medium_01_inner_vs_left_join`    | employees  | INNER JOIN drops employees with no department.             |
| `medium_02_missing_group_by`      | orders     | Aggregate without `GROUP BY`.                              |
| `medium_03_wrong_order_direction` | sales      | `ORDER BY ... ASC LIMIT 5` instead of `DESC`.              |
| `medium_04_or_vs_and`             | employees  | `OR` instead of `AND` in a conjunctive filter.             |

### Hard (max 10 steps): subtle semantic bugs

| task_id                          | schema    | bug                                                             |
|----------------------------------|-----------|-----------------------------------------------------------------|
| `hard_01_null_equality`          | employees | `manager_id = NULL` instead of `IS NULL`.                       |
| `hard_02_having_vs_where`        | orders    | Aggregate filter in WHERE instead of HAVING.                    |
| `hard_03_window_partition`       | sales     | Window function partitions by the wrong column.                 |
| `hard_04_date_off_by_one`        | orders    | Half-open range excludes the last day of February 2024.         |
| `hard_05_count_null_skip`        | hospital  | `COUNT(column)` skips NULLs so still-admitted patients are undercounted per doctor. |
| `hard_06_self_join_double_count` | projects  | Self-join uses `<>` not `<` and double-counts every pair of team members. |

### Expert (max 12 steps): 2-3 compounding bugs

These tasks require the agent to identify and fix multiple bugs at
once. The buggy query runs to completion (no errors) but produces
wrong output, so the grader gives partial credit for each bug fixed.

| task_id                           | schema    | compounding bugs                                                                                 |
|-----------------------------------|-----------|--------------------------------------------------------------------------------------------------|
| `expert_01_library_multi_bug`     | library   | `= 'USA'` only instead of `IN ('USA','UK')`, missing `HAVING COUNT >= 3`, `ORDER BY ... ASC`.    |
| `expert_02_library_complex_join`  | library   | INNER JOIN drops books never checked out, wrong `IS NULL` column, selects id not name.           |
| `expert_03_student_window_agg`    | students  | Window `PARTITION BY` wrong column, `rnk > 1` not `= 1`, wrong ORDER BY.                          |
| `expert_04_student_date_null`     | students  | `LIKE '%Fall%'` matches every year, sums `course_id` not `credits`, HAVING uses OR.              |
| `expert_05_null_revenue_leak`     | ecommerce | Revenue sums refunded purchases, no COALESCE on NULL totals, WHERE clause drops LEFT JOIN users. |
| `expert_06_window_running_total`  | hospital  | Wrong `PARTITION BY`, reversed window order, extra outcome filter drops ongoing treatments.      |
| `expert_07_top_per_group`         | ecommerce | `ROW_NUMBER()` missing `PARTITION BY`, wrong rank filter, no refunded filter, missing DISTINCT.  |
| `expert_08_cte_progress_tracking` | projects  | Missing billable filter in CTE, missing GROUP BY in milestone CTE, missing active project filter. |

The eight schemas are: employees/departments, customers/products/orders,
sales, authors/books/checkouts (library), students/courses/enrollments,
users/sessions/purchases (ecommerce), patients/doctors/treatments/lab_results
(hospital), and team_members/projects/time_entries/milestones
(project management). Row counts per table range from 5 to 45 so the
result sets are big enough to be interesting but small enough that
partial-credit differences are visible.

## Reward function

Implemented in `server/grader.py`. When a `fix` action runs successfully
the reward is a weighted sum of five components:

| Component         | Weight | What it measures                                                             |
|-------------------|--------|------------------------------------------------------------------------------|
| `syntax_valid`    | 0.10   | `1.0` if the query parsed and executed, else `0.0`.                          |
| `column_match`    | 0.20   | Jaccard similarity of actual vs expected column name sets.                   |
| `row_count_match` | 0.15   | `min(actual, expected) / max(actual, expected)`.                             |
| `value_match`     | 0.35   | Per-column multiset overlap of cell values (position-insensitive within column). |
| `order_match`     | 0.20   | Positional row match, only counted when the gold query has `ORDER BY`.       |

Then:
- **Step penalty**: multiply by `max(0, 1 - 0.02 * steps_taken)`.
- **Error floor**: if the query errors, return `0.05` flat.
- **Info reward**: non-fix actions (check/describe/diagnostic/explain) return `0.02`.
- **Clamp**: every reward is clamped to the strictly-interior range `[0.01, 0.99]`.

`order_match` is what stops a correct-rows-wrong-order query from
scoring the same as a correctly-ordered one. If the gold query has no
`ORDER BY`, `order_match` is 1.0 (ordering isn't part of the spec). If
the gold query does have `ORDER BY`, the agent earns credit proportional
to how many rows appear at the correct index.

Every step's observation includes a `grader_breakdown` field so the
agent can see which component it lost:

```json
{
  "syntax_valid": 1.0,
  "column_match": 1.0,
  "row_count_match": 0.4,
  "value_match": 0.5,
  "order_match": 0.8,
  "raw_score": 0.72,
  "step_penalty_factor": 0.98,
  "error": null
}
```

The grader is fully deterministic.

## Episode lifecycle

1. `reset(task_id=None)` picks the requested task (or samples one),
   builds a fresh in-memory SQLite database, runs the gold query to
   compute the hidden expected result set, and returns the first
   observation with `done=False` and `reward=0.01`.
2. `step(action)` runs the action, scores it (for `fix` actions only),
   and updates the observation. The episode ends when a `fix` action
   produces an exact row-for-row match of the gold result set, or when
   `steps_taken` hits `max_steps`.
3. `state` returns `{task_id, steps_taken, done, last_reward}`.

## Setup

### Run it locally without Docker

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install openenv-core fastapi uvicorn pydantic requests openai
PYTHONPATH=. python -m server.app
```

That starts uvicorn on port 7860. Quick sanity check:

```bash
curl -s http://localhost:7860/health
# {"status":"healthy"}

curl -s -X POST http://localhost:7860/reset \
     -H "Content-Type: application/json" -d '{}'

# fix action
curl -s -X POST http://localhost:7860/step \
     -H "Content-Type: application/json" \
     -d '{"action":{"action_type":"fix","query":"SELECT full_name, salary FROM employees WHERE dept_id = 1 ORDER BY salary DESC;"}}'

# describe action
curl -s -X POST http://localhost:7860/step \
     -H "Content-Type: application/json" \
     -d '{"action":{"action_type":"describe","table_name":"employees"}}'

# diagnostic action
curl -s -X POST http://localhost:7860/step \
     -H "Content-Type: application/json" \
     -d '{"action":{"action_type":"diagnostic","query":"SELECT * FROM employees LIMIT 3"}}'

# check action
curl -s -X POST http://localhost:7860/step \
     -H "Content-Type: application/json" \
     -d '{"action":{"action_type":"check","query":"SELECT full_name FROM employees WHERE dept_id = 1"}}'
```

### Run it in Docker

```bash
docker build -f server/Dockerfile -t sql-debug-env .
docker run --rm -p 7860:7860 sql-debug-env
```

The image runs comfortably inside a 2 vCPU / 8 GB RAM budget. SQLite is
in-process, there's no GPU dependency, and it's a single uvicorn worker.

## Running the baseline agent

`inference.py` lives in the repo root. It talks to a running server
over HTTP and prints the `[START]` / `[STEP]` / `[END]` log lines the
grading harness expects.

```bash
# optional: enables the LLM path. Without it a deterministic heuristic
# fallback takes over so the script still runs for local testing.
export HF_TOKEN=<your token>
export API_BASE_URL=https://router.huggingface.co/v1
export MODEL_NAME=Qwen/Qwen2.5-72B-Instruct

# point at a running server. Defaults to the deployed HF Space.
export ENV_BASE_URL=http://localhost:7860

python inference.py
```

When the LLM path is active, the agent is prompted with a symptom-only
hint and no expected output. It can emit any of the five action types,
is encouraged to explore the schema and data first, and can check a
candidate fix against the hidden oracle before committing it.

### Baseline scores

Two baselines are shown. The heuristic is a deterministic string-replace
table with full coverage of easy/medium/hard and partial coverage of
expert (it fixes only one of the 2-3 compounding bugs per expert task).
The LLM baseline is an estimate based on manual testing with frontier
models running through the full action language.

#### Heuristic baseline (lookup table, run against the live Space)

| Difficulty | Tasks | Score per task                                                              | Avg   |
|------------|-------|-----------------------------------------------------------------------------|-------|
| easy       | 4     | 0.98 / 0.98 / 0.98 / 0.98                                                   | 0.980 |
| medium     | 4     | 0.98 / 0.98 / 0.98 / 0.98                                                   | 0.980 |
| hard       | 6     | 0.98 / 0.98 / 0.98 / 0.98 / 0.98 / 0.98                                     | 0.980 |
| expert     | 8     | 0.524 / 0.607 / 0.679 / 0.592 / 0.461 / 0.624 / 0.325 / 0.523               | 0.542 |
| overall    | 22    |                                                                             | 0.821 |

The expert scores are meaningful: the heuristic fixes exactly one of
the 2-3 compounding bugs per task, and the grader reports partial
credit that reflects how much of the expected result set was recovered.
`expert_07_top_per_group` scores 0.325 because fixing only the rank
filter still leaves the missing PARTITION BY and the missing DISTINCT,
which produce very different rows. `expert_03_student_window_agg`
scores 0.679 because fixing the rank filter already yields a result
that overlaps significantly with the gold answer.

#### LLM baseline (estimated)

These estimates are based on manual testing with frontier models. The
exact numbers depend on the model, the prompting, and how well the
agent uses the exploration actions (`describe`, `diagnostic`, `check`).

| Difficulty | Tasks | Expected score range | Notes                                                                          |
|------------|-------|---------------------|--------------------------------------------------------------------------------|
| easy       | 4     | 0.95 - 0.98         | Trivial syntax fixes.                                                          |
| medium     | 4     | 0.80 - 0.95         | Logic bugs a strong LLM handles once it reads the schema.                      |
| hard       | 6     | 0.60 - 0.85         | NULL, window, self-join, COUNT-skip bugs. Models that skip investigation lose here. |
| expert     | 8     | 0.30 - 0.60         | Multi-bug tasks with vague hints. Requires check actions to iterate reliably. |

The heuristic numbers are an upper bound on easy/medium/hard because
the heuristic is a cheat sheet. On expert tasks, a real LLM should
BEAT the heuristic numbers if it uses the exploration and check
actions effectively.

## Validation

```bash
./validate-submission.sh https://your-space.hf.space .
```

Three checks: the HF Space is live, the Docker image builds, and
`openenv validate` passes.

## Repo layout

```
sql-debug-env/
|-- __init__.py
|-- client.py                   # HTTP EnvClient
|-- models.py                   # SqlDebugAction, SqlDebugObservation
|-- openenv.yaml                # OpenEnv manifest
|-- pyproject.toml
|-- uv.lock
|-- README.md
|-- inference.py                # baseline agent, lives in root on purpose
|-- validate-submission.sh
`-- server/
    |-- __init__.py
    |-- app.py                  # FastAPI entrypoint + main()
    |-- sql_debug_environment.py
    |-- tasks.py                # 22 task definitions
    |-- grader.py               # 5-component partial-credit grader
    |-- requirements.txt
    `-- Dockerfile
```
