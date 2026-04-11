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
SQLite schema with realistic row counts (200 to 500 rows per main
table), a broken query, and a short symptom-only hint. It then uses a
five-verb action language to investigate the data, check candidate
fixes against a hidden oracle (limited to 2 uses per episode), and
eventually submit a corrected query for grading.

The environment has **33 tasks**, including a set of five
"data-investigation" tasks where the bug is IN THE DATA rather than in
the SQL. These tasks cannot be solved by reading the query alone - the
agent must use the ``diagnostic`` action to inspect the table
contents and discover the inconsistency.

The agent does NOT see the gold expected output. It reasons about what
the query should produce from the schema, the data, and the hint, then
fixes the query. Several tasks require actual data exploration to
diagnose: a cents-vs-dollars bug only shows up if you look at the row
values, an implicit cross-join only reveals itself if you compare row
counts before and after aggregation, and an empty-string-vs-NULL bug
is invisible without inspecting raw data.

## Why SQL debugging

- Fixing broken SQL is something engineers and analysts do every day.
- The bug library mixes textbook antipatterns with real production bugs
  that don't show up in interview prep: case-inconsistent status
  columns, empty strings vs NULL, cents stored as integers, COUNT
  semantics around NULL, implicit cross joins, and integer division
  in averages.
- The reward signal uses a 5-component grader with difficulty-scaled
  weights (syntax is penalised heavily on easy tasks, values are
  penalised heavily on expert tasks) so tier ceilings actually differ.
- The grader uses result-set equivalence via position-sensitive order
  matching only when the gold query has an outermost `ORDER BY`, so an
  agent that writes a semantically correct query with different SQL
  gets the same score as one that matches byte-for-byte.

## Action space

Every action includes an `action_type` field. The old single-field
format `{"query": "..."}` still works and defaults to
`action_type: "fix"` for backward compatibility.

| `action_type` | Required fields        | What it does                                                                       |
|---------------|------------------------|------------------------------------------------------------------------------------|
| `fix`         | `query`                | Submit a corrected SQL query. This is the only action that gets a real reward.    |
| `check`       | `query`                | Test a query against the hidden expected output. Returns a pass/fail summary only. LIMITED to 2 uses per episode. |
| `describe`    | `table_name`           | Inspect a table via `PRAGMA table_info` + `COUNT(*)`.                              |
| `diagnostic`  | `query` (SELECT only)  | Run a read-only SELECT to investigate the data. Output capped at 50 rows.          |
| `explain`     | `query`                | Run `EXPLAIN QUERY PLAN` and return the plan.                                      |

Every action costs 1 step from the per-task budget. The `check`
action is limited to 2 uses per episode to prevent an agent from
binary-searching the hidden oracle. `fix`, `diagnostic`, and `explain`
all reject write statements (INSERT/UPDATE/DELETE/DROP/etc.) so the
shared per-episode database stays clean.

Info-action rewards are differentiated by average usefulness (all
inside `(0.01, 0.99)`):

| Action               | Reward |
|----------------------|--------|
| `describe`           | 0.03   |
| `diagnostic`         | 0.03   |
| `explain`            | 0.02   |
| `check` (FAIL)       | 0.03   |
| `check` (PASS)       | 0.05   |
| `fix` (graded)       | 0.01 - 0.99 per the grader |

## Observation space

The observation does NOT include an expected-output field. An agent
that wants to know what the query should return has to infer it from
the schema, the hint, and its own `check`/`diagnostic` actions.

| Field               | Type        | Description                                                                  |
|---------------------|-------------|------------------------------------------------------------------------------|
| `done`              | bool        | True when the episode is over.                                               |
| `reward`            | float       | Reward for the latest step, strictly inside (0, 1).                          |
| `task_id`           | str         | Identifier of the current task.                                              |
| `difficulty`        | str         | `"easy"`, `"medium"`, `"hard"`, or `"expert"`.                               |
| `schema_sql`        | str         | Full `CREATE TABLE` + `INSERT` script for this task.                         |
| `buggy_query`       | str         | The original broken query to repair.                                         |
| `hint`              | str         | One-sentence symptom-only hint. Does not describe the fix or the category.  |
| `query_result`      | str         | Result of the most recent `fix` attempt (first 50 rows) or error message.    |
| `check_result`      | str         | Pass/fail summary of the most recent `check` action.                         |
| `diagnostic_result` | str         | Output of the most recent `describe`/`diagnostic`/`explain` action.          |
| `action_type`       | str         | Type of the action that produced this observation.                           |
| `is_error`          | bool        | Whether the last action raised a runtime error.                              |
| `last_action_error` | str \| None | Raw error message for the last action, or `None`.                            |
| `steps_taken`       | int         | Steps used in the current episode.                                           |
| `max_steps`         | int         | Per-task step budget.                                                        |
| `checks_remaining`  | int         | Number of `check` actions still available this episode.                      |
| `grader_breakdown`  | dict        | Per-component grader output after a `fix` action.                            |

