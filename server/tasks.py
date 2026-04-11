"""Task definitions for the SQL Debug Environment.

Each task bundles a fresh schema (CREATE TABLE + INSERT rows), a buggy query,
the gold-standard corrected query, a natural-language hint about the bug and a
step budget. The corrected query is executed at reset() time to compute the
hidden ``expected_output`` that the grader compares against. The expected
output is NEVER shown to the agent - the agent must investigate the schema
and data on its own using describe/diagnostic/explain actions, or check its
candidate fix against the hidden answer using a check action.

Difficulty tiers:
    easy   - surface-level syntax / typo / operator bugs (max 5 steps)
    medium - logic errors in joins, grouping, filtering (max 8 steps)
    hard   - subtle semantic bugs including NULL handling, window PARTITION,
             HAVING vs WHERE, date boundaries, COUNT() vs COUNT(*),
             self-join aliasing (max 10 steps)
    expert - 2-3 compounding bugs in real-world query patterns across CTEs,
             correlated subqueries, window functions, NULL propagation
             (max 12 steps)

Hints describe the SYMPTOM the user would observe, not the cause or the
fix. A frontier LLM reading the hint alone should not be able to infer the
exact change to make. This is intentional - the point of the environment
is to test whether an agent can reason from symptoms to a diagnosis.
"""
from __future__ import annotations

from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Schema A: employees / departments
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


# ---------------------------------------------------------------------------
# Schema B: customers / products / orders
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Schema C: sales
# ---------------------------------------------------------------------------

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
# Schema D: authors / books / checkouts (library)
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


# ---------------------------------------------------------------------------
# Schema E: students / courses / enrollments
# ---------------------------------------------------------------------------

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
# Schema F: users / sessions / purchases (e-commerce)
# ---------------------------------------------------------------------------

