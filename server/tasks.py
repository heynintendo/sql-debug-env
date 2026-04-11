"""Task definitions for the SQL Debug Environment.

Each task bundles a fresh schema (CREATE TABLE + INSERT rows), a buggy query,
the gold-standard corrected query, a natural-language hint about the bug and a
step budget. The corrected query is executed at reset() time to produce the
``expected_output`` string that the agent sees.

Difficulty tiers:
    easy   - surface-level syntax / typo / operator bugs (max 5 steps)
    medium - logic errors in joins, grouping, filtering (max 8 steps)
    hard   - subtle semantic bugs (NULL, windows, HAVING vs WHERE) (max 10 steps)
    expert - 2-3 compounding bugs that must be fixed simultaneously (max 12 steps)

Hints are deliberately vague: they point at a symptom, not a fix. The earlier
task catalogue gave away the answer in the hint ("SELCT is a typo"), which
meant a frontier LLM could solve every task from the hint alone. The current
hints describe what the agent should observe and force it to look at the
schema and the diff between actual and expected output.
"""
from __future__ import annotations

from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Reusable schema fragments. Each task gets its own fresh :memory: database,
# so name collisions across tasks are fine - we isolate per-task below.
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
# Expert-tier schemas. New domain shapes, separate from the easy/medium/hard
# tables, to address the "only 3 schemas" feedback from the evaluator.
# ---------------------------------------------------------------------------