## Task catalog

33 tasks across four difficulty tiers, eight schemas, and one
"data-investigation" subset where the bug is hidden in the data
rather than in the SQL.

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

### Hard (max 10 steps): subtle and real-world bugs

| task_id                            | schema    | bug                                                              |
|------------------------------------|-----------|------------------------------------------------------------------|
| `hard_01_null_equality`            | employees | `= NULL` instead of `IS NULL`.                                   |
| `hard_02_having_vs_where`          | orders    | Aggregate filter in WHERE instead of HAVING.                     |
| `hard_03_window_partition`         | sales     | Window function partitions by the wrong column.                  |
| `hard_04_date_off_by_one`          | orders    | Date range excludes the last day of the month.                   |
| `hard_05_count_null_skip`          | hospital  | `COUNT(column)` skips NULLs; should be `COUNT(DISTINCT ...)`.    |
| `hard_06_self_join_double_count`   | projects  | Self-join uses `<>` not `<` and double-counts every pair.        |
| `hard_07_empty_string_null`        | hospital  | Some discharge dates are `''` not `NULL`; `IS NULL` misses them. |
| `hard_08_case_inconsistent_status` | projects  | `status` column has mixed `'active'` / `'Active'` / `'ACTIVE'`.  |
| `hard_09_duplicate_user_product`   | ecommerce | Duplicate `(user, product)` rows inflate `COUNT(user_id)`.       |
| `hard_10_integer_division`         | students  | `SUM/COUNT` with INTEGER columns gives integer-division result.  |

### Expert (max 12 steps): 2-3 compounding bugs

These tasks require the agent to identify and fix multiple bugs. The
buggy query runs without errors but produces wrong output, so the
grader gives partial credit for each bug fixed. Two of the expert
tasks (`expert_09_cents_vs_dollars` and `expert_10_implicit_cross_join`)
are diagnosable ONLY by running `diagnostic` actions against the data.
Reading the schema and the buggy query is not enough.

| task_id                           | schema    | compounding bugs                                                                                 |
|-----------------------------------|-----------|--------------------------------------------------------------------------------------------------|
| `expert_01_library_multi_bug`     | library   | USA-only country filter, missing `HAVING COUNT >= 4`, wrong `ORDER BY` direction.                |
| `expert_02_library_complex_join`  | library   | INNER JOIN drops books never checked out, wrong `IS NULL` column, selects id not name.           |
| `expert_03_student_window_agg`    | students  | Window `PARTITION BY` wrong column, `rnk > 1` not `= 1`, wrong ORDER BY.                          |
| `expert_04_student_date_null`     | students  | `LIKE '%Fall%'` matches every year, sums `course_id` not `credits`, HAVING uses OR.              |
| `expert_05_null_revenue_leak`     | ecommerce | Revenue sums refunded purchases, no COALESCE on NULL totals, WHERE clause drops LEFT JOIN users. |
| `expert_06_window_running_total`  | hospital  | Wrong `PARTITION BY`, reversed window order, extra outcome filter drops ongoing treatments.      |
| `expert_07_top_per_group`         | ecommerce | `ROW_NUMBER()` missing `PARTITION BY`, wrong rank filter, no refunded filter, missing DISTINCT.  |
| `expert_08_cte_progress_tracking` | projects  | Missing billable filter in CTE, missing GROUP BY in milestone CTE, missing status filter.        |
| `expert_09_cents_vs_dollars`      | ecommerce | `amount` column stores cents, not dollars. `WHERE amount > 100` matches almost everything. Only diagnosable by `diagnostic: SELECT amount FROM purchases LIMIT 10`. |
| `expert_10_implicit_cross_join`   | projects  | Comma-join missing join condition creates a cartesian product. Totals are inflated. Only obvious when you compare raw row counts. |

### Data investigation (5 tasks): the bug is in the data, not the query

These 5 tasks present a query that is syntactically and logically
correct. The bug is that the underlying data contains unexpected
values: mixed-case strings, trailing whitespace, cancelled zero-hour
entries, a SYSTEM placeholder user with a negative id, mixed
ISO/unix timestamp formats. An LLM that reads only the query cannot
diagnose any of these - it has to run `diagnostic` actions against
the real data to find the inconsistency.