ECOMMERCE_SCHEMA = """
CREATE TABLE users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT NOT NULL,
    email       TEXT,
    signup_date TEXT,
    country     TEXT,
    timezone    TEXT
);
CREATE TABLE sessions (
    session_id   INTEGER PRIMARY KEY,
    user_id      INTEGER REFERENCES users(user_id),
    start_time   TEXT,
    end_time     TEXT,
    device       TEXT,
    pages_viewed INTEGER
);
CREATE TABLE purchases (
    purchase_id   INTEGER PRIMARY KEY,
    session_id    INTEGER REFERENCES sessions(session_id),
    user_id       INTEGER REFERENCES users(user_id),
    product_name  TEXT,
    amount        REAL,
    currency      TEXT,
    purchase_time TEXT,
    refunded      INTEGER DEFAULT 0
);
INSERT INTO users VALUES
    (1,  'alice',   'alice@test.com',  '2023-01-05', 'USA',     'America/New_York'),
    (2,  'bob',     'bob@test.com',    '2023-02-12', 'USA',     'America/Los_Angeles'),
    (3,  'carol',   'carol@test.com',  '2023-03-20', 'UK',      'Europe/London'),
    (4,  'dave',    'dave@test.com',   '2023-04-15', 'Germany', 'Europe/Berlin'),
    (5,  'eve',     NULL,              '2023-05-01', 'Japan',   'Asia/Tokyo'),
    (6,  'frank',   'frank@test.com',  '2023-06-10', 'Canada',  'America/Toronto'),
    (7,  'grace',   'grace@test.com',  '2023-07-04', 'USA',     'America/Chicago'),
    (8,  'henry',   'henry@test.com',  '2023-08-22', 'UK',      'Europe/London'),
    (9,  'ivy',     'ivy@test.com',    '2023-09-15', 'Japan',   'Asia/Tokyo'),
    (10, 'jack',    'jack@test.com',   '2023-10-01', 'USA',     'America/New_York'),
    (11, 'kara',    'kara@test.com',   '2023-11-12', 'Germany', 'Europe/Berlin'),
    (12, 'leo',     NULL,              '2023-12-05', 'USA',     'America/Los_Angeles'),
    (13, 'mia',     'mia@test.com',    '2024-01-18', 'Canada',  'America/Toronto'),
    (14, 'noah',    'noah@test.com',   '2024-02-08', 'UK',      'Europe/London'),
    (15, 'olivia',  'olivia@test.com', '2024-02-20', 'USA',     'America/Chicago'),
    (16, 'peter',   'peter@test.com',  '2024-03-03', 'Germany', 'Europe/Berlin'),
    (17, 'quinn',   'quinn@test.com',  '2024-03-15', 'USA',     'America/New_York'),
    (18, 'rachel',  'rachel@test.com', '2024-04-01', 'Japan',   'Asia/Tokyo'),
    (19, 'steve',   'steve@test.com',  '2024-04-12', 'Canada',  'America/Toronto'),
    (20, 'tina',    'tina@test.com',   '2024-05-01', 'UK',      'Europe/London');
INSERT INTO sessions VALUES
    (1,  1, '2024-01-05 09:00', '2024-01-05 09:45', 'desktop', 12),
    (2,  1, '2024-01-12 14:20', '2024-01-12 15:10', 'mobile',   8),
    (3,  2, '2024-01-10 11:00', '2024-01-10 11:35', 'desktop', 15),
    (4,  2, '2024-02-15 19:00', NULL,               'mobile',   3),
    (5,  3, '2024-01-18 08:30', '2024-01-18 09:15', 'tablet',   6),
    (6,  4, '2024-01-22 16:00', '2024-01-22 16:45', 'desktop', 20),
    (7,  5, '2024-01-25 10:00', '2024-01-25 10:30', 'mobile',   4),
    (8,  6, '2024-02-01 12:00', '2024-02-01 12:50', 'desktop', 18),
    (9,  7, '2024-02-05 09:15', '2024-02-05 10:00', 'mobile',   9),
    (10, 7, '2024-03-08 14:00', '2024-03-08 14:40', 'desktop', 11),
    (11, 8, '2024-02-10 15:30', '2024-02-10 16:20', 'tablet',   7),
    (12, 9, '2024-02-14 20:00', '2024-02-14 20:30', 'mobile',   5),
    (13, 10,'2024-02-18 11:00', '2024-02-18 11:50', 'desktop', 16),
    (14, 10,'2024-03-22 08:00', '2024-03-22 08:30', 'mobile',   4),
    (15, 11,'2024-02-22 17:00', '2024-02-22 17:45', 'desktop', 13),
    (16, 12,'2024-03-01 13:00', '2024-03-01 13:40', 'mobile',   8),
    (17, 13,'2024-03-05 10:30', '2024-03-05 11:20', 'tablet',  14),
    (18, 14,'2024-03-10 14:45', NULL,               'mobile',   2),
    (19, 15,'2024-03-14 16:00', '2024-03-14 16:55', 'desktop', 17),
    (20, 16,'2024-03-18 09:00', '2024-03-18 09:30', 'mobile',   6),
    (21, 17,'2024-03-22 11:30', '2024-03-22 12:00', 'desktop', 10),
    (22, 17,'2024-04-05 15:00', '2024-04-05 15:45', 'mobile',   7),
    (23, 18,'2024-03-26 19:30', '2024-03-26 20:10', 'desktop', 12),
    (24, 19,'2024-03-30 08:00', '2024-03-30 08:40', 'tablet',   9),
    (25, 20,'2024-04-02 14:00', '2024-04-02 14:35', 'mobile',   5),
    (26, 1, '2024-04-08 10:00', '2024-04-08 10:45', 'desktop', 14),
    (27, 3, '2024-04-12 16:30', '2024-04-12 17:15', 'mobile',   8),
    (28, 5, '2024-04-16 11:00', '2024-04-16 11:40', 'desktop', 11),
    (29, 7, '2024-04-20 13:30', '2024-04-20 14:20', 'tablet',  15),
    (30, 10,'2024-04-25 09:00', '2024-04-25 09:50', 'desktop', 13);
INSERT INTO purchases VALUES
    (1,  1,  1,  'Widget',   29.99, 'USD', '2024-01-05 09:30', 0),
    (2,  1,  1,  'Gadget',   49.99, 'USD', '2024-01-05 09:40', 0),
    (3,  3,  2,  'Widget',   29.99, 'USD', '2024-01-10 11:20', 0),
    (4,  3,  2,  'License',  99.00, 'USD', '2024-01-10 11:30', 1),
    (5,  5,  3,  'Gadget',   45.00, 'GBP', '2024-01-18 09:00', 0),
    (6,  6,  4,  'Support', 120.00, 'EUR', '2024-01-22 16:30', 0),
    (7,  6,  4,  'Widget',   28.00, 'EUR', '2024-01-22 16:40', 0),
    (8,  8,  6,  'Widget',   39.99, 'CAD', '2024-02-01 12:20', 0),
    (9,  9,  7,  'License',  89.00, 'USD', '2024-02-05 09:45', 0),
    (10, 11, 8,  'Gadget',   42.00, 'GBP', '2024-02-10 15:50', 1),
    (11, 12, 9,  'Widget',   3500.0,'JPY', '2024-02-14 20:15', 0),
    (12, 13, 10, 'Support',  99.00, 'USD', '2024-02-18 11:20', 0),
    (13, 13, 10, 'License', 149.00, 'USD', '2024-02-18 11:40', 0),
    (14, 13, 10, 'Widget',   29.99, 'USD', '2024-02-18 11:45', 0),
    (15, 15, 11, 'Gadget',   46.00, 'EUR', '2024-02-22 17:20', 0),
    (16, 16, 12, 'Widget',   29.99, 'USD', '2024-03-01 13:20', 1),
    (17, 17, 13, 'License', 110.00, 'CAD', '2024-03-05 10:50', 0),
    (18, 19, 15, 'Gadget',   49.99, 'USD', '2024-03-14 16:30', 0),
    (19, 19, 15, 'Support',  89.00, 'USD', '2024-03-14 16:40', 0),
    (20, 20, 16, 'Widget',   28.00, 'EUR', '2024-03-18 09:20', 0),
    (21, 21, 17, 'Widget',   29.99, 'USD', '2024-03-22 11:45', 0),
    (22, 22, 17, 'Gadget',   49.99, 'USD', '2024-04-05 15:20', 0),
    (23, 22, 17, 'License',  99.00, 'USD', '2024-04-05 15:30', 0),
    (24, 23, 18, 'Widget',   3200.0,'JPY', '2024-03-26 19:50', 0),
    (25, 24, 19, 'Support', 120.00, 'CAD', '2024-03-30 08:20', 0),
    (26, 25, 20, 'Gadget',   45.00, 'GBP', '2024-04-02 14:20', 1),
    (27, 26, 1,  'License',  99.00, 'USD', '2024-04-08 10:20', 0),
    (28, 26, 1,  'Support',  89.00, 'USD', '2024-04-08 10:30', 0),
    (29, 27, 3,  'Widget',   28.00, 'GBP', '2024-04-12 16:50', 0),
    (30, 28, 5,  'Gadget',   4200.0,'JPY', '2024-04-16 11:20', 0),
    (31, 29, 7,  'Widget',   29.99, 'USD', '2024-04-20 13:50', 0),
    (32, 29, 7,  'License',  99.00, 'USD', '2024-04-20 14:00', 0),
    (33, 30, 10, 'Gadget',   49.99, 'USD', '2024-04-25 09:30', 0),
    (34, 30, 10, 'Widget',   29.99, 'USD', '2024-04-25 09:40', 0),
    (35, 30, 10, 'Support',  89.00, 'USD', '2024-04-25 09:45', 0);
"""