LIBRARY_SCHEMA = """
CREATE TABLE authors (
    author_id    INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    country      TEXT,
    birth_year   INTEGER
);
CREATE TABLE books (
    book_id        INTEGER PRIMARY KEY,
    title          TEXT NOT NULL,
    author_id      INTEGER REFERENCES authors(author_id),
    genre          TEXT,
    published_year INTEGER,
    price          REAL
);
CREATE TABLE checkouts (
    checkout_id   INTEGER PRIMARY KEY,
    book_id       INTEGER REFERENCES books(book_id),
    member_name   TEXT NOT NULL,
    checkout_date TEXT,
    return_date   TEXT
);
INSERT INTO authors VALUES
    (1,  'Jane Austen',            'UK',        1775),
    (2,  'Mark Twain',              'USA',       1835),
    (3,  'Haruki Murakami',         'Japan',     1949),
    (4,  'Chimamanda Adichie',      'Nigeria',   1977),
    (5,  'Ursula K Le Guin',        'USA',       1929),
    (6,  'George Orwell',           'UK',        1903),
    (7,  'Gabriel Garcia Marquez',  'Colombia',  1927),
    (8,  'Toni Morrison',           'USA',       1931),
    (9,  'Franz Kafka',             'Germany',   1883),
    (10, 'Virginia Woolf',          'UK',        1882),
    (11, 'Isaac Asimov',            'USA',       1920),
    (12, 'Margaret Atwood',         'Canada',    1939),
    (13, 'Neil Gaiman',             'UK',        1960),
    (14, 'N K Jemisin',             'USA',       1972),
    (15, 'Jorge Luis Borges',       'Argentina', 1899);
INSERT INTO books VALUES
    (1,  'Pride and Prejudice',      1,  'Romance',    1813, 12.99),
    (2,  'Emma',                     1,  'Romance',    1815, 10.99),
    (3,  'Sense and Sensibility',    1,  'Romance',    1811, 11.50),
    (4,  'Huckleberry Finn',         2,  'Adventure',  1884, 14.00),
    (5,  'Tom Sawyer',               2,  'Adventure',  1876, 13.50),
    (6,  'Norwegian Wood',           3,  'Fiction',    1987, 16.99),
    (7,  'Kafka on the Shore',       3,  'Fiction',    2002, 18.50),
    (8,  '1Q84',                     3,  'Fiction',    2009, 22.00),
    (9,  'Americanah',               4,  'Fiction',    2013, 17.00),
    (10, 'The Left Hand of Darkness',5,  'SciFi',      1969, 15.50),
    (11, 'A Wizard of Earthsea',     5,  'Fantasy',    1968, 13.99),
    (12, 'The Dispossessed',         5,  'SciFi',      1974, 14.50),
    (13, '1984',                     6,  'Dystopian',  1949, 15.99),
    (14, 'Animal Farm',              6,  'Dystopian',  1945,  9.99),
    (15, 'One Hundred Years',        7,  'Fiction',    1967, 19.99),
    (16, 'Beloved',                  8,  'Fiction',    1987, 16.50),
    (17, 'The Trial',                9,  'Fiction',    1925, 12.50),
    (18, 'Mrs Dalloway',             10, 'Fiction',    1925, 11.99),
    (19, 'Foundation',               11, 'SciFi',      1951, 13.00),
    (20, 'I Robot',                  11, 'SciFi',      1950, 12.50),
    (21, 'The Handmaids Tale',       12, 'Dystopian',  1985, 16.00),
    (22, 'Oryx and Crake',           12, 'SciFi',      2003, 15.50),
    (23, 'American Gods',            13, 'Fantasy',    2001, 17.50),
    (24, 'The Fifth Season',         14, 'SciFi',      2015, 18.00),
    (25, 'Ficciones',                15, 'Fiction',    1944, 14.00);
INSERT INTO checkouts VALUES
    (1,  1,  'Alice',  '2024-01-05', '2024-01-19'),
    (2,  4,  'Bob',    '2024-01-07', '2024-01-21'),
    (3,  7,  'Carol',  '2024-01-10', NULL),
    (4,  13, 'Dave',   '2024-01-12', '2024-01-26'),
    (5,  15, 'Eve',    '2024-01-15', '2024-01-29'),
    (6,  1,  'Frank',  '2024-02-01', '2024-02-15'),
    (7,  6,  'Grace',  '2024-02-03', '2024-02-17'),
    (8,  19, 'Henry',  '2024-02-05', NULL),
    (9,  13, 'Ivy',    '2024-02-10', '2024-02-24'),
    (10, 8,  'Jack',   '2024-02-14', '2024-02-28'),
    (11, 2,  'Kara',   '2024-02-18', '2024-03-03'),
    (12, 21, 'Leo',    '2024-02-20', '2024-03-05'),
    (13, 10, 'Mia',    '2024-03-01', '2024-03-15'),
    (14, 13, 'Noah',   '2024-03-05', '2024-03-19'),
    (15, 6,  'Olivia', '2024-03-08', NULL),
    (16, 1,  'Pam',    '2024-03-10', '2024-03-24'),
    (17, 16, 'Quinn',  '2024-03-14', '2024-03-28'),
    (18, 25, 'Ray',    '2024-03-18', '2024-04-01'),
    (19, 11, 'Sam',    '2024-03-22', '2024-04-05'),
    (20, 7,  'Tina',   '2024-04-01', '2024-04-15'),
    (21, 4,  'Uma',    '2024-04-05', NULL),
    (22, 15, 'Vic',    '2024-04-08', '2024-04-22'),
    (23, 2,  'Wade',   '2024-04-10', '2024-04-24'),
    (24, 13, 'Xena',   '2024-04-14', NULL),
    (25, 9,  'Yuki',   '2024-04-18', '2024-05-02'),
    (26, 23, 'Zane',   '2024-04-22', '2024-05-06'),
    (27, 3,  'Amy',    '2024-04-25', '2024-05-09'),
    (28, 1,  'Ben',    '2024-05-01', '2024-05-15'),
    (29, 6,  'Cal',    '2024-05-05', NULL),
    (30, 13, 'Deb',    '2024-05-08', '2024-05-22');
"""

