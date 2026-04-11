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

This is an OpenEnv environment for SQL query debugging. The agent gets a
SQLite schema, a broken query, the output that query is supposed to produce,
and a short hint about what's wrong. It then submits fixed queries one at a
time. Each attempt runs against a fresh in-memory database and gets a
partial-credit reward, so the agent can tell it's getting closer even before
the query is fully correct.

## Why SQL debugging

I picked this domain for a few reasons:

- Fixing broken SQL is something engineers and analysts actually do every
  day, and as far as I can tell there's no existing OpenEnv environment for
  it.
- The bugs cover a nice difficulty range. The easy ones are one-character
  typos. The hard ones are the kind of subtle semantic traps (NULL
  comparisons, HAVING vs WHERE, window partitioning) that I still mess up
  occasionally.
- The reward signal is smooth instead of binary. An agent that got the
  right columns but the wrong row count still gets meaningful credit, which
  gives RL training a gradient to climb.

## Action space

`SqlDebugAction` has one field:

| Field   | Type | Description                    |
|---------|------|--------------------------------|
| `query` | str  | The corrected SQL query.       |

## Observation space

`SqlDebugObservation` extends the OpenEnv base `Observation`. The extra
fields are everything the agent needs to understand the task and iterate on
a fix:

| Field               | Type        | Description                                                      |
|---------------------|-------------|------------------------------------------------------------------|
| `done`              | bool        | True when the episode is over.                                   |
| `reward`            | float       | Reward for the latest step, strictly inside (0, 1).              |
| `metadata`          | dict        | `{task_id, difficulty, steps_taken, max_steps}`.                 |
| `task_id`           | str         | Identifier of the current task.                                  |
| `difficulty`        | str         | "easy", "medium", or "hard".                                     |
| `schema_sql`        | str         | Full CREATE TABLE and INSERT script for this task.               |
| `buggy_query`       | str         | The original broken query to repair.                             |
| `expected_output`   | str         | Formatted text table of the correct result set.                  |
| `hint`              | str         | Short natural-language hint about the bug.                       |
| `query_result`      | str         | Formatted output (or "ERROR: ...") from the last submitted query.|
| `is_error`          | bool        | Whether the last query raised a SQLite error.                    |
| `last_action_error` | str or None | Raw error message from the last step, or None.                   |
| `steps_taken`       | int         | Number of steps taken so far this episode.                       |
| `max_steps`         | int         | Per-task step budget (5 / 8 / 10 for easy / medium / hard).      |

## Task catalog

There are 16 tasks across four difficulty tiers. All of them live in
`server/tasks.py`.

### Easy, 5-step budget: syntax and typo bugs

| task_id                  | bug                                                    |
|--------------------------|--------------------------------------------------------|
| `easy_01_typo`           | `SELCT` misspelling of `SELECT`.                       |
| `easy_02_wrong_column`   | References `user_name` where the schema has `username`.|
| `easy_03_string_quotes`  | Unquoted string literal in `WHERE country = USA`.      |
| `easy_04_trailing_comma` | Stray trailing comma in the SELECT column list.        |

### Medium, 8-step budget: logic bugs

| task_id                           | bug                                                         |
|-----------------------------------|-------------------------------------------------------------|
| `medium_01_inner_vs_left_join`    | Uses INNER JOIN where LEFT JOIN is needed, drops rows.      |
| `medium_02_missing_group_by`      | Aggregate without `GROUP BY country`.                       |
| `medium_03_wrong_order_direction` | `ORDER BY amount ASC` instead of `DESC` for a top-5 query.  |
| `medium_04_or_vs_and`             | `OR` instead of `AND` in a conjunctive filter.              |

### Hard, 10-step budget: subtle semantic bugs

| task_id                    | bug                                                               |
|----------------------------|-------------------------------------------------------------------|
| `hard_01_null_equality`    | `manager_id = NULL` instead of `manager_id IS NULL`.              |
| `hard_02_having_vs_where`  | Aggregate filter placed in WHERE, needs to move to HAVING.        |
| `hard_03_window_partition` | Window function partitions by the wrong column.                   |
| `hard_04_date_off_by_one`  | Half-open date range excludes the last day of February 2024.      |

### Expert, 12-step budget: 2-3 compounding bugs

These tasks chain multiple bugs together so an agent has to fix each one
before the grader returns a near-perfect reward. They also use two new
schemas (library and student-grades) so an agent can't memorise column
names from the earlier tiers.

| task_id                          | schema   | compounding bugs                                                                           |
|----------------------------------|----------|--------------------------------------------------------------------------------------------|
| `expert_01_library_multi_bug`    | library  | USA-only filter instead of USA/UK, missing `HAVING COUNT >= 3`, wrong ORDER BY direction.  |
| `expert_02_library_complex_join` | library  | INNER JOIN drops books never checked out, wrong `IS NULL` column, selects id not name.    |
| `expert_03_student_window_agg`   | students | Window `PARTITION BY` wrong column, `rnk > 1` instead of `rnk = 1`, wrong ORDER BY.       |
| `expert_04_student_date_null`    | students | `LIKE '%Fall%'` matches every year, sums `course_id` not `credits`, HAVING uses OR.       |

There are five schemas in total: `employees`/`departments`,
`customers`/`products`/`orders`, `sales`, `authors`/`books`/`checkouts`,
and `students`/`courses`/`enrollments`. Every table has 10 to 45 rows of
seed data, which is enough that the result sets are actually interesting
and partial-credit scores stay informative.

