#!/usr/bin/env python3
"""Suite runner — collect each node ONCE, then evaluate every check.

Avoids re-hitting the live lab per check: it computes the union of getters each
node needs, runs one parallel collection, then evaluates all checks against the
cached per-node state. Command/intent-based checks run (and cache) per host.
Returns a SuiteRun ready to persist via results.save_run.
"""

from __future__ import annotations

import json as _json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime

from checks import CheckResult, evaluate_check, resolve_targets
from command_lib import run_command, run_intent
from config import NODE_INDEX
from napalm_lab import collect_node
from results import SuiteRun


def _new_run_id() -> str:
    return "run_" + datetime.now().strftime("%Y%m%d_%H%M%S_") + f"{int(time.time()*1000) % 1000:03d}"


def run_suite(suite, fabric_filter: str | None = None, max_workers: int = 8) -> SuiteRun:
    run = SuiteRun(run_id=_new_run_id(), suite_id=suite.id, suite_name=suite.name,
                   fabric=fabric_filter or "all")
    if not suite.checks:
        run.status = "error"
        run.error = "suite has no checks"
        run.finished = datetime.now().isoformat(timespec="seconds")
        return run

    # 1. plan targets + the getter union each host needs.
    plan: list = []
    host_getters: dict[str, set] = {}
    for check in suite.checks:
        hosts = resolve_targets(check.target)
        if fabric_filter and fabric_filter != "all":
            hosts = [h for h in hosts if NODE_INDEX.get(h, {}).get("fabric") == fabric_filter]
        plan.append((check, hosts))
        src = check.source or {}
        if "getter" in src or "node" in src or not ({"command", "intent"} & set(src)):
            for h in hosts:
                host_getters.setdefault(h, set())
                if "getter" in src:
                    host_getters[h].add(src["getter"])
    # always include get_facts so reachable/facts/method populate.
    for h in host_getters:
        host_getters[h].add("get_facts")

    # 2. collect each needed host once, in parallel.
    cache: dict[str, dict] = {}

    def _collect(h):
        return h, collect_node(h, sorted(host_getters[h]))

    if host_getters:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for h, data in ex.map(_collect, list(host_getters)):
                cache[h] = data

    # 3. evaluate every check against the cached state (+ command/intent per host).
    cmd_cache: dict = {}
    results: list[CheckResult] = []
    for check, hosts in plan:
        src = check.source or {}
        for h in hosts:
            t0 = time.time()
            root = None
            try:
                if "command" in src:
                    key = ("c", h, src["command"])
                    if key not in cmd_cache:
                        r = run_command(h, src["command"])
                        parsed = None
                        if src.get("parse") == "json":
                            try:
                                parsed = _json.loads(r.get("output") or "")
                            except Exception:  # noqa: BLE001
                                parsed = None
                        cmd_cache[key] = {"output": r.get("output", ""), "json": parsed,
                                          "ok": r.get("ok"), "rc": r.get("rc"), "error": r.get("error")}
                    root = cmd_cache[key]
                elif "intent" in src:
                    key = ("i", h, src["intent"])
                    if key not in cmd_cache:
                        cmd_cache[key] = run_intent(h, src["intent"])
                    root = cmd_cache[key]
                else:
                    root = cache.get(h)
                ar = evaluate_check(check, root) if root is not None else None
            except Exception as e:  # noqa: BLE001 — a bad check mustn't sink the run
                results.append(CheckResult(check.id, check.name, h, False, check.severity,
                                           check.op, check.expected, None, f"errored: {e}",
                                           int((time.time() - t0) * 1000), True))
                continue
            dur = int((time.time() - t0) * 1000)
            if ar is None:
                results.append(CheckResult(check.id, check.name, h, False, check.severity,
                                           check.op, check.expected, None,
                                           "errored: no data collected", dur, True))
            else:
                results.append(CheckResult(check.id, check.name, h, ar.passed, check.severity,
                                           check.op, check.expected, ar.observed, ar.message, dur, False))

    passed = sum(1 for r in results if r.passed and not r.errored)
    errored = sum(1 for r in results if r.errored)
    failed = len(results) - passed - errored
    run.results = [asdict(r) for r in results]
    run.totals = {"passed": passed, "failed": failed, "errored": errored, "total": len(results)}
    run.status = "done"
    run.finished = datetime.now().isoformat(timespec="seconds")
    return run
