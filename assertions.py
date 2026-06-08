#!/usr/bin/env python3
"""Pure assertion evaluators for the test platform — no side effects, no I/O.

`evaluate(op, observed, expected)` returns an AssertionResult. Numbers are
coerced so "4" == 4. Used by checks.py to turn collected device state into
PASS/FAIL with an expected-vs-observed message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

OPS = ("eq", "ne", "lt", "lte", "gt", "gte", "contains", "not_contains",
       "regex", "in", "exists", "nonempty", "between")


@dataclass(frozen=True)
class AssertionResult:
    passed: bool
    op: str
    expected: object
    observed: object
    message: str


def _num(x):
    """Best-effort numeric coercion; returns None if not numeric."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return x
    try:
        return float(str(x).strip())
    except (ValueError, TypeError):
        return None


def _cmp(op, a, b) -> bool:
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        a, b = na, nb
    return {"lt": a < b, "lte": a <= b, "gt": a > b, "gte": a >= b}[op]


def evaluate(op: str, observed, expected=None) -> AssertionResult:
    """Evaluate one assertion. Never raises — bad ops/inputs return passed=False."""
    op = (op or "").strip().lower()
    try:
        if op == "eq":
            na, nb = _num(observed), _num(expected)
            passed = (na == nb) if (na is not None and nb is not None) else (observed == expected)
        elif op == "ne":
            na, nb = _num(observed), _num(expected)
            passed = (na != nb) if (na is not None and nb is not None) else (observed != expected)
        elif op in ("lt", "lte", "gt", "gte"):
            passed = _cmp(op, observed, expected)
        elif op == "contains":
            passed = expected in observed if observed is not None else False
        elif op == "not_contains":
            passed = expected not in observed if observed is not None else True
        elif op == "regex":
            passed = re.search(str(expected), str(observed)) is not None
        elif op == "in":
            passed = observed in (expected or [])
        elif op == "exists":
            passed = observed is not None
        elif op == "nonempty":
            passed = bool(observed) and (len(observed) > 0 if hasattr(observed, "__len__") else True)
        elif op == "between":
            lo, hi = (expected or [None, None])[:2]
            n = _num(observed)
            passed = n is not None and _num(lo) <= n <= _num(hi)
        else:
            return AssertionResult(False, op, expected, observed, f"unknown op '{op}'")
    except Exception as e:  # noqa: BLE001 — assertions must never raise
        return AssertionResult(False, op, expected, observed, f"eval error: {e}")

    sym = {"eq": "==", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}.get(op, op)
    msg = (f"{observed!r} {sym} {expected!r}" if op in ("eq", "ne", "lt", "lte", "gt", "gte")
           else f"{op}({observed!r}, {expected!r})")
    return AssertionResult(passed, op, expected, observed, ("ok: " if passed else "FAIL: ") + msg)
