#!/usr/bin/env python3
"""Test suites — named, file-backed collections of checks (JSON, zero-dep).

A suite is `suites/<id>.json`:
    {"id","name","description","tags":[...],
     "checks":[ <check dict> | {"use":"<builtin-id>", ...overrides} ]}
Checks validate on load (bad op/quantifier → clear error, fail fast).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import checks as checks_mod
from checks_builtin import builtin_index

SUITES_DIR = Path(__file__).parent / "suites"


@dataclass(frozen=True)
class Suite:
    id: str
    name: str
    description: str
    checks: tuple
    tags: tuple = ()


def _expand_check(d: dict) -> dict:
    """Resolve a {'use': builtin-id, ...overrides} ref into a full check dict."""
    if "use" in d:
        base = dict(builtin_index().get(d["use"], {}))
        if not base:
            raise ValueError(f"unknown builtin check '{d['use']}'")
        base.update({k: v for k, v in d.items() if k != "use"})
        return base
    return d


def load_suite(path: Path) -> Suite:
    raw = json.loads(Path(path).read_text())
    sid = raw.get("id") or Path(path).stem
    checks = tuple(checks_mod.from_dict(_expand_check(c)) for c in raw.get("checks", []))
    if not checks:
        raise ValueError(f"suite '{sid}' has no checks")
    return Suite(id=sid, name=raw.get("name") or sid,
                 description=raw.get("description") or "",
                 checks=checks, tags=tuple(raw.get("tags") or []))


def load_all_suites() -> dict[str, Suite]:
    out: dict[str, Suite] = {}
    if not SUITES_DIR.exists():
        return out
    for p in sorted(SUITES_DIR.glob("*.json")):
        try:
            s = load_suite(p)
            out[s.id] = s
        except Exception as e:  # noqa: BLE001 — one bad suite mustn't hide the rest
            out[p.stem] = Suite(id=p.stem, name=p.stem,
                                description=f"LOAD ERROR: {e}", checks=(), tags=("error",))
    return out


def suite_summary(s: Suite) -> dict:
    return {"id": s.id, "name": s.name, "description": s.description,
            "tags": list(s.tags), "check_count": len(s.checks),
            "checks": [{"id": c.id, "name": c.name, "severity": c.severity,
                        "target": c.target, "description": c.description} for c in s.checks]}
