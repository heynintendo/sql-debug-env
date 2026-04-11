"""Partial-credit grader for SQL debugging attempts.

The grader compares the agent's candidate result set to the gold result set
using four weighted components and then applies a small step penalty. It is
fully deterministic: identical inputs always produce identical scores.

Components (applied only when the candidate query runs successfully):
    syntax_valid     0.15  — candidate parsed and executed (always 1.0 if we got here)
    column_match     0.25  — Jaccard similarity of column name sets
    row_count_match  0.20  — min(actual, expected) / max(actual, expected)
    value_match      0.40  — fraction of candidate cells that also appear at
                             the right (row-ignorant, multiset) position in
                             the expected output

If the query errored, a flat floor reward of 0.05 is returned (so the agent
is nudged away from pure syntax failures but not rewarded for them).

After computing the weighted sum we multiply by a step-penalty factor
(1.0 - 0.02 * steps_taken). The final reward is then clamped to the
strictly-interior range [SCORE_MIN, SCORE_MAX] = [0.01, 0.99] — the Phase 2
validator rejects exact 0.0 or 1.0 rewards, so we never emit those values.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

GradeBreakdown = dict


W_SYNTAX = 0.15
W_COLUMNS = 0.25
W_ROWS = 0.20
W_VALUES = 0.40
STEP_PENALTY_PER_STEP = 0.02
ERROR_FLOOR = 0.05

# Hard clamp bounds. The Phase 2 validator rejects rewards that are exactly
# 0.0 or 1.0, so EVERY reward we emit must fall strictly inside (0, 1). We
# use 0.01 and 0.99 as the clamp endpoints so a "perfect" solve is still
# distinguishable from a near-perfect one but never breaches the limit.
SCORE_MIN = 0.01
SCORE_MAX = 0.99


def clamp_score(score: float) -> float:
    """Clamp any candidate reward to [SCORE_MIN, SCORE_MAX]."""
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

    Flatten each row to a tuple of stringified cells so that types line up,
    then count how many expected cells appear (with multiplicity) in the
    candidate output. Order-insensitive at the row level but position-
    sensitive within a row — you can't get credit for putting the right
    values in the wrong columns.
    """
    if not expected:
        return 1.0 if not actual else 0.0

    n_cols = len(expected[0])
    if n_cols == 0:
        return 1.0

    # Per-column multisets
    total = 0
    matched = 0
    for col_idx in range(n_cols):
        exp_col: List[str] = [str(row[col_idx]) for row in expected]
        act_col: List[str] = [
            str(row[col_idx]) for row in actual if col_idx < len(row)
        ]
        # count matches as multiset intersection
        act_remaining = list(act_col)
        for v in exp_col:
            total += 1
            if v in act_remaining:
                matched += 1
                act_remaining.remove(v)
    if total == 0:
        return 1.0
    return matched / total


def format_result(columns: Sequence[str], rows: Sequence[tuple]) -> str:
    """Render a result set as a deterministic, human-readable text table.

    Used for both ``expected_output`` (shown to the agent) and
    ``query_result`` (feedback after each step). Keeping the renderer stable
    means the LLM can diff the two strings directly.
    """
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
) -> Tuple[float, GradeBreakdown]:
    """Compute reward in [0, 1] and return it along with a breakdown dict."""
    if query_error is not None:
        floor = clamp_score(ERROR_FLOOR)
        return floor, {
            "syntax_valid": 0.0,
            "column_match": 0.0,
            "row_count_match": 0.0,
            "value_match": 0.0,
            "raw_score": floor,
            "step_penalty_factor": 1.0,
            "error": query_error,
        }

    syntax = 1.0
    columns = _jaccard(actual_columns, expected_columns)
    rows = _row_count_ratio(len(actual_rows), len(expected_rows))
    values = _value_match(list(actual_rows), list(expected_rows))

    raw = (
        W_SYNTAX * syntax
        + W_COLUMNS * columns
        + W_ROWS * rows
        + W_VALUES * values
    )

    penalty_factor = max(0.0, 1.0 - STEP_PENALTY_PER_STEP * max(0, steps_taken))
    score = raw * penalty_factor

    # Clamp to strictly-interior (0, 1) — never emit exactly 0.0 or 1.0.
    score = clamp_score(score)

    return score, {
        "syntax_valid": syntax,
        "column_match": columns,
        "row_count_match": rows,
        "value_match": values,
        "raw_score": raw,
        "step_penalty_factor": penalty_factor,
        "error": None,
    }
