"""Partial-credit grader for SQL debugging attempts.

Five weighted components, weights scaled by difficulty tier:

    syntax_valid      query parsed and executed
    column_match      Jaccard similarity of column name sets
    row_count_match   min(actual, expected) / max(actual, expected)
    value_match       per-column multiset overlap of cell values (sorted
                      before comparison when the gold query has no
                      outermost ORDER BY, so result-set equivalence
                      across differently-written queries earns full
                      credit as long as the output is correct)
    order_match       positional row match, only counted when the gold
                      query contains ORDER BY in its outermost SELECT

Weights are tier-specific (see DIFFICULTY_WEIGHTS below). Easy tasks
weight syntax heavily because syntax IS the bug; expert tasks weight
value_match heavily because getting the right values matters most.

If the query errored, a flat floor reward of ERROR_FLOOR = 0.05 is
returned. After computing the weighted sum we multiply by a step-
penalty factor (1.0 - 0.02 * steps_taken) and clamp to the strictly-
interior range [SCORE_MIN, SCORE_MAX] = [0.01, 0.99]. ``clamp_score``
is the AUTHORITATIVE choke point: any path that produces a reward
inside the server MUST route through it. Downstream layers should
trust that a value returned from here is already safe.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Sequence, Tuple

GradeBreakdown = Dict[str, Any]


# ---------------------------------------------------------------------------
# Difficulty-scaled weights. Each dict sums to 1.0.
# ---------------------------------------------------------------------------

DIFFICULTY_WEIGHTS: Dict[str, Dict[str, float]] = {
    "easy": {
        # Syntax IS the bug on easy tasks; weight it heavily.
        "syntax_valid": 0.25,
        "column_match": 0.20,
        "row_count_match": 0.15,
        "value_match": 0.25,
        "order_match": 0.15,
    },
    "medium": {
        "syntax_valid": 0.10,
        "column_match": 0.20,
        "row_count_match": 0.15,
        "value_match": 0.35,
        "order_match": 0.20,
    },
    "hard": {
        # Hard bugs aren't syntax errors; weight values higher.
        "syntax_valid": 0.05,
        "column_match": 0.15,
        "row_count_match": 0.15,
        "value_match": 0.45,
        "order_match": 0.20,
    },
    "expert": {
        # Expert is all about getting the right values.
        "syntax_valid": 0.05,
        "column_match": 0.10,
        "row_count_match": 0.10,
        "value_match": 0.50,
        "order_match": 0.25,
    },
}
_DEFAULT_DIFFICULTY = "medium"

STEP_PENALTY_PER_STEP = 0.02
ERROR_FLOOR = 0.05

# Clamp bounds. The Phase 2 validator rejects rewards that are exactly
# 0.0 or 1.0, so EVERY reward we emit must fall strictly inside (0, 1).
SCORE_MIN = 0.01
SCORE_MAX = 0.99


def clamp_score(score: float) -> float:
    """Clamp any candidate reward to [SCORE_MIN, SCORE_MAX].

    This is the AUTHORITATIVE clamp point. Any path that produces a
    reward inside the server MUST route through this function.
    Downstream layers should trust its output.
    """
    if score != score:  # NaN
        return SCORE_MIN
    if score in (float("inf"), float("-inf")):
        return SCORE_MIN
    if score < SCORE_MIN:
        return SCORE_MIN
    if score > SCORE_MAX:
        return SCORE_MAX
    return score


# ---------------------------------------------------------------------------
# Query-shape helpers.
# ---------------------------------------------------------------------------

def _has_outermost_order_by(query: str) -> bool:
    """Return True iff the outermost SELECT in ``query`` has an
    ORDER BY clause.

    The string-based approach walks the query tracking parenthesis
    depth and looks for ``ORDER BY`` at depth 0. This correctly
    ignores ORDER BY inside CTEs, derived-table subqueries, and
    window function specs.
    """
    if not query:
        return False
    upper = query.upper()
    depth = 0
    i = 0
    n = len(upper)
    while i < n:
        ch = upper[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0 and upper[i:i + 8] == "ORDER BY":
            # Word boundary check: make sure the char before isn't
            # alphanumeric (so we don't match e.g. ``XORDER BY``).
            if i == 0 or not upper[i - 1].isalnum():
                return True
            i += 8
            continue
        i += 1
    return False


# ---------------------------------------------------------------------------
# Per-component metrics.
# ---------------------------------------------------------------------------

def _jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


def _row_count_ratio(n_actual: int, n_expected: int) -> float:
    if n_actual == 0 and n_expected == 0:
        return 1.0
    denom = max(n_actual, n_expected)
    if denom == 0:
        return 0.0
    return min(n_actual, n_expected) / denom


def _value_match(
    actual: List[tuple],
    expected: List[tuple],
    sort_before: bool,
) -> float:
    """Per-column multiset overlap of cell values.

    Uses ``collections.Counter`` for O(n) per-column comparison so
    large result sets (thousands of rows) grade in under 100 ms.

    ``sort_before`` is ignored for the multiset logic itself (it's
    already order-insensitive) but we keep the parameter for symmetry
    with ``_order_match`` and to signal intent in callers.
    """
    if not expected:
        return 1.0 if not actual else 0.0

    n_cols = len(expected[0])
    if n_cols == 0:
        return 1.0

    total = 0
    matched = 0
    for col_idx in range(n_cols):
        exp_col: List[str] = [str(row[col_idx]) for row in expected]
        act_col: List[str] = [
            str(row[col_idx]) for row in actual if col_idx < len(row)
        ]
        exp_counter = Counter(exp_col)
        act_counter = Counter(act_col)
        # Intersection of multisets: min count per key.
        overlap = sum((exp_counter & act_counter).values())
        total += len(exp_col)
        matched += overlap
    if total == 0:
        return 1.0
    return matched / total


def _order_match(
    actual: List[tuple],
    expected: List[tuple],
    correct_query: str,
) -> float:
    """Positional row match, only counted when the gold query has an
    outermost ORDER BY. Returns 1.0 if the gold is order-insensitive.
    """
    if not _has_outermost_order_by(correct_query or ""):
        return 1.0
    if not expected:
        return 1.0 if not actual else 0.0
    matched = 0
    for i, exp_row in enumerate(expected):
        if i < len(actual) and tuple(actual[i]) == tuple(exp_row):
            matched += 1
    return matched / len(expected)


# ---------------------------------------------------------------------------
# Output formatting (used for ``query_result`` and other observation fields).
# ---------------------------------------------------------------------------

def format_result(columns: Sequence[str], rows: Sequence[tuple]) -> str:
    """Render a result set as a deterministic, human-readable text table."""
    if not columns:
        return "(no columns)"
    col_list = list(columns)
    str_rows = [[("NULL" if c is None else str(c)) for c in r] for r in rows]
    widths = [len(c) for c in col_list]
    for r in str_rows:
        for i, cell in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))

    def fmt_row(vals: Sequence[str]) -> str:
        return " | ".join(v.ljust(widths[i]) for i, v in enumerate(vals))

    sep = "-+-".join("-" * w for w in widths)
    lines = [fmt_row(col_list), sep]
    for r in str_rows:
        lines.append(fmt_row(r))
    lines.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Grader entry point.
# ---------------------------------------------------------------------------

def grade(
    *,
    query_error: str | None,
    actual_columns: Sequence[str],
    actual_rows: Sequence[tuple],
    expected_columns: Sequence[str],
    expected_rows: Sequence[tuple],
    steps_taken: int,
    correct_query: str = "",
    difficulty: str = _DEFAULT_DIFFICULTY,
) -> Tuple[float, GradeBreakdown]:
    """Compute reward in [SCORE_MIN, SCORE_MAX] and return a breakdown.

    The grader is fully deterministic. It compares the candidate
    result set against the hidden gold result set (precomputed by the
    environment at reset time). Multiple valid SQL queries that
    produce the correct result get full credit regardless of how they
    are written.
    """
    weights = DIFFICULTY_WEIGHTS.get(difficulty, DIFFICULTY_WEIGHTS[_DEFAULT_DIFFICULTY])

    if query_error is not None:
        floor = clamp_score(ERROR_FLOOR)
        return floor, {
            "syntax_valid": 0.0,
            "column_match": 0.0,
            "row_count_match": 0.0,
            "value_match": 0.0,
            "order_match": 0.0,
            "raw_score": floor,
            "step_penalty_factor": 1.0,
            "weights": dict(weights),
            "error": query_error,
        }

    syntax = 1.0
    columns = _jaccard(actual_columns, expected_columns)
    rows = _row_count_ratio(len(actual_rows), len(expected_rows))
    values = _value_match(list(actual_rows), list(expected_rows), sort_before=True)
    order = _order_match(list(actual_rows), list(expected_rows), correct_query)

    raw = (
        weights["syntax_valid"] * syntax
        + weights["column_match"] * columns
        + weights["row_count_match"] * rows
        + weights["value_match"] * values
        + weights["order_match"] * order
    )

    penalty_factor = max(0.0, 1.0 - STEP_PENALTY_PER_STEP * max(0, steps_taken))
    score = clamp_score(raw * penalty_factor)

    return score, {
        "syntax_valid": syntax,
        "column_match": columns,
        "row_count_match": rows,
        "value_match": values,
        "order_match": order,
        "raw_score": raw,
        "step_penalty_factor": penalty_factor,
        "weights": dict(weights),
        "error": None,
    }
