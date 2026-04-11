"""Task definitions for the SQL Debug Environment.

Each task bundles a fresh schema (CREATE TABLE + INSERT rows), a buggy query,
the gold-standard corrected query, a natural-language hint about the bug and a
step budget. The corrected query is executed at reset() time to produce the
``expected_output`` string that the agent sees.

Difficulty tiers:
    easy   — surface-level syntax / typo / operator bugs (max 5 steps)
    medium — logic errors in joins, grouping, filtering (max 8 steps)
    hard   — subtle semantic bugs (NULL, windows, HAVING vs WHERE) (max 10 steps)
"""
from __future__ import annotations

from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Reusable schema fragments. Each task gets its own fresh :memory: database,
# so name collisions across tasks are fine — we isolate per-task below.
# ---------------------------------------------------------------------------

EMPLOYEES_SCHEMA = """
CREATE TABLE departments (
    dept_id   INTEGER PRIMARY KEY,
    dept_name TEXT NOT NULL
);
CREATE TABLE employees (
    emp_id     INTEGER PRIMARY KEY,
    username   TEXT NOT NULL,
    full_name  TEXT NOT NULL,
    dept_id    INTEGER,
    salary     REAL,
    hired_on   TEXT,
    manager_id INTEGER,
    FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
);
INSERT INTO departments VALUES
    (1, 'Engineering'),
    (2, 'Sales'),
    (3, 'Marketing'),
    (4, 'Finance'),
    (5, 'Research');
INSERT INTO employees VALUES
    (1,  'alice',    'Alice Chen',     1, 120000, '2019-03-15', NULL),
    (2,  'bob',      'Bob Martinez',   1,  95000, '2020-06-01', 1),
    (3,  'carol',    'Carol Singh',    2,  85000, '2018-11-20', NULL),
    (4,  'dave',     'Dave Patel',     2,  72000, '2021-02-10', 3),
    (5,  'eve',      'Eve Johnson',    3,  68000, '2022-01-05', NULL),
    (6,  'frank',    'Frank Liu',      1, 110000, '2017-09-12', 1),
    (7,  'grace',    'Grace Kim',      4, 130000, '2016-04-22', NULL),
    (8,  'henry',    'Henry Nguyen',   2,  78000, '2023-03-18', 3),
    (9,  'ivy',      'Ivy Brown',      1,  99000, '2021-07-30', 1),
    (10, 'jack',     'Jack Wilson',    3,  71000, '2020-10-14', 5),
    (11, 'kara',     'Kara Davis',     4, 115000, '2019-12-03', 7),
    (12, 'leo',      'Leo Santos',     1, 105000, '2018-08-27', 1),
    (13, 'mia',      'Mia Anderson',   5,  88000, '2022-05-19', NULL),
    (14, 'noah',     'Noah Thompson',  5,  92000, '2021-11-08', 13),
    (15, 'olivia',   'Olivia Garcia',  NULL, 60000, '2023-09-01', NULL);
"""

ORDERS_SCHEMA = """
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    country     TEXT NOT NULL,
    signup_date TEXT
);
CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL,
    price      REAL NOT NULL
);
CREATE TABLE orders (
    order_id    INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    product_id  INTEGER NOT NULL,
    quantity    INTEGER NOT NULL,
    order_date  TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
    FOREIGN KEY (product_id)  REFERENCES products(product_id)
);
INSERT INTO customers VALUES
    (1, 'Acme Corp',    'USA',     '2022-01-10'),
    (2, 'Globex',       'Germany', '2022-03-22'),
    (3, 'Initech',      'USA',     '2021-11-05'),
    (4, 'Umbrella',     'UK',      '2023-02-18'),
    (5, 'Hooli',        'USA',     '2022-07-30'),
    (6, 'Pied Piper',   'Canada',  '2023-05-14'),
    (7, 'Wayne Ent',    'USA',     '2020-09-01'),
    (8, 'Stark Ind',    'USA',     '2021-04-12'),
    (9, 'Cyberdyne',    'Japan',   '2023-08-20'),
    (10,'Tyrell',       'UK',      '2022-12-03');
INSERT INTO products VALUES
    (1, 'Widget',  'Hardware', 19.99),
    (2, 'Gadget',  'Hardware', 49.99),
    (3, 'Gizmo',   'Hardware',  9.99),
    (4, 'License', 'Software',199.00),
    (5, 'Support', 'Service',  99.00),
    (6, 'Training','Service', 149.00);
INSERT INTO orders VALUES
    (1,  1, 1, 10, '2024-01-05'),
    (2,  1, 4,  2, '2024-01-12'),
    (3,  2, 2,  5, '2024-01-18'),
    (4,  3, 1, 20, '2024-02-01'),
    (5,  3, 5,  1, '2024-02-03'),
    (6,  4, 3, 15, '2024-02-10'),
    (7,  5, 4,  3, '2024-02-15'),
    (8,  5, 6,  1, '2024-02-22'),
    (9,  1, 2,  4, '2024-03-01'),
    (10, 7, 1, 50, '2024-03-08'),
    (11, 8, 4,  5, '2024-03-14'),
    (12, 8, 5,  2, '2024-03-19'),
    (13, 2, 3, 30, '2024-03-25'),
    (14, 9, 2,  7, '2024-04-02'),
    (15, 10,6,  2, '2024-04-11'),
    (16, 7, 4,  1, '2024-04-18'),
    (17, 3, 2,  8, '2024-04-25'),
    (18, 5, 1, 12, '2024-05-03'),
    (19, 1, 3,  2, '2024-02-29');
"""