# ---------------------------------------------------------------------------
# Schema G: patients / doctors / treatments / lab_results (hospital)
# ---------------------------------------------------------------------------

HOSPITAL_SCHEMA = """
CREATE TABLE patients (
    patient_id     INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    date_of_birth  TEXT,
    gender         TEXT,
    blood_type     TEXT,
    admission_date TEXT,
    discharge_date TEXT
);
CREATE TABLE doctors (
    doctor_id  INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    specialty  TEXT,
    department TEXT
);
CREATE TABLE treatments (
    treatment_id   INTEGER PRIMARY KEY,
    patient_id     INTEGER REFERENCES patients(patient_id),
    doctor_id      INTEGER REFERENCES doctors(doctor_id),
    treatment_name TEXT,
    treatment_date TEXT,
    cost           REAL,
    outcome        TEXT
);
CREATE TABLE lab_results (
    result_id    INTEGER PRIMARY KEY,
    patient_id   INTEGER REFERENCES patients(patient_id),
    test_name    TEXT,
    result_value REAL,
    normal_min   REAL,
    normal_max   REAL,
    test_date    TEXT
);
INSERT INTO doctors VALUES
    (1,  'Dr Smith',    'Cardiology',    'Cardiology'),
    (2,  'Dr Johnson',  'Oncology',      'Oncology'),
    (3,  'Dr Lee',      'Neurology',     'Neurology'),
    (4,  'Dr Garcia',   'Pediatrics',    'Pediatrics'),
    (5,  'Dr Williams', 'Cardiology',    'Cardiology'),
    (6,  'Dr Brown',    'Orthopedics',   'Orthopedics'),
    (7,  'Dr Davis',    'Oncology',      'Oncology'),
    (8,  'Dr Miller',   'Pediatrics',    'Pediatrics'),
    (9,  'Dr Wilson',   'Emergency',     'Emergency'),
    (10, 'Dr Martinez', 'Neurology',     'Neurology');
INSERT INTO patients VALUES
    (1,  'John Doe',       '1955-03-12', 'M', 'O+',  '2024-01-05', '2024-01-12'),
    (2,  'Mary Lee',       '1982-07-20', 'F', 'A-',  '2024-01-08', '2024-01-15'),
    (3,  'Alan Park',      '1970-11-02', 'M', 'B+',  '2024-01-15', NULL),
    (4,  'Susan Clark',    '1995-04-18', 'F', 'AB+', '2024-01-20', '2024-01-27'),
    (5,  'Ben Taylor',     '2010-01-25', 'M', 'O-',  '2024-02-01', '2024-02-05'),
    (6,  'Linda Hall',     '1968-09-10', 'F', 'A+',  '2024-02-03', '2024-02-14'),
    (7,  'David King',     '1977-12-05', 'M', 'B-',  '2024-02-08', NULL),
    (8,  'Carol Wright',   '1989-06-22', 'F', 'O+',  '2024-02-12', '2024-02-20'),
    (9,  'Peter Adams',    '2005-03-14', 'M', 'A+',  '2024-02-18', '2024-02-22'),
    (10, 'Nancy Baker',    '1965-08-30', 'F', 'AB-', '2024-02-25', NULL),
    (11, 'Oscar Ruiz',     '1958-02-14', 'M', 'O+',  '2024-03-01', '2024-03-10'),
    (12, 'Pam Green',      '1992-10-08', 'F', 'B+',  '2024-03-05', '2024-03-12'),
    (13, 'Quinn Bell',     '2008-05-17', 'M', 'A-',  '2024-03-10', '2024-03-15'),
    (14, 'Rita Fox',       '1975-11-25', 'F', 'O-',  '2024-03-14', NULL),
    (15, 'Sam Holt',       '1983-04-03', 'M', 'AB+', '2024-03-18', '2024-03-28'),
    (16, 'Tina Perry',     '1999-08-19', 'F', 'A+',  '2024-03-22', '2024-03-30'),
    (17, 'Ugo Vale',       '1960-12-12', 'M', 'B-',  '2024-03-25', NULL),
    (18, 'Vicky Ross',     '1987-06-05', 'F', 'O+',  '2024-04-01', '2024-04-08'),
    (19, 'Will Scott',     '2012-09-28', 'M', 'A-',  '2024-04-05', '2024-04-10'),
    (20, 'Xena Mills',     '1973-07-16', 'F', 'B+',  '2024-04-10', NULL);
INSERT INTO treatments VALUES
    (1,  1,  1,  'ECG',             '2024-01-06',   450.00, 'completed'),
    (2,  1,  1,  'Bypass Surgery',  '2024-01-08', 45000.00, 'completed'),
    (3,  2,  2,  'Chemotherapy',    '2024-01-10',  3500.00, 'completed'),
    (4,  2,  7,  'Blood Test',      '2024-01-11',   120.00, 'completed'),
    (5,  3,  3,  'MRI Scan',        '2024-01-16',  1200.00, 'completed'),
    (6,  3,  3,  'Medication',      '2024-01-18',    85.00, 'ongoing'),
    (7,  4,  4,  'Vaccination',     '2024-01-21',    60.00, 'completed'),
    (8,  5,  4,  'Check-up',        '2024-02-02',   150.00, 'completed'),
    (9,  5,  8,  'Vaccination',     '2024-02-03',    60.00, 'completed'),
    (10, 6,  5,  'Cardiac Cath',    '2024-02-05',  8500.00, 'completed'),
    (11, 6,  1,  'Medication',      '2024-02-08',   220.00, 'completed'),
    (12, 7,  3,  'CT Scan',         '2024-02-10',  1800.00, 'completed'),
    (13, 7,  10, 'Consultation',    '2024-02-12',   200.00, 'ongoing'),
    (14, 8,  7,  'Biopsy',          '2024-02-14',  2200.00, 'completed'),
    (15, 9,  8,  'X-ray',           '2024-02-19',   280.00, 'completed'),
    (16, 10, 1,  'Stress Test',     '2024-02-26',   650.00, 'ongoing'),
    (17, 10, 5,  'Medication',      '2024-02-28',   180.00, 'ongoing'),
    (18, 11, 6,  'Knee Surgery',    '2024-03-04', 15000.00, 'completed'),
    (19, 11, 6,  'Physical Therapy','2024-03-07',   300.00, 'completed'),
    (20, 12, 2,  'Chemotherapy',    '2024-03-06',  3500.00, 'completed'),
    (21, 13, 4,  'Check-up',        '2024-03-11',   150.00, 'completed'),
    (22, 14, 3,  'Medication',      '2024-03-15',   180.00, 'ongoing'),
    (23, 14, 10, 'MRI Scan',        '2024-03-18',  1200.00, 'ongoing'),
    (24, 15, 6,  'Hip Surgery',     '2024-03-20', 18000.00, 'completed'),
    (25, 16, 4,  'Vaccination',     '2024-03-23',    60.00, 'completed'),
    (26, 17, 9,  'Emergency Care',  '2024-03-26',  2500.00, 'ongoing'),
    (27, 17, 5,  'ECG',             '2024-03-28',   450.00, 'ongoing'),
    (28, 18, 2,  'Surgery',         '2024-04-02', 12000.00, 'completed'),
    (29, 19, 4,  'X-ray',           '2024-04-06',   280.00, 'completed'),
    (30, 19, 8,  'Check-up',        '2024-04-08',   150.00, 'completed'),
    (31, 20, 9,  'Emergency Care',  '2024-04-11',  2500.00, 'ongoing'),
    (32, 20, 3,  'CT Scan',         '2024-04-13',  1800.00, 'ongoing');
INSERT INTO lab_results VALUES
    (1,  1,  'Cholesterol',     245.0, 125.0, 200.0, '2024-01-06'),
    (2,  1,  'Glucose',          105.0,  70.0, 100.0, '2024-01-06'),
    (3,  2,  'WBC Count',         12.5,   4.0,  11.0, '2024-01-10'),
    (4,  3,  'Cholesterol',     180.0, 125.0, 200.0, '2024-01-16'),
    (5,  4,  'Glucose',           95.0,  70.0, 100.0, '2024-01-21'),
    (6,  6,  'Hemoglobin',        11.5,  12.0,  16.0, '2024-02-05'),
    (7,  7,  'PSA',                4.5,   0.0,   4.0, '2024-02-10'),
    (8,  8,  'WBC Count',         13.2,   4.0,  11.0, '2024-02-14'),
    (9,  10, 'Troponin',           0.8,   0.0,   0.4, '2024-02-26'),
    (10, 11, 'Hemoglobin',        10.5,  12.0,  16.0, '2024-03-04'),
    (11, 12, 'WBC Count',         14.0,   4.0,  11.0, '2024-03-06'),
    (12, 14, 'Cholesterol',     220.0, 125.0, 200.0, '2024-03-15'),
    (13, 15, 'Glucose',          110.0,  70.0, 100.0, '2024-03-20'),
    (14, 17, 'Troponin',           1.2,   0.0,   0.4, '2024-03-26'),
    (15, 18, 'Hemoglobin',        13.0,  12.0,  16.0, '2024-04-02'),
    (16, 20, 'PSA',                5.2,   0.0,   4.0, '2024-04-11');
"""