STUDENTS_SCHEMA = """
CREATE TABLE students (
    student_id      INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    major           TEXT,
    enrollment_year INTEGER
);
CREATE TABLE courses (
    course_id   INTEGER PRIMARY KEY,
    course_name TEXT NOT NULL,
    department  TEXT,
    credits     INTEGER
);
CREATE TABLE enrollments (
    enrollment_id INTEGER PRIMARY KEY,
    student_id    INTEGER REFERENCES students(student_id),
    course_id     INTEGER REFERENCES courses(course_id),
    semester      TEXT,
    grade         REAL
);
INSERT INTO students VALUES
    (1,  'Alice Chen',      'Computer Science', 2022),
    (2,  'Bob Martinez',    'Mathematics',      2021),
    (3,  'Carol Singh',     'Computer Science', 2023),
    (4,  'Dave Patel',      'Physics',          2022),
    (5,  'Eve Johnson',     'Mathematics',      2024),
    (6,  'Frank Liu',       'Computer Science', 2021),
    (7,  'Grace Kim',       'Biology',          2023),
    (8,  'Henry Nguyen',    'Physics',          2022),
    (9,  'Ivy Brown',       'Biology',          2024),
    (10, 'Jack Wilson',     'Computer Science', 2022),
    (11, 'Kara Davis',      'Mathematics',      2023),
    (12, 'Leo Santos',      'Biology',          2021),
    (13, 'Mia Anderson',    'Physics',          2024),
    (14, 'Noah Thompson',   'Computer Science', 2023),
    (15, 'Olivia Garcia',   NULL,               2024);
INSERT INTO courses VALUES
    (1,  'Intro to Programming',  'Computer Science', 3),
    (2,  'Data Structures',       'Computer Science', 4),
    (3,  'Algorithms',            'Computer Science', 4),
    (4,  'Linear Algebra',        'Mathematics',      3),
    (5,  'Calculus I',            'Mathematics',      4),
    (6,  'Quantum Mechanics',     'Physics',          4),
    (7,  'Thermodynamics',        'Physics',          3),
    (8,  'Genetics',              'Biology',          4),
    (9,  'Cell Biology',          'Biology',          3),
    (10, 'Statistics',            'Mathematics',      3);
INSERT INTO enrollments VALUES
    (1,  1, 1,  'Fall 2023',   3.8),
    (2,  1, 2,  'Fall 2024',   3.9),
    (3,  1, 3,  'Fall 2024',   4.0),
    (4,  1, 4,  'Fall 2024',   3.7),
    (5,  1, 10, 'Fall 2024',   3.5),
    (6,  2, 4,  'Fall 2023',   3.5),
    (7,  2, 5,  'Spring 2024', 3.7),
    (8,  2, 10, 'Fall 2024',   3.6),
    (9,  2, 1,  'Fall 2024',   3.4),
    (10, 3, 1,  'Fall 2024',   3.6),
    (11, 3, 2,  'Fall 2024',   3.8),
    (12, 3, 3,  'Fall 2024',   3.7),
    (13, 3, 4,  'Fall 2024',   3.9),
    (14, 3, 5,  'Fall 2024',   3.5),
    (15, 4, 6,  'Fall 2023',   3.2),
    (16, 4, 7,  'Spring 2024', 3.4),
    (17, 4, 4,  'Fall 2024',   3.6),
    (18, 5, 5,  'Fall 2024',   4.0),
    (19, 5, 10, 'Fall 2024',   3.9),
    (20, 5, 4,  'Fall 2024',   3.8),
    (21, 5, 1,  'Fall 2024',   3.5),
    (22, 6, 1,  'Fall 2022',   3.3),
    (23, 6, 2,  'Spring 2023', 3.5),
    (24, 6, 3,  'Fall 2023',   3.6),
    (25, 6, 10, 'Spring 2024', 3.4),
    (26, 7, 8,  'Fall 2023',   3.7),
    (27, 7, 9,  'Spring 2024', 3.8),
    (28, 7, 8,  'Fall 2024',   3.9),
    (29, 8, 6,  'Fall 2023',   3.5),
    (30, 8, 7,  'Spring 2024', 3.3),
    (31, 9, 8,  'Fall 2024',   4.0),
    (32, 9, 9,  'Fall 2024',   3.9),
    (33, 10,1,  'Fall 2022',   3.0),
    (34, 10,2,  'Spring 2023', 3.2),
    (35, 10,3,  'Fall 2023',   3.4),
    (36, 11,4,  'Fall 2023',   3.8),
    (37, 11,5,  'Spring 2024', 3.9),
    (38, 12,8,  'Fall 2023',   3.6),
    (39, 12,9,  'Spring 2024', 3.7),
    (40, 13,6,  'Fall 2024',   3.8),
    (41, 13,7,  'Fall 2024',   3.5),
    (42, 14,1,  'Fall 2024',   3.3),
    (43, 14,2,  'Fall 2024',   3.5),
    (44, 14,3,  'Fall 2024',   3.6),
    (45, 14,10, 'Fall 2024',   3.4);
"""