| task_id                         | schema    | bug in the data                                                                |
|---------------------------------|-----------|--------------------------------------------------------------------------------|
| `data_01_case_mismatch`         | ecommerce | Some users have `country='usa'` or `'Usa'` alongside `'USA'`. Exact-match filter misses them. |
| `data_02_trailing_whitespace`   | hospital  | Some doctors have `specialty='Cardiology '` (trailing space). `WHERE = 'Cardiology'` skips them. |
| `data_03_zero_vs_null`          | projects  | Cancelled time entries have `hours=0.0`. Naive `HAVING COUNT(*) > 0` admits members who did zero real work. |
| `data_04_negative_id`           | ecommerce | A `SYSTEM` placeholder user with `user_id=-1` holds 60 anonymous purchases and tops the "most orders" ranking. |
| `data_05_unix_timestamp`        | ecommerce | ~20 `purchase_time` values are stored as unix timestamp strings (`'1704153600'`). Lexicographic `>= '2024-01-01'` excludes them because `'1' < '2'`. |

### Schemas

Eight schemas with realistic row counts:

| Schema        | Tables                                               | Approx row counts  |
|---------------|------------------------------------------------------|--------------------|
| employees     | departments, employees                               | 5, 220             |
| orders        | customers, products, orders                          | 50, 8, 302         |
| sales         | sales                                                | 300                |
| library       | authors, books, checkouts                            | 200, 500, 400      |
| students      | students, courses, enrollments                       | 200, 20, 800       |
| ecommerce     | users, sessions, purchases (amounts in CENTS)        | 200, 400, 500      |
| hospital      | patients, doctors, treatments, lab_results           | 200, 15, 500, 300  |
| projects      | team_members, projects, time_entries, milestones     | 50, 20, 500, 60    |

The ecommerce schema deliberately stores purchase amounts as INTEGER
cents (1999 = $19.99), the hospital schema has some patients with
`discharge_date = ''` mixed with NULL discharges, and the projects
schema has `status` values in mixed case (`'active'`, `'Active'`,
`'ACTIVE'`). These data-level quirks drive the real-world bug tasks.

## Reward function

Implemented in `server/grader.py`. When a `fix` action runs
successfully, the reward is a weighted sum of five components, with
weights scaled by difficulty tier:

| Component         | Easy | Medium | Hard | Expert | What it measures                                          |
|-------------------|------|--------|------|--------|-----------------------------------------------------------|
| `syntax_valid`    | 0.25 | 0.10   | 0.05 | 0.05   | `1.0` if the query parsed and executed, else `0.0`.       |
| `column_match`    | 0.20 | 0.20   | 0.15 | 0.10   | Jaccard similarity of actual vs expected column name sets.|
| `row_count_match` | 0.15 | 0.15   | 0.15 | 0.10   | `min(actual, expected) / max(actual, expected)`.          |
| `value_match`     | 0.25 | 0.35   | 0.45 | 0.50   | Per-column multiset overlap of cell values.               |
| `order_match`     | 0.15 | 0.20   | 0.20 | 0.25   | Positional row match, only counted when gold has `ORDER BY`. |

Easy tasks weight syntax heavily because the bug IS the syntax.
Expert tasks weight value_match heavily because getting the right
values is what matters most. The grader is implemented with
`collections.Counter` for O(n) per-column comparison so large result
sets (thousands of rows) grade in under 10 ms.

Then:
- **Step penalty**: multiply by `max(0, 1 - 0.02 * steps_taken)`.
- **Error floor**: if the query errors, return `0.05` flat.
- **Clamp**: every reward is clamped to `[0.01, 0.99]` so the Phase 2
  validator's "no exact 0.0 or 1.0" rule is always satisfied.

The grader compares query results, not query text. Multiple valid
SQL solutions get the same score as long as they produce the correct
output.

### grader_breakdown

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
  "weights": {"syntax_valid": 0.05, "column_match": 0.15, ...},
  "error": null
}
```

## Episode lifecycle

1. `reset(task_id=None)` picks the requested task (or samples one),
   builds a fresh in-memory SQLite database ONCE, runs the gold query
   to compute the hidden expected result set, and returns the first
   observation with `done=False` and `reward=0.01`.
2. `step(action)` reuses the persistent per-episode DB (fast: ~0.01
   ms/step on 500-row tables), runs the action, scores it for `fix`
   actions only, and updates the observation. The episode ends when
   a `fix` action produces an exact row-for-row match of the gold
   result set, or when `steps_taken` hits `max_steps`.
3. `state` returns `{task_id, steps_taken, done, last_reward}`.

## Setup

### Run it locally without Docker

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install openenv-core fastapi uvicorn pydantic requests openai
PYTHONPATH=. python -m server.app
```

Quick sanity check:

