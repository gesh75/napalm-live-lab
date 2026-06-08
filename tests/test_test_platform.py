#!/usr/bin/env python3
"""Hermetic tests for the test platform (engine → checks → suites → runner → export).

No Docker / no live lab — collectors are monkeypatched.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assertions as A          # noqa: E402
import checks as C              # noqa: E402
import checks_builtin as CB     # noqa: E402
import exporters as EX          # noqa: E402
import jsonpath as JP           # noqa: E402
import results as R             # noqa: E402
import runner as RUN            # noqa: E402
import suites as S              # noqa: E402

pytestmark = pytest.mark.hermetic


# ── jsonpath ────────────────────────────────────────────────────────────────

def test_jsonpath_wildcard_and_quotes():
    data = {"data": {"g": {"peers": {"10.0.1.0": {"up": True}, "10.0.2.0": {"up": False}}}}}
    assert JP.resolve(data, "data.g.peers.*.up") == [True, False]
    assert JP.resolve(data, 'data.g.peers."10.0.1.0".up') == [True]
    assert JP.resolve(data, "data.nope.x") == []


# ── assertions ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("op,obs,exp,ok", [
    ("eq", "4", 4, True), ("eq", 5, 4, False), ("gte", 5, 3, True),
    ("lt", 2, 3, True), ("regex", "4.33.1F", r"^4\.33", True),
    ("nonempty", [1], None, True), ("nonempty", [], None, False),
    ("between", 50, [0, 80], True), ("contains", "abcd", "bc", True),
    ("in", "x", ["x", "y"], True), ("exists", None, None, False),
])
def test_assertions(op, obs, exp, ok):
    assert A.evaluate(op, obs, exp).passed is ok


# ── checks: selectors + quantifiers ─────────────────────────────────────────

def test_resolve_targets_filters():
    eos = C.resolve_targets({"driver": "eos"})
    assert eos and all(h.startswith(("spine", "leaf")) for h in eos)
    one = C.resolve_targets({"hostname": "leaf1"})
    assert one == ["leaf1"]
    assert len(C.resolve_targets({"all": True})) == 19


def test_evaluate_check_quantifiers():
    node = {"reachable": True,
            "data": {"get_bgp_neighbors": {"global": {"peers": {
                "1": {"is_up": True}, "2": {"is_up": True}}}}}}
    # value
    c = C.from_dict({"name": "r", "field": "reachable", "op": "eq", "expected": True})
    assert C.evaluate_check(c, node).passed
    # all
    c = C.from_dict({"name": "u", "field": "data.get_bgp_neighbors.global.peers.*.is_up",
                     "op": "eq", "expected": True, "quantifier": "all"})
    assert C.evaluate_check(c, node).passed
    # count
    c = C.from_dict({"name": "n", "field": "data.get_bgp_neighbors.global.peers.*",
                     "op": "gte", "expected": 1, "quantifier": "count"})
    assert C.evaluate_check(c, node).passed


def test_from_dict_validates():
    with pytest.raises(ValueError):
        C.from_dict({"name": "x", "field": "a"})           # missing op
    with pytest.raises(ValueError):
        C.from_dict({"name": "x", "field": "a", "op": "eq", "quantifier": "bogus"})


def test_builtin_checks_all_valid():
    for d in CB.BUILTIN_CHECKS:
        c = C.from_dict(d)
        assert c.op and c.severity in C.SEVERITIES


# ── suites ──────────────────────────────────────────────────────────────────

def test_seed_suites_load():
    ss = S.load_all_suites()
    assert {"fabric_health", "napalm_coverage", "evpn_bgp"} <= set(ss)
    for s in ss.values():
        assert s.checks, f"{s.id} has no checks"


# ── runner (monkeypatched collectors) ───────────────────────────────────────

def test_run_suite_aggregates(monkeypatch):
    def fake_collect(host, getters):
        return {"hostname": host, "reachable": True, "method": "napalm",
                "facts": {"os_version": "1.0"},
                "getters": {"get_facts": {"ok": True}},
                "data": {"get_bgp_neighbors": {"global": {"peers": {"1": {"is_up": True}}}}}}
    monkeypatch.setattr(RUN, "collect_node", fake_collect)
    monkeypatch.setattr(RUN, "run_intent", lambda h, i: {"ok": True, "command": "x"})
    suite = S.load_all_suites()["fabric_health"]
    run = RUN.run_suite(suite, fabric_filter="all")
    assert run.status == "done"
    assert run.totals["total"] > 0
    assert run.totals["errored"] == 0
    assert run.totals["passed"] == run.totals["total"]  # all green with fake data


# ── results store (temp db) ─────────────────────────────────────────────────

def test_results_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "DB_PATH", tmp_path / "t.db")
    run = R.SuiteRun(run_id="run_x", suite_id="s", suite_name="S", fabric="all",
                     status="done", totals={"passed": 2, "failed": 1, "errored": 0, "total": 3},
                     results=[{"check_id": "c", "hostname": "h", "passed": True}])
    R.save_run(run)
    got = R.get_run("run_x")
    assert got["suite_id"] == "s" and got["totals"]["total"] == 3
    listed = R.list_runs(limit=10)
    assert listed and listed[0]["run_id"] == "run_x"


# ── exporters ───────────────────────────────────────────────────────────────

def test_exporters():
    run = {"run_id": "run_x", "suite_id": "s", "suite_name": "S", "fabric": "all",
           "started": "t", "finished": "t",
           "totals": {"passed": 1, "failed": 1, "errored": 0, "total": 2},
           "results": [
               {"check_id": "a", "name": "A", "hostname": "h1", "passed": True,
                "severity": "high", "message": "ok", "duration_ms": 5, "errored": False},
               {"check_id": "b", "name": "B", "hostname": "h2", "passed": False,
                "severity": "high", "message": "FAIL: 1 != 2", "duration_ms": 5, "errored": False}]}
    xml = EX.to_junit(run)
    assert '<testsuites' in xml and 'failures="1"' in xml and '<failure' in xml
    html = EX.to_html(run)
    assert "<table" in html and "FAILED" in html
    assert EX.to_json(run)["run_id"] == "run_x"