# ---------------------------------------------------------------------------
# Task list. Ordered easy -> medium -> hard -> expert so iterating in order
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
        "hint": "There's a syntax error in this query. Look carefully at the SQL keywords.",
        "max_steps": 5,
    },
    {
        "task_id": "easy_02_wrong_column",
        "difficulty": "easy",
        "schema_sql": EMPLOYEES_SCHEMA,
        "buggy_query": "SELECT user_name, full_name FROM employees WHERE dept_id = 2;",
        "correct_query": "SELECT username, full_name FROM employees WHERE dept_id = 2;",
        "hint": "The query references a column that doesn't exist in the schema.",
        "max_steps": 5,
    },
    {
        "task_id": "easy_03_string_quotes",
        "difficulty": "easy",
        "schema_sql": ORDERS_SCHEMA,
        "buggy_query": "SELECT name, country FROM customers WHERE country = USA;",
        "correct_query": "SELECT name, country FROM customers WHERE country = 'USA';",
        "hint": "There's a syntax issue with how a literal value is used in the WHERE clause.",
        "max_steps": 5,
    },
    {
        "task_id": "easy_04_trailing_comma",
        "difficulty": "easy",
        "schema_sql": SALES_SCHEMA,
        "buggy_query": "SELECT rep_name, amount, FROM sales WHERE region = 'East';",
        "correct_query": "SELECT rep_name, amount FROM sales WHERE region = 'East';",
        "hint": "There's a small syntax issue in the SELECT column list.",
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
        "hint": "The query returns fewer rows than expected. Some records are being dropped.",
        "max_steps": 8,
    },
    {
        # FIX 3: ORDER BY moved into the buggy query too, so the ONLY
        # difference between buggy and correct is the GROUP BY. The grader
        # no longer penalises an agent for not replicating an ordering
        # clause that isn't related to the documented bug.
        "task_id": "medium_02_missing_group_by",
        "difficulty": "medium",
        "schema_sql": ORDERS_SCHEMA,
        "buggy_query": "SELECT country, COUNT(*) AS num_customers FROM customers ORDER BY country;",
        "correct_query": "SELECT country, COUNT(*) AS num_customers FROM customers GROUP BY country ORDER BY country;",
        "hint": "This query should aggregate data by a grouping column, but the results are wrong.",
        "max_steps": 8,
    },
    {
        "task_id": "medium_03_wrong_order_direction",
        "difficulty": "medium",
        "schema_sql": SALES_SCHEMA,
        "buggy_query": "SELECT rep_name, amount FROM sales ORDER BY amount ASC LIMIT 5;",
        "correct_query": "SELECT rep_name, amount FROM sales ORDER BY amount DESC LIMIT 5;",
        "hint": "The query is supposed to show the top values, but the ordering seems off.",
        "max_steps": 8,
    },
    {
        "task_id": "medium_04_or_vs_and",
        "difficulty": "medium",
        "schema_sql": EMPLOYEES_SCHEMA,
        "buggy_query": "SELECT full_name, salary, dept_id FROM employees WHERE dept_id = 1 OR salary > 100000;",
        "correct_query": "SELECT full_name, salary, dept_id FROM employees WHERE dept_id = 1 AND salary > 100000;",
        "hint": "The WHERE clause is matching too many rows. The filter conditions aren't combining correctly.",
        "max_steps": 8,
    },

    # ------------------------------- HARD -------------------------------
    {
        "task_id": "hard_01_null_equality",
        "difficulty": "hard",
        "schema_sql": EMPLOYEES_SCHEMA,
        "buggy_query": "SELECT full_name FROM employees WHERE manager_id = NULL ORDER BY emp_id;",
        "correct_query": "SELECT full_name FROM employees WHERE manager_id IS NULL ORDER BY emp_id;",
        "hint": "Some rows that should appear in the results are missing. Think about how the database handles missing values.",
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
        "hint": "The query fails to execute. The filtering logic is in the wrong place for what it's trying to do.",
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
        "hint": "The calculated values are wrong. The grouping in the analytical calculation doesn't match what's needed.",
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
        "hint": "The query is missing one day of data at the boundary of the date range.",
        "max_steps": 10,
    },

    # ------------------------------ EXPERT ------------------------------
    # 2-3 compounding bugs per task. The buggy query runs to completion but
    # produces wrong output, so the grader gives partial credit and an agent
    # needs to identify AND fix all bugs to converge on the gold result.
    {
        "task_id": "expert_01_library_multi_bug",
        "difficulty": "expert",
        "schema_sql": LIBRARY_SCHEMA,
        # Bugs:
        #   (1) missing HAVING COUNT(*) >= 3 filter - returns every USA/UK author
        #   (2) country filter uses = 'USA' only, drops all UK authors
        #   (3) ORDER BY is ASC instead of DESC
        "buggy_query": (
            "SELECT a.name, AVG(b.price) AS avg_price "
            "FROM authors a "
            "JOIN books b ON a.author_id = b.author_id "
            "WHERE a.country = 'USA' "
            "GROUP BY a.name "
            "ORDER BY avg_price ASC;"
        ),
        "correct_query": (
            "SELECT a.name, AVG(b.price) AS avg_price "
            "FROM authors a "
            "JOIN books b ON a.author_id = b.author_id "
            "WHERE a.country IN ('USA', 'UK') "
            "GROUP BY a.name "
            "HAVING COUNT(b.book_id) >= 3 "
            "ORDER BY avg_price DESC;"
        ),
        "hint": (
            "The query is looking for authors who meet multiple conditions and should return "
            "them in a specific order. The current result is both incomplete and in the wrong "
            "order, and contains rows that shouldn't be there."
        ),
        "max_steps": 12,
    },
    {
        "task_id": "expert_02_library_complex_join",
        "difficulty": "expert",
        "schema_sql": LIBRARY_SCHEMA,
        # Bugs:
        #   (1) INNER JOIN on checkouts drops books that were never checked out
        #   (2) filter uses return_date IS NULL (currently checked out) instead of
        #       checkout_id IS NULL (never checked out)
        #   (3) selects b.author_id instead of joining to get a.name
        "buggy_query": (
            "SELECT b.title, b.author_id AS author_name, b.genre "
            "FROM books b "
            "INNER JOIN authors a ON b.author_id = a.author_id "
            "INNER JOIN checkouts c ON b.book_id = c.book_id "
            "WHERE c.return_date IS NULL "
            "ORDER BY b.title;"
        ),
        "correct_query": (
            "SELECT b.title, a.name AS author_name, b.genre "
            "FROM books b "
            "LEFT JOIN authors a ON b.author_id = a.author_id "
            "LEFT JOIN checkouts c ON b.book_id = c.book_id "
            "WHERE c.checkout_id IS NULL "
            "ORDER BY b.title;"
        ),
        "hint": (
            "The goal is to find rows from the books table that have no matching record in "
            "the checkouts table. The current query returns the opposite set and has the "
            "author column showing the wrong value."
        ),
        "max_steps": 12,
    },
    {
        "task_id": "expert_03_student_window_agg",
        "difficulty": "expert",
        "schema_sql": STUDENTS_SCHEMA,
        # Bugs:
        #   (1) Window PARTITION BY s.student_id so every student is their own
        #       partition and always ranks 1 in their own subgroup
        #   (2) Outer filter uses rnk > 1 (should be = 1) - returns nothing
        #       from the top of each partition
        #   (3) ORDER BY s.name instead of c.department
        "buggy_query": (
            "SELECT name, department, avg_grade FROM ("
            "    SELECT s.name, c.department, AVG(e.grade) AS avg_grade, "
            "           RANK() OVER (PARTITION BY s.student_id ORDER BY AVG(e.grade) DESC) AS rnk "
            "    FROM students s "
            "    JOIN enrollments e ON s.student_id = e.student_id "
            "    JOIN courses c ON e.course_id = c.course_id "
            "    GROUP BY s.student_id, s.name, c.department"
            ") t "
            "WHERE rnk > 1 "
            "ORDER BY name;"
        ),
        "correct_query": (
            "SELECT name, department, avg_grade FROM ("
            "    SELECT s.name, c.department, AVG(e.grade) AS avg_grade, "
            "           RANK() OVER (PARTITION BY c.department ORDER BY AVG(e.grade) DESC) AS rnk "
            "    FROM students s "
            "    JOIN enrollments e ON s.student_id = e.student_id "
            "    JOIN courses c ON e.course_id = c.course_id "
            "    GROUP BY s.student_id, s.name, c.department"
            ") t "
            "WHERE rnk = 1 "
            "ORDER BY department;"
        ),
        "hint": (
            "The query should return one row per department showing the top student by "
            "average grade. The current result doesn't match the expected shape at all - "
            "check how the window function is grouping values, what the rank filter is "
            "selecting, and how the final rows are ordered."
        ),
        "max_steps": 12,
    },
    {
        "task_id": "expert_04_student_date_null",
        "difficulty": "expert",
        "schema_sql": STUDENTS_SCHEMA,
        # Bugs:
        #   (1) LIKE '%Fall%' matches every Fall semester, not just Fall 2024
        #   (2) sums e.course_id (meaningless) instead of c.credits (missing join)
        #   (3) HAVING uses OR which over-admits students
        "buggy_query": (
            "SELECT s.name, SUM(e.course_id) AS total_credits "
            "FROM students s "
            "JOIN enrollments e ON s.student_id = e.student_id "
            "WHERE e.semester LIKE '%Fall%' "
            "GROUP BY s.student_id, s.name "
            "HAVING COUNT(*) > 3 OR SUM(e.course_id) > 10 "
            "ORDER BY s.name;"
        ),
        "correct_query": (
            "SELECT s.name, SUM(c.credits) AS total_credits "
            "FROM students s "
            "JOIN enrollments e ON s.student_id = e.student_id "
            "JOIN courses c ON e.course_id = c.course_id "
            "WHERE e.semester = 'Fall 2024' "
            "GROUP BY s.student_id, s.name "
            "HAVING COUNT(*) > 3 "
            "ORDER BY s.name;"
        ),
        "hint": (
            "The query is supposed to count total credits for a specific semester only, "
            "but too many rows are being returned and the credit totals don't look right. "
            "The filtering is too loose and the aggregated value comes from the wrong column."
        ),
        "max_steps": 12,
    },
]


def get_task(task_id: str) -> Dict[str, Any]:
    for task in TASKS:
        if task["task_id"] == task_id:
            return task
    raise KeyError(f"Unknown task_id: {task_id}")


def list_task_ids() -> List[str]:
    return [t["task_id"] for t in TASKS]
