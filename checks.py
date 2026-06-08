#!/usr/bin/env python3
"""Declarative Check model + target selectors + evaluation.

A Check binds a TARGET (which nodes) + a SOURCE (getter / command / node field)
+ a FIELD (jsonpath) + a QUANTIFIER + an assertion (op/expected) + severity. The
runner collects each node once, then evaluates every check against that cached
state. Checks are pure data — fully unit-testable with no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _field

import jsonpath
from assertions import AssertionResult, evaluate
from config import NODE_INDEX

QUANTIFIERS = ("value", "all", "any", "count")
SEVERITIES = ("high", "med", "low")


@dataclass(frozen=True)
class Check:
    id: str
    name: str
    target: dict            # selector: {"all":true} | {fabric|tier|vendor|driver|hostname: val|[vals]}
    source: dict            # {"node":true} | {"getter":"get_facts"} | {"command":"...","parse":"json|text"} | {"intent":"bgp"}
    field: str              # jsonpath into the per-check data root (see runner)
    op: str
    expected: object = None
    quantifier: str = "value"
    severity: str = "med"
    description: str = ""


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    name: str
    hostname: str
    passed: bool
    severity: str
    op: str
    expected: object
    observed: object
    message: str
    duration_ms: int = 0
    errored: bool = False


def resolve_targets(selector: dict) -> list[str]:
    """Resolve a target selector to a list of hostnames from the fabric allowlist."""
    if not selector or selector == "all" or selector.get("all"):
        return list(NODE_INDEX)
    want = selector.get("hostname")
    if want:
        want = [want] if isinstance(want, str) else want
        return [h for h in want if h in NODE_INDEX]
    hosts = []
    for h, n in NODE_INDEX.items():
        ok = True
        for key in ("fabric", "tier", "vendor", "driver"):
            if key in selector:
                vals = selector[key]
                vals = vals if isinstance(vals, list) else [vals]
                if n.get(key) not in vals:
                    ok = False
                    break
        if ok:
            hosts.append(h)
    return hosts


def evaluate_check(check: Check, root) -> AssertionResult:
    """Resolve the check's field against ``root`` and apply quantifier + assertion."""
    matched = jsonpath.resolve(root, check.field)
    q = check.quantifier or "value"

    if q == "count":
        return evaluate(check.op, len(matched), check.expected)

    if q in ("all", "any"):
        if not matched:
            return AssertionResult(False, check.op, check.expected, None,
                                   f"FAIL: no values matched '{check.field}'")
        results = [evaluate(check.op, v, check.expected) for v in matched]
        n_ok = sum(1 for r in results if r.passed)
        passed = (n_ok == len(results)) if q == "all" else (n_ok > 0)
        return AssertionResult(passed, check.op, check.expected, f"{n_ok}/{len(results)}",
                               ("ok: " if passed else "FAIL: ") +
                               f"{q} {n_ok}/{len(results)} satisfy {check.op} {check.expected!r}")

    # "value" — first matched value (or None)
    observed = matched[0] if matched else None
    return evaluate(check.op, observed, check.expected)


def from_dict(d: dict) -> Check:
    """Build a Check from a plain dict (suite JSON / API body). Validates fields."""
    cid = d.get("id") or d.get("name") or "check"
    op = (d.get("op") or "").strip().lower()
    if not op:
        raise ValueError(f"check '{cid}': missing op")
    q = (d.get("quantifier") or "value").strip().lower()
    if q not in QUANTIFIERS:
        raise ValueError(f"check '{cid}': bad quantifier '{q}'")
    sev = (d.get("severity") or "med").strip().lower()
    if sev not in SEVERITIES:
        sev = "med"
    source = d.get("source") or {"node": True}
    if not isinstance(source, dict):
        raise ValueError(f"check '{cid}': source must be an object")
    return Check(
        id=cid, name=d.get("name") or cid, target=d.get("target") or {"all": True},
        source=source, field=d.get("field") or "", op=op,
        expected=d.get("expected"), quantifier=q, severity=sev,
        description=d.get("description") or "",
    )