# ---------------------------------------------------------------------------
# Schema H: team_members / projects / time_entries / milestones (projects)
# ---------------------------------------------------------------------------

PROJECTS_SCHEMA = """
CREATE TABLE team_members (
    member_id    INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    role         TEXT,
    hourly_rate  REAL,
    start_date   TEXT,
    end_date     TEXT
);
CREATE TABLE projects (
    project_id   INTEGER PRIMARY KEY,
    project_name TEXT NOT NULL,
    client       TEXT,
    budget       REAL,
    start_date   TEXT,
    deadline     TEXT,
    status       TEXT
);
CREATE TABLE time_entries (
    entry_id    INTEGER PRIMARY KEY,
    member_id   INTEGER REFERENCES team_members(member_id),
    project_id  INTEGER REFERENCES projects(project_id),
    work_date   TEXT,
    hours       REAL,
    description TEXT,
    billable    INTEGER DEFAULT 1
);
CREATE TABLE milestones (
    milestone_id   INTEGER PRIMARY KEY,
    project_id     INTEGER REFERENCES projects(project_id),
    milestone_name TEXT,
    due_date       TEXT,
    completed_date TEXT
);
INSERT INTO team_members VALUES
    (1,  'Alice Chen',      'Senior Dev',  120.0, '2022-01-10', NULL),
    (2,  'Bob Martinez',    'Dev',          85.0, '2022-06-15', NULL),
    (3,  'Carol Singh',     'Designer',     95.0, '2023-02-01', NULL),
    (4,  'Dave Patel',      'Dev',          85.0, '2023-03-20', '2024-03-01'),
    (5,  'Eve Johnson',     'PM',          110.0, '2021-11-05', NULL),
    (6,  'Frank Liu',       'Senior Dev',  130.0, '2020-08-12', NULL),
    (7,  'Grace Kim',       'QA',           75.0, '2023-09-01', NULL),
    (8,  'Henry Nguyen',    'Designer',     90.0, '2023-11-15', NULL),
    (9,  'Ivy Brown',       'Dev',          80.0, '2024-01-05', NULL),
    (10, 'Jack Wilson',     'Dev',          90.0, '2022-04-10', '2023-12-15'),
    (11, 'Kara Davis',      'Senior Dev',  125.0, '2021-06-20', NULL),
    (12, 'Leo Santos',      'PM',          105.0, '2022-09-08', NULL);
INSERT INTO projects VALUES
    (1, 'Phoenix',     'Acme Corp',   150000, '2024-01-01', '2024-06-30', 'active'),
    (2, 'Atlas',       'Globex',       80000, '2024-02-01', '2024-05-31', 'active'),
    (3, 'Titan',       'Initech',     200000, '2023-11-15', '2024-04-30', 'completed'),
    (4, 'Orion',       'Umbrella',     60000, '2024-03-01', '2024-07-31', 'active'),
    (5, 'Nova',        'Hooli',       120000, '2024-01-15', '2024-08-31', 'active'),
    (6, 'Vega',        'Pied Piper',   45000, '2023-12-01', '2024-03-31', 'completed'),
    (7, 'Lyra',        'Wayne Ent',   180000, '2024-02-15', '2024-09-30', 'active'),
    (8, 'Cygnus',      'Stark Ind',    95000, '2024-03-15', '2024-07-15', 'on_hold');
INSERT INTO time_entries VALUES
    (1,  1, 1, '2024-01-02', 6.0, 'Architecture review', 1),
    (2,  1, 1, '2024-01-03', 7.5, 'Database design', 1),
    (3,  2, 1, '2024-01-02', 5.0, 'API scaffolding', 1),
    (4,  2, 1, '2024-01-04', 8.0, 'Auth implementation', 1),
    (5,  3, 1, '2024-01-03', 6.0, 'UI mockups', 1),
    (6,  5, 1, '2024-01-02', 3.0, 'Planning meeting', 0),
    (7,  6, 2, '2024-02-02', 7.0, 'Backend setup', 1),
    (8,  6, 2, '2024-02-05', 8.0, 'Integration work', 1),
    (9,  8, 2, '2024-02-03', 6.0, 'Design review', 1),
    (10, 5, 2, '2024-02-04', 2.5, 'Stakeholder sync', 0),
    (11, 1, 3, '2023-11-20', 8.0, 'Kickoff', 1),
    (12, 1, 3, '2023-11-22', 7.0, 'Schema design', 1),
    (13, 4, 3, '2023-11-25', 6.5, 'API endpoints', 1),
    (14, 4, 3, '2023-12-01', 8.0, 'Testing', 1),
    (15, 7, 3, '2023-12-05', 5.0, 'QA round 1', 1),
    (16, 7, 3, '2023-12-15', 7.0, 'QA round 2', 1),
    (17, 11, 3, '2024-01-10', 8.0, 'Code review', 1),
    (18, 11, 3, '2024-02-15', 6.0, 'Performance tuning', 1),
    (19, 2, 4, '2024-03-05', 6.0, 'Setup', 1),
    (20, 9, 4, '2024-03-06', 7.5, 'Feature dev', 1),
    (21, 9, 4, '2024-03-12', 8.0, 'Feature dev', 1),
    (22, 8, 4, '2024-03-08', 5.0, 'Mockups', 1),
    (23, 1, 5, '2024-01-18', 8.0, 'Architecture', 1),
    (24, 6, 5, '2024-01-22', 7.0, 'Backend core', 1),
    (25, 6, 5, '2024-02-01', 8.0, 'Services', 1),
    (26, 11, 5, '2024-02-10', 6.0, 'Review', 1),
    (27, 12, 5, '2024-01-20', 4.0, 'Sprint planning', 0),
    (28, 3, 5, '2024-01-25', 6.0, 'Visual design', 1),
    (29, 10, 6, '2023-12-05', 8.0, 'Initial dev', 1),
    (30, 10, 6, '2023-12-10', 7.0, 'Feature work', 1),
    (31, 2, 6, '2023-12-15', 6.0, 'Integration', 1),
    (32, 7, 6, '2024-01-10', 5.0, 'QA', 1),
    (33, 1, 7, '2024-02-20', 7.0, 'Kickoff arch', 1),
    (34, 6, 7, '2024-02-25', 8.0, 'Core systems', 1),
    (35, 6, 7, '2024-03-05', 7.5, 'API dev', 1),
    (36, 9, 7, '2024-03-01', 6.0, 'Frontend', 1),
    (37, 9, 7, '2024-03-10', 7.0, 'Frontend', 1),
    (38, 8, 7, '2024-03-03', 6.0, 'Design', 1),
    (39, 11, 7, '2024-03-15', 5.0, 'Review', 1),
    (40, 7, 7, '2024-03-20', 4.0, 'QA', 1),
    (41, 12, 7, '2024-02-22', 3.0, 'PM sync', 0),
    (42, 2, 8, '2024-03-18', 5.0, 'Setup', 1),
    (43, 9, 8, '2024-03-20', 6.0, 'Dev work', 1),
    (44, 5, 8, '2024-03-19', 2.5, 'Planning', 0);
INSERT INTO milestones VALUES
    (1,  1, 'Architecture complete', '2024-01-31', '2024-01-28'),
    (2,  1, 'Alpha release',         '2024-03-15', '2024-03-18'),
    (3,  1, 'Beta release',          '2024-05-01', NULL),
    (4,  1, 'GA release',            '2024-06-30', NULL),
    (5,  2, 'MVP',                   '2024-03-15', '2024-03-20'),
    (6,  2, 'Launch',                '2024-05-31', NULL),
    (7,  3, 'Phase 1',               '2023-12-15', '2023-12-20'),
    (8,  3, 'Phase 2',               '2024-02-15', '2024-02-18'),
    (9,  3, 'Final delivery',        '2024-04-30', '2024-04-25'),
    (10, 4, 'Design phase',          '2024-04-15', NULL),
    (11, 4, 'Dev complete',          '2024-06-30', NULL),
    (12, 5, 'Design review',         '2024-02-28', '2024-02-25'),
    (13, 5, 'Build complete',        '2024-06-30', NULL),
    (14, 5, 'QA complete',           '2024-08-15', NULL),
    (15, 6, 'Initial demo',          '2024-01-15', '2024-01-20'),
    (16, 6, 'Final delivery',        '2024-03-31', '2024-03-28'),
    (17, 7, 'Kickoff',               '2024-03-01', '2024-02-28'),
    (18, 7, 'Alpha',                 '2024-05-15', NULL),
    (19, 7, 'Beta',                  '2024-07-31', NULL),
    (20, 8, 'Planning',              '2024-04-01', NULL);
"""