SALES_SCHEMA = """
CREATE TABLE sales (
    sale_id    INTEGER PRIMARY KEY,
    region     TEXT NOT NULL,
    rep_name   TEXT NOT NULL,
    amount     REAL NOT NULL,
    sale_date  TEXT NOT NULL,
    commission REAL
);
INSERT INTO sales VALUES
    (1,  'North', 'Alice',   15000, '2024-01-15', 1500),
    (2,  'North', 'Alice',   22000, '2024-02-20', 2200),
    (3,  'South', 'Bob',     18000, '2024-01-10', 1800),
    (4,  'South', 'Bob',     12000, '2024-03-05', NULL),
    (5,  'East',  'Carol',   31000, '2024-02-14', 3100),
    (6,  'East',  'Carol',   27000, '2024-03-22', 2700),
    (7,  'West',  'Dave',    19000, '2024-01-28', 1900),
    (8,  'West',  'Dave',    24000, '2024-02-18', NULL),
    (9,  'North', 'Eve',     16000, '2024-03-12', 1600),
    (10, 'South', 'Frank',   21000, '2024-02-05', 2100),
    (11, 'East',  'Carol',   29000, '2024-04-01', 2900),
    (12, 'West',  'Dave',    17000, '2024-04-15', 1700);
"""


# ---------------------------------------------------------------------------
# Task list. Keep this ordered easy -> medium -> hard so iterating in order
# produces a natural difficulty curve for the baseline agent.
# ---------------------------------------------------------------------------