## Reward function

The grader lives in `server/grader.py`. If the query runs successfully, the
reward is a weighted sum of four things:

| Component         | Weight | What it measures                                                        |
|-------------------|--------|-------------------------------------------------------------------------|
| `syntax_valid`    | 0.15   | 1.0 if the query parsed and executed, else 0.0.                         |
| `column_match`    | 0.25   | Jaccard similarity of the actual vs expected column name sets.          |
| `row_count_match` | 0.20   | `min(actual, expected) / max(actual, expected)`.                        |
| `value_match`     | 0.40   | Per-column multiset overlap of cell values.                             |

Each step also returns the full per-component breakdown as a top-level
`grader_breakdown` field on the observation, so an agent can see exactly
which component it lost (columns vs rows vs values) and target the next
attempt accordingly:

```json
{
  "syntax_valid": 1.0,
  "column_match": 1.0,
  "row_count_match": 0.4,
  "value_match": 0.5,
  "raw_score": 0.68,
  "step_penalty_factor": 0.98,
  "error": null
}
```

After that:

- Step penalty: the weighted sum is multiplied by `max(0, 1 - 0.02 * steps_taken)`,
  so dragging out an episode costs a little.
- Error floor: if the query raises a SQLite error, the reward is a flat
  0.05 for that step. The agent still gets a small nudge away from pure
  syntax failures.
- Clamp: every reward is then clamped to the strictly-interior range
  [0.01, 0.99]. Exact 0.0 or 1.0 never shows up, which matches the Phase 2
  validator's requirement. A perfect result-set match still ends the
  episode (we detect it independently of the reward value), it just emits
  0.98 instead of 1.00.

The grader is deterministic. Same inputs, same outputs.

## Episode lifecycle

1. `reset(task_id=None)` picks the requested task, or samples one if you
   don't specify. It builds a fresh in-memory SQLite database, runs the
   gold query to produce `expected_output`, and returns the first
   observation with `done=False` and `reward=0.01`.
2. `step(action)` runs your query against the database, scores it, and
   fills in `query_result`, `is_error`, and `last_action_error`. The
   episode ends when you match the expected result exactly or when
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
     -H "Content-Type: application/json" \
     -d '{"task_id":"easy_01_typo"}'

curl -s -X POST http://localhost:7860/step \
     -H "Content-Type: application/json" \
     -d '{"action":{"query":"SELECT full_name, salary FROM employees WHERE dept_id = 1 ORDER BY salary DESC;"}}'
```

An empty body works too: `curl -X POST .../reset -d '{}'` picks a task for
you.

### Run it in Docker

```bash
docker build -f server/Dockerfile -t sql-debug-env .
docker run --rm -p 7860:7860 sql-debug-env
```

The image stays comfortably inside the 2 vCPU, 8 GB RAM budget. SQLite is
in-process, there's no GPU dependency, and it's a single uvicorn worker.

## Running the baseline agent

`inference.py` lives in the repo root. It talks to a running server over
HTTP and prints the `[START]` / `[STEP]` / `[END]` log lines the grading
harness expects.

```bash
# optional: enables the LLM path. Without it a deterministic heuristic
# fallback takes over so the script still runs for local testing.
export HF_TOKEN=<your token>
export API_BASE_URL=https://router.huggingface.co/v1
export MODEL_NAME=Qwen/Qwen2.5-72B-Instruct

# point at a running server. Defaults to the deployed HF Space, so you
# only need to set this if you're running the env locally.
export ENV_BASE_URL=http://localhost:7860

python inference.py
```

### Baseline scores

The fallback heuristic is a deterministic string-rewrite table. For the
easy / medium / hard tiers it encodes the full fix for each bug pattern,
so the baseline solves them on the first attempt. For the expert tier it
deliberately fixes only ONE of the 2-3 compounding bugs per task, which
leaves the agent in the partial-credit region and shows the difficulty
gap in the table:

| Difficulty | Tasks | Score per task                    | Avg score |
|------------|-------|-----------------------------------|-----------|
| easy       | 4     | 0.98, 0.98, 0.98, 0.98            | 0.980     |
| medium     | 4     | 0.98, 0.98, 0.98, 0.98            | 0.980     |
| hard       | 4     | 0.98, 0.98, 0.98, 0.98            | 0.980     |
| expert     | 4     | 0.666, 0.765, 0.840, 0.749        | 0.755     |
| **overall**| **16**|                                   | **0.924** |

The easy / medium / hard uniform 0.98 is the clamp ceiling (rewards are
bounded above by 0.99, with an additional step penalty of 0.02 per step).
The heuristic already knows each bug so it always returns the fix on
step 1. The expert numbers are meaningful: the agent fixes one bug but
the other two leave columns, rows, and values misaligned, so the grader
reports partial credit that reflects how much of the result set it got
right.

A real LLM agent should do BETTER than the heuristic on expert tasks
(closer to the 0.98 ceiling if it fixes every bug) but may do worse on
individual hard tasks if it misreads the hint. The baseline table is
therefore a lower bound on what a strong LLM should achieve on expert
and a rough upper bound on everything else.

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
    |-- tasks.py                # 12 task definitions
    |-- grader.py               # partial-credit grader
    |-- requirements.txt
    `-- Dockerfile
```