```bash
curl -s http://localhost:7860/health
# {"status":"healthy"}

curl -s -X POST http://localhost:7860/reset \
     -H "Content-Type: application/json" -d '{}'

# fix action
curl -s -X POST http://localhost:7860/step \
     -H "Content-Type: application/json" \
     -d '{"action":{"action_type":"fix","query":"SELECT full_name, salary FROM employees WHERE dept_id = 1 ORDER BY salary DESC;"}}'

# exploration actions
curl -s -X POST http://localhost:7860/step \
     -H "Content-Type: application/json" \
     -d '{"action":{"action_type":"describe","table_name":"purchases"}}'
curl -s -X POST http://localhost:7860/step \
     -H "Content-Type: application/json" \
     -d '{"action":{"action_type":"diagnostic","query":"SELECT amount FROM purchases LIMIT 10"}}'
```

### Run it in Docker

```bash
docker build -f server/Dockerfile -t sql-debug-env .
docker run --rm -p 7860:7860 sql-debug-env
```

The image stays comfortably inside a 2 vCPU / 8 GB RAM budget.

## Running the baseline agent

`inference.py` lives in the repo root. It talks to a running server
over HTTP and prints the `[START]` / `[STEP]` / `[END]` log lines the
grading harness expects.

```bash
export HF_TOKEN=<your token>
export API_BASE_URL=https://router.huggingface.co/v1
export MODEL_NAME=Qwen/Qwen2.5-72B-Instruct
export ENV_BASE_URL=http://localhost:7860
python inference.py
```

When the LLM path is active, the agent is prompted with a symptom-only
hint and no expected output. It can emit any of the five action types,
is encouraged to explore the schema and data first, and can check a
candidate fix against the hidden oracle (up to 2 times) before
committing it.

### Baseline scores

The heuristic baseline is a deterministic string-replace table. For
the easy / medium / hard tiers it encodes the full fix for each bug.
For the 8 expert tasks (plus the 2 exploration-heavy experts
`expert_09` and `expert_10` which get no heuristic entry at all) it
fixes at most 1 of the 2-3 compounding bugs. The resulting spread
from the live Space:

| Difficulty        | Tasks | Score per task                                                                          | Avg    |
|-------------------|-------|------------------------------------------------------------------------------------------|--------|
| easy              | 4     | 0.98 / 0.98 / 0.98 / 0.98                                                               | 0.980  |
| medium            | 4     | 0.98 / 0.98 / 0.98 / 0.98                                                               | 0.980  |
| hard              | 10    | 0.98 x 10                                                                                | 0.980  |
| expert            | 10    | 0.500 / 0.545 / 0.617 / 0.634 / 0.549 / 0.441 / 0.150 / 0.354 / 0.921 / 0.490           | 0.524  |
| data-investigation| 5     | 0.256 / 0.325 / 0.743 / 0.735 / 0.632                                                   | 0.538  |
| **overall**       | **33**|                                                                                          | **0.774** |

The 5 data-investigation tasks score 0.26-0.74 because the heuristic
has NO entries for them - the buggy query is submitted verbatim, and
the grader reports the partial overlap between buggy and correct
result sets. `data_01_case_mismatch` scores the lowest at 0.256
because the lowercase-only country filter catches just 7 of 69 users,
leaving most of the result set wrong. `data_03_zero_vs_null` and
`data_04_negative_id` score higher (0.74 / 0.73) because the
pollution is small (4-1 extra rows on large outputs).

A real LLM agent using the `diagnostic` action on these tasks should
substantially beat these numbers. An LLM that skips diagnostic and
submits the obvious "correct" fix without checking the data cannot
improve on the heuristic - because there IS no obvious fix.

The expert scores span a wide range. `expert_09_cents_vs_dollars`
lands at **0.150** because the heuristic doesn't know the `amount`
column stores cents - it can only learn that from a `diagnostic`
action. `expert_06_window_running_total` lands at **0.921** because
the heuristic's single `PARTITION BY` fix is enough to recover most
of the result. This distribution is the point: tasks that REQUIRE
exploration to solve score much lower than tasks where the fix can
be pattern-matched from the schema alone.

The heuristic numbers are an upper bound on easy/medium/hard because
the heuristic is a cheat sheet. For expert tasks, a real LLM using
the exploration actions should generally beat these numbers - especially
on `expert_09` and `expert_10`, where the heuristic has no answer at
all. An LLM that checks `SELECT amount FROM purchases LIMIT 10` and
notices the values are in cents can easily fix `expert_09`.

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
|-- utils.py                    # shared flatten_response helper
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
    |-- tasks.py                # 28 task definitions
    |-- grader.py               # 5-component partial-credit grader
    |-- requirements.txt
    `-- Dockerfile
```