TASKS: List[Dict[str, Any]] = [
    # ------------------------------- EASY -------------------------------
    {
        "task_id": "easy_01_typo",
        "difficulty": "easy",
        "schema_sql": EMPLOYEES_SCHEMA,
        "buggy_query": "SELCT full_name, salary FROM employees WHERE dept_id = 1 ORDER BY salary DESC;",
        "correct_query": "SELECT full_name, salary FROM employees WHERE dept_id = 1 ORDER BY salary DESC;",
        "hint": "There is a typo in the SQL keyword at the very start of the query.",
        "max_steps": 5,
    },
    {
        "task_id": "easy_02_wrong_column",
        "difficulty": "easy",
        "schema_sql": EMPLOYEES_SCHEMA,
        "buggy_query": "SELECT user_name, full_name FROM employees WHERE dept_id = 2;",
        "correct_query": "SELECT username, full_name FROM employees WHERE dept_id = 2;",
        "hint": "The column name for the login handle is slightly different from what the query uses — check the schema.",
        "max_steps": 5,
    },
    {
        "task_id": "easy_03_string_quotes",
        "difficulty": "easy",
        "schema_sql": ORDERS_SCHEMA,
        "buggy_query": "SELECT name, country FROM customers WHERE country = USA;",
        "correct_query": "SELECT name, country FROM customers WHERE country = 'USA';",
        "hint": "String literals in SQL must be wrapped in single quotes.",
        "max_steps": 5,
    },
    {
        "task_id": "easy_04_trailing_comma",
        "difficulty": "easy",
        "schema_sql": SALES_SCHEMA,
        "buggy_query": "SELECT rep_name, amount, FROM sales WHERE region = 'East';",
        "correct_query": "SELECT rep_name, amount FROM sales WHERE region = 'East';",
        "hint": "There is a stray trailing comma in the SELECT clause right before FROM — SQL does not allow a dangling comma in a column list.",
        "max_steps": 5,
    },

    # ------------------------------ MEDIUM ------------------------------
    {
        "task_id": "medium_01_inner_vs_left_join",
        "difficulty": "medium",
        "schema_sql": EMPLOYEES_SCHEMA,
        "buggy_query": (
            "SELECT e.full_name, d.dept_name "
            "FROM employees e "
            "INNER JOIN departments d ON e.dept_id = d.dept_id "
            "ORDER BY e.emp_id;"
        ),
        "correct_query": (
            "SELECT e.full_name, d.dept_name "
            "FROM employees e "
            "LEFT JOIN departments d ON e.dept_id = d.dept_id "
            "ORDER BY e.emp_id;"
        ),
        "hint": "The result should list EVERY employee, including those with no department assigned. The current join drops such rows.",
        "max_steps": 8,
    },
    {
        "task_id": "medium_02_missing_group_by",
        "difficulty": "medium",
        "schema_sql": ORDERS_SCHEMA,
        "buggy_query": "SELECT country, COUNT(*) AS num_customers FROM customers;",
        "correct_query": "SELECT country, COUNT(*) AS num_customers FROM customers GROUP BY country ORDER BY country;",
        "hint": "You want one row per country with the count of customers in that country. An aggregate without a grouping collapses everything into a single row.",
        "max_steps": 8,
    },
    {
        "task_id": "medium_03_wrong_order_direction",
        "difficulty": "medium",
        "schema_sql": SALES_SCHEMA,
        "buggy_query": "SELECT rep_name, amount FROM sales ORDER BY amount ASC LIMIT 5;",
        "correct_query": "SELECT rep_name, amount FROM sales ORDER BY amount DESC LIMIT 5;",
        "hint": "The task is to return the TOP 5 sales by amount (largest first).",
        "max_steps": 8,
    },
    {
        "task_id": "medium_04_or_vs_and",
        "difficulty": "medium",
        "schema_sql": EMPLOYEES_SCHEMA,
        "buggy_query": "SELECT full_name, salary, dept_id FROM employees WHERE dept_id = 1 OR salary > 100000;",
        "correct_query": "SELECT full_name, salary, dept_id FROM employees WHERE dept_id = 1 AND salary > 100000;",
        "hint": "We want Engineering (dept_id = 1) employees who ALSO earn more than 100k — both conditions must hold.",
        "max_steps": 8,
    },

    # ------------------------------- HARD -------------------------------
    {
        "task_id": "hard_01_null_equality",
        "difficulty": "hard",
        "schema_sql": EMPLOYEES_SCHEMA,
        "buggy_query": "SELECT full_name FROM employees WHERE manager_id = NULL ORDER BY emp_id;",
        "correct_query": "SELECT full_name FROM employees WHERE manager_id IS NULL ORDER BY emp_id;",
        "hint": "In SQL, '= NULL' never matches anything — NULL comparisons need a dedicated operator.",
        "max_steps": 10,
    },
    {
        "task_id": "hard_02_having_vs_where",
        "difficulty": "hard",
        "schema_sql": ORDERS_SCHEMA,
        "buggy_query": (
            "SELECT customer_id, SUM(quantity) AS total_qty "
            "FROM orders "
            "WHERE SUM(quantity) > 20 "
            "GROUP BY customer_id;"
        ),
        "correct_query": (
            "SELECT customer_id, SUM(quantity) AS total_qty "
            "FROM orders "
            "GROUP BY customer_id "
            "HAVING SUM(quantity) > 20 "
            "ORDER BY customer_id;"
        ),
        "hint": "Filtering on an aggregate cannot happen in WHERE — WHERE runs before grouping. Use the clause that runs after.",
        "max_steps": 10,
    },
    {
        "task_id": "hard_03_window_partition",
        "difficulty": "hard",
        "schema_sql": SALES_SCHEMA,
        "buggy_query": (
            "SELECT rep_name, region, amount, "
            "       RANK() OVER (PARTITION BY rep_name ORDER BY amount DESC) AS region_rank "
            "FROM sales "
            "ORDER BY region, region_rank;"
        ),
        "correct_query": (
            "SELECT rep_name, region, amount, "
            "       RANK() OVER (PARTITION BY region ORDER BY amount DESC) AS region_rank "
            "FROM sales "
            "ORDER BY region, region_rank;"
        ),
        "hint": "The column is named region_rank — the window should partition by region, not by rep_name.",
        "max_steps": 10,
    },
    {
        "task_id": "hard_04_date_off_by_one",
        "difficulty": "hard",
        "schema_sql": ORDERS_SCHEMA,
        "buggy_query": (
            "SELECT order_id, order_date "
            "FROM orders "
            "WHERE order_date >= '2024-02-01' AND order_date < '2024-02-28' "
            "ORDER BY order_date;"
        ),
        "correct_query": (
            "SELECT order_id, order_date "
            "FROM orders "
            "WHERE order_date >= '2024-02-01' AND order_date <= '2024-02-29' "
            "ORDER BY order_date;"
        ),
        "hint": "The intent is 'all orders in February 2024 inclusive'. February 2024 has 29 days (leap year) and the upper bound needs to include the final day.",
        "max_steps": 10,
    },
]


def get_task(task_id: str) -> Dict[str, Any]:
    for task in TASKS:
        if task["task_id"] == task_id:
            return task
    raise KeyError(f"Unknown task_id: {task_id}")


def list_task_ids() -> List[str]:
    return [t["task_id"] for t in TASKS]