# ---------------------------------------------------------------------------
# Task list. 22 tasks: 4 easy + 4 medium + 6 hard + 8 expert.
# ---------------------------------------------------------------------------

TASKS: List[Dict[str, Any]] = [
    # ------------------------------- EASY -------------------------------
    {
        "task_id": "easy_01_typo",
        "difficulty": "easy",
        "schema_sql": EMPLOYEES_SCHEMA,
        "buggy_query": "SELCT full_name, salary FROM employees WHERE dept_id = 1 ORDER BY salary DESC;",
        "correct_query": "SELECT full_name, salary FROM employees WHERE dept_id = 1 ORDER BY salary DESC;",
        "hint": "The query fails to run at all. Look closely at every token.",
        "max_steps": 5,
    },
    {
        "task_id": "easy_02_wrong_column",
        "difficulty": "easy",
        "schema_sql": EMPLOYEES_SCHEMA,
        "buggy_query": "SELECT user_name, full_name FROM employees WHERE dept_id = 2;",
        "correct_query": "SELECT username, full_name FROM employees WHERE dept_id = 2;",
        "hint": "The database rejects the query before any data comes back.",
        "max_steps": 5,
    },
    {
        "task_id": "easy_03_string_quotes",
        "difficulty": "easy",
        "schema_sql": ORDERS_SCHEMA,
        "buggy_query": "SELECT name, country FROM customers WHERE country = USA;",
        "correct_query": "SELECT name, country FROM customers WHERE country = 'USA';",
        "hint": "Running the query produces an error about something in the filter.",
        "max_steps": 5,
    },
    {
        "task_id": "easy_04_trailing_comma",
        "difficulty": "easy",
        "schema_sql": SALES_SCHEMA,
        "buggy_query": "SELECT rep_name, amount, FROM sales WHERE region = 'East';",
        "correct_query": "SELECT rep_name, amount FROM sales WHERE region = 'East';",
        "hint": "The parser won't accept the query. Count the punctuation.",
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
        "hint": "An employee you know exists in the table doesn't appear in the output.",
        "max_steps": 8,
    },
    {
        "task_id": "medium_02_missing_group_by",
        "difficulty": "medium",
        "schema_sql": ORDERS_SCHEMA,
        "buggy_query": "SELECT country, COUNT(*) AS num_customers FROM customers ORDER BY country;",
        "correct_query": "SELECT country, COUNT(*) AS num_customers FROM customers GROUP BY country ORDER BY country;",
        "hint": "You expect one row per country, but the output has far fewer rows than that.",
        "max_steps": 8,
    },
    {
        "task_id": "medium_03_wrong_order_direction",
        "difficulty": "medium",
        "schema_sql": SALES_SCHEMA,
        "buggy_query": "SELECT rep_name, amount FROM sales ORDER BY amount ASC LIMIT 5;",
        "correct_query": "SELECT rep_name, amount FROM sales ORDER BY amount DESC LIMIT 5;",
        "hint": "You asked for the top five sales and got the bottom five instead.",
        "max_steps": 8,
    },
    {
        "task_id": "medium_04_or_vs_and",
        "difficulty": "medium",
        "schema_sql": EMPLOYEES_SCHEMA,
        "buggy_query": "SELECT full_name, salary, dept_id FROM employees WHERE dept_id = 1 OR salary > 100000;",
        "correct_query": "SELECT full_name, salary, dept_id FROM employees WHERE dept_id = 1 AND salary > 100000;",
        "hint": "The result contains employees who clearly shouldn't be included.",
        "max_steps": 8,
    },

    # ------------------------------- HARD -------------------------------
    {
        "task_id": "hard_01_null_equality",
        "difficulty": "hard",
        "schema_sql": EMPLOYEES_SCHEMA,
        "buggy_query": "SELECT full_name FROM employees WHERE manager_id = NULL ORDER BY emp_id;",
        "correct_query": "SELECT full_name FROM employees WHERE manager_id IS NULL ORDER BY emp_id;",
        "hint": "The query returns zero rows even though you can see in the table that some rows clearly satisfy the intended condition.",
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
        "hint": "The database rejects the query at runtime and complains about something in the filter.",
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
        "hint": "The last column is supposed to be a within-region ranking but every row shows rank 1 or rank 2.",
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
        "hint": "Pulling February 2024 orders, and the output looks a little short compared to what's in the table.",
        "max_steps": 10,
    },
    {
        # NEW hard task: COUNT(column) skips NULLs, COUNT(*) doesn't.
        # Some patients are still admitted (NULL discharge_date) so the
        # buggy query undercounts patients for doctors whose patients are
        # still in the hospital.
        "task_id": "hard_05_count_null_skip",
        "difficulty": "hard",
        "schema_sql": HOSPITAL_SCHEMA,
        "buggy_query": (
            "SELECT d.name, COUNT(p.discharge_date) AS patient_count "
            "FROM doctors d "
            "LEFT JOIN treatments t ON d.doctor_id = t.doctor_id "
            "LEFT JOIN patients p ON t.patient_id = p.patient_id "
            "GROUP BY d.name "
            "ORDER BY d.name;"
        ),
        "correct_query": (
            "SELECT d.name, COUNT(DISTINCT t.patient_id) AS patient_count "
            "FROM doctors d "
            "LEFT JOIN treatments t ON d.doctor_id = t.doctor_id "
            "LEFT JOIN patients p ON t.patient_id = p.patient_id "
            "GROUP BY d.name "
            "ORDER BY d.name;"
        ),
        "hint": "The patient totals for a few of the neurology and emergency doctors look implausibly low. You know from browsing the tables that they've treated more patients than that.",
        "max_steps": 10,
    },
    {
        # NEW hard task: self-join where < should be used instead of <>
        # so each pair of (member_a, member_b) isn't counted twice.
        "task_id": "hard_06_self_join_double_count",
        "difficulty": "hard",
        "schema_sql": PROJECTS_SCHEMA,
        "buggy_query": (
            "SELECT DISTINCT t1.name AS member_a, t2.name AS member_b, p.project_name "
            "FROM time_entries te1 "
            "JOIN time_entries te2 ON te1.project_id = te2.project_id AND te1.member_id <> te2.member_id "
            "JOIN team_members t1 ON te1.member_id = t1.member_id "
            "JOIN team_members t2 ON te2.member_id = t2.member_id "
            "JOIN projects p ON te1.project_id = p.project_id "
            "ORDER BY p.project_name, t1.name, t2.name;"
        ),
        "correct_query": (
            "SELECT DISTINCT t1.name AS member_a, t2.name AS member_b, p.project_name "
            "FROM time_entries te1 "
            "JOIN time_entries te2 ON te1.project_id = te2.project_id AND te1.member_id < te2.member_id "
            "JOIN team_members t1 ON te1.member_id = t1.member_id "
            "JOIN team_members t2 ON te2.member_id = t2.member_id "
            "JOIN projects p ON te1.project_id = p.project_id "
            "ORDER BY p.project_name, t1.name, t2.name;"
        ),
        "hint": "You're trying to list pairs of team members who worked on the same project. The output has roughly twice as many rows as you'd expect.",
        "max_steps": 10,
    },

    # ------------------------------ EXPERT ------------------------------
    {
        "task_id": "expert_01_library_multi_bug",
        "difficulty": "expert",
        "schema_sql": LIBRARY_SCHEMA,
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
        "hint": "The output is missing several authors you expected, the authors that do appear are in the wrong order, and a handful of entries shouldn't be there at all.",
        "max_steps": 12,
    },
    {
        "task_id": "expert_02_library_complex_join",
        "difficulty": "expert",
        "schema_sql": LIBRARY_SCHEMA,
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
        "hint": "The list of books in the output contains books that are currently checked out, and the author column shows numbers instead of names.",
        "max_steps": 12,
    },
    {
        "task_id": "expert_03_student_window_agg",
        "difficulty": "expert",
        "schema_sql": STUDENTS_SCHEMA,
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
        "hint": "The expected result shape is one row per department naming the top student there, but the actual output is either empty or completely scrambled.",
        "max_steps": 12,
    },
    {
        "task_id": "expert_04_student_date_null",
        "difficulty": "expert",
        "schema_sql": STUDENTS_SCHEMA,
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
        "hint": "The output lists too many students, and the credit totals for the ones that do appear don't match what you'd compute by hand.",
        "max_steps": 12,
    },
    {
        # NEW expert task: NULL propagation through LEFT JOIN aggregates
        "task_id": "expert_05_null_revenue_leak",
        "difficulty": "expert",
        "schema_sql": ECOMMERCE_SCHEMA,
        "buggy_query": (
            "SELECT u.username, SUM(p.amount) AS total_revenue "
            "FROM users u "
            "LEFT JOIN purchases p ON u.user_id = p.user_id "
            "WHERE p.currency = 'USD' "
            "GROUP BY u.user_id, u.username "
            "ORDER BY u.username;"
        ),
        "correct_query": (
            "SELECT u.username, COALESCE(SUM(p.amount), 0.0) AS total_revenue "
            "FROM users u "
            "LEFT JOIN purchases p ON u.user_id = p.user_id AND p.currency = 'USD' AND p.refunded = 0 "
            "GROUP BY u.user_id, u.username "
            "ORDER BY u.username;"
        ),
        "hint": "Revenue totals look too high, and more than half the users who should appear in the output are simply missing from it.",
        "max_steps": 12,
    },
    {
        # NEW expert task: running total window function. Three bugs:
        #   (1) PARTITION BY doctor_id instead of patient_id
        #   (2) ORDER BY t.treatment_date DESC inside the window (wrong
        #       direction, so running totals reflect reverse chronological)
        #   (3) extra WHERE t.outcome = 'completed' drops ongoing
        #       treatments that belong in the output
        "task_id": "expert_06_window_running_total",
        "difficulty": "expert",
        "schema_sql": HOSPITAL_SCHEMA,
        "buggy_query": (
            "SELECT p.name AS patient, t.treatment_date, t.cost, "
            "       SUM(t.cost) OVER (PARTITION BY t.doctor_id ORDER BY t.treatment_date DESC "
            "                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total "
            "FROM treatments t "
            "JOIN patients p ON t.patient_id = p.patient_id "
            "WHERE t.outcome = 'completed' "
            "ORDER BY p.name, t.treatment_date;"
        ),
        "correct_query": (
            "SELECT p.name AS patient, t.treatment_date, t.cost, "
            "       SUM(t.cost) OVER (PARTITION BY t.patient_id ORDER BY t.treatment_date "
            "                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total "
            "FROM treatments t "
            "JOIN patients p ON t.patient_id = p.patient_id "
            "ORDER BY p.name, t.treatment_date;"
        ),
        "hint": "The running totals don't match up when you add costs by hand for a given patient, and several treatments that belong in the report are missing entirely.",
        "max_steps": 12,
    },
    {
        # NEW expert task: correlated subquery / ROW_NUMBER with 3 bugs
        "task_id": "expert_07_top_per_group",
        "difficulty": "expert",
        "schema_sql": ECOMMERCE_SCHEMA,
        "buggy_query": (
            "SELECT u.username, p.product_name, p.amount, p.purchase_time "
            "FROM users u "
            "JOIN ("
            "    SELECT user_id, product_name, amount, purchase_time, "
            "           ROW_NUMBER() OVER (ORDER BY purchase_time DESC) AS rn "
            "    FROM purchases"
            ") p ON u.user_id = p.user_id "
            "WHERE p.rn < 5 "
            "ORDER BY u.username;"
        ),
        "correct_query": (
            "SELECT DISTINCT u.username, p.product_name, p.amount, p.purchase_time "
            "FROM users u "
            "JOIN ("
            "    SELECT user_id, product_name, amount, purchase_time, "
            "           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY purchase_time DESC) AS rn "
            "    FROM purchases "
            "    WHERE refunded = 0"
            ") p ON u.user_id = p.user_id "
            "WHERE p.rn = 1 "
            "ORDER BY u.username;"
        ),
        "hint": "You're trying to get each user's most recent non-refunded purchase as a single row. Some users appear multiple times in the output, and most users are missing entirely.",
        "max_steps": 12,
    },
    {
        # NEW expert task: CTE-based progress tracking with 3 compounding bugs
        "task_id": "expert_08_cte_progress_tracking",
        "difficulty": "expert",
        "schema_sql": PROJECTS_SCHEMA,
        "buggy_query": (
            "WITH all_hours AS ("
            "    SELECT te.project_id, SUM(te.hours) AS total_hours, "
            "           SUM(te.hours * tm.hourly_rate) AS total_cost "
            "    FROM time_entries te "
            "    JOIN team_members tm ON te.member_id = tm.member_id "
            "    GROUP BY te.project_id"
            "), "
            "milestone_stats AS ("
            "    SELECT project_id, "
            "           COUNT(*) AS total, "
            "           SUM(CASE WHEN completed_date IS NOT NULL THEN 1 ELSE 0 END) AS completed "
            "    FROM milestones"
            ") "
            "SELECT p.project_name, "
            "       COALESCE(ah.total_hours, 0) AS total_hours, "
            "       COALESCE(ah.total_cost, 0) AS total_cost, "
            "       ROUND(100.0 * ms.completed / ms.total, 1) AS pct_complete "
            "FROM projects p "
            "LEFT JOIN all_hours ah ON p.project_id = ah.project_id "
            "LEFT JOIN milestone_stats ms ON p.project_id = ms.project_id "
            "ORDER BY p.project_name;"
        ),
        "correct_query": (
            "WITH billable_hours AS ("
            "    SELECT te.project_id, SUM(te.hours) AS total_hours, "
            "           SUM(te.hours * tm.hourly_rate) AS total_cost "
            "    FROM time_entries te "
            "    JOIN team_members tm ON te.member_id = tm.member_id "
            "    WHERE te.billable = 1 "
            "    GROUP BY te.project_id"
            "), "
            "milestone_stats AS ("
            "    SELECT project_id, "
            "           COUNT(*) AS total, "
            "           SUM(CASE WHEN completed_date IS NOT NULL THEN 1 ELSE 0 END) AS completed "
            "    FROM milestones "
            "    GROUP BY project_id"
            ") "
            "SELECT p.project_name, "
            "       COALESCE(bh.total_hours, 0) AS total_hours, "
            "       COALESCE(bh.total_cost, 0) AS total_cost, "
            "       ROUND(100.0 * ms.completed / ms.total, 1) AS pct_complete "
            "FROM projects p "
            "LEFT JOIN billable_hours bh ON p.project_id = bh.project_id "
            "LEFT JOIN milestone_stats ms ON p.project_id = ms.project_id "
            "WHERE p.status = 'active' "
            "ORDER BY p.project_name;"
        ),
        "hint": "The output contains projects that shouldn't be in the report, percentage-complete comes out blank for most rows, and the hours totals are higher than what a manual sum would give.",
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
