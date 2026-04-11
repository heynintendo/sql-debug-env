"""Partial-credit grader for SQL debugging attempts.

The grader compares the agent's candidate result set to the gold result set
using five weighted components and then applies a small step penalty. It is
fully deterministic: identical inputs always produce identical scores.

Components (applied only when the candidate query runs successfully):

    syntax_valid      0.10  candidate parsed and executed
    column_match      0.20  Jaccard similarity of column name sets
    row_count_match   0.15  min(actual, expected) / max(actual, expected)
    value_match       0.35  per-column multiset overlap of cell values
    order_match       0.20  positional row match, only counted when the
                            gold query contains ORDER BY

If the gold query has no ORDER BY clause, ``order_match`` is set to 1.0 -
row order is not meaningful for an unordered query. If the gold query does
have ORDER BY, the agent earns credit proportional to how many rows appear
at the correct index in their output. A correct row set in the wrong order
therefore loses 0.20 points, which it didn't before.

If the query errored, a flat floor reward of 0.05 is returned (so the agent
is nudged away from pure syntax failures but not rewarded for them).

After computing the weighted sum we multiply by a step-penalty factor
(1.0 - 0.02 * steps_taken). ``clamp_score`` is the AUTHORITATIVE choke
point: the final reward is clamped to the strictly-interior range
[SCORE_MIN, SCORE_MAX] = [0.01, 0.99] here, so nothing downstream should
need to re-clamp a well-behaved grader output. The Phase 2 validator
rejects exact 0.0 or 1.0 rewards, so we never emit those values.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

GradeBreakdown = dict


W_SYNTAX = 0.10
W_COLUMNS = 0.20
W_ROWS = 0.15
W_VALUES = 0.35
W_ORDER = 0.20
STEP_PENALTY_PER_STEP = 0.02
ERROR_FLOOR = 0.05

# Hard clamp bounds. The Phase 2 validator rejects rewards that are exactly
# 0.0 or 1.0, so EVERY reward we emit must fall strictly inside (0, 1).
SCORE_MIN = 0.01
SCORE_MAX = 0.99


def clamp_score(score: float) -> float:
    """Clamp any candidate reward to [SCORE_MIN, SCORE_MAX].

    This is the AUTHORITATIVE clamp point. Any path that produces a reward
    inside the server MUST route through this function. Downstream layers
    should trust that a value returned from here is already safe.
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


def _value_match(actual: List[tuple], expected: List[tuple]) -> float:
    """Multiset cell overlap.

    Row-order-INSENSITIVE: flatten each column to a multiset of cell
    values and measure overlap. This lets an agent earn credit for the
    right values in roughly the right shape even before they've nailed
    the ordering. Ordering is scored separately by ``_order_match``.
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
        act_remaining = list(act_col)
        for v in exp_col:
            total += 1
            if v in act_remaining:
                matched += 1
                act_remaining.remove(v)
    if total == 0:
        return 1.0
    return matched / total


def _order_match(actual: List[tuple], expected: List[tuple], correct_query: str) -> float:
    """Positional row match, only counted when the gold query has ORDER BY.

    Returns 1.0 if the gold query has no ORDER BY (ordering is not part
    of the spec). Otherwise returns the fraction of expected rows whose
    exact value appears at the same index in the candidate output.
    """
    if "ORDER BY" not in (correct_query or "").upper():
        return 1.0
    if not expected:
        return 1.0 if not actual else 0.0
    matched = 0
    for i, exp_row in enumerate(expected):
        if i < len(actual) and tuple(actual[i]) == tuple(exp_row):
            matched += 1
    return matched / len(expected)


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


def grade(
    *,
    query_error: str | None,
    actual_columns: Sequence[str],
    actual_rows: Sequence[tuple],
    expected_columns: Sequence[str],
    expected_rows: Sequence[tuple],
    steps_taken: int,
    correct_query: str = "",
) -> Tuple[float, GradeBreakdown]:
    """Compute reward in [SCORE_MIN, SCORE_MAX] and return a breakdown."""
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
            "error": query_error,
        }

    syntax = 1.0
    columns = _jaccard(actual_columns, expected_columns)
    rows = _row_count_ratio(len(actual_rows), len(expected_rows))
    values = _value_match(list(actual_rows), list(expected_rows))
    order = _order_match(list(actual_rows), list(expected_rows), correct_query)

    raw = (
        W_SYNTAX * syntax
        + W_COLUMNS * columns
        + W_ROWS * rows
        + W_VALUES * values
        + W_ORDER * order
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
        "error": None,
    }
