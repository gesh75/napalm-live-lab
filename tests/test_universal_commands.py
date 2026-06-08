#!/usr/bin/env python3
"""Hermetic tests for universal commands (one intent → vendor-correct command).

`_docker_exec` is monkeypatched, so no Docker / live lab is needed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import command_lib as cl  # noqa: E402
import universal_commands as uc  # noqa: E402

pytestmark = pytest.mark.hermetic


def test_every_intent_maps_for_every_lab_driver():
    """Each intent must resolve to >=1 command for eos, frr, and srl."""
    for intent in uc.INTENTS:
        for driver in ("eos", "frr", "srl"):
            cmds = uc.commands_for(driver, intent)
            assert cmds, f"no command for intent={intent} driver={driver}"
            assert all(isinstance(c, str) and c for c in cmds)


def test_intent_commands_are_read_only():
    """Universal intents must never include a mutating command."""
    for intent, spec in uc.INTENTS.items():
        for vendor, cmds in spec["cmd"].items():
            for c in cmds:
                assert cl.is_read_only(c), f"{intent}/{vendor}: {c!r} not read-only"


def test_driver_to_vendor_covers_lab():
    assert uc.DRIVER_TO_VENDOR["eos"] == "arista-eos"
    assert uc.DRIVER_TO_VENDOR["frr"] == "frr"
    assert uc.DRIVER_TO_VENDOR["srl"] == "nokia-srl"


def test_intent_list_shape():
    items = uc.intent_list()
    assert {"version", "bgp", "interfaces", "routes"} <= {i["intent"] for i in items}
    for i in items:
        assert i["label"] and i["cat"]


def test_unknown_intent_or_driver_returns_empty():
    assert uc.commands_for("eos", "nope") == []
    assert uc.commands_for("bogus", "bgp") == []


# ── run_intent dispatch (monkeypatched exec) ────────────────────────────────

def test_run_intent_picks_vendor_command(monkeypatch):
    seen = {}

    def fake(container, argv, timeout=20):
        seen["argv"] = argv
        return 0, "OUTPUT", ""

    monkeypatch.setattr(cl, "_docker_exec", fake)
    # leaf1 = eos -> first candidate "show ip bgp summary | json"
    r = cl.run_intent("leaf1", "bgp")
    assert r["ok"] is True
    assert r["intent"] == "bgp"
    assert r["command"] == "show ip bgp summary | json"
    assert r["intent_label"] == "BGP summary"


def test_run_intent_falls_back_on_failure(monkeypatch):
    """First candidate fails (rc!=0) → second candidate is tried."""
    calls = []

    def fake(container, argv, timeout=20):
        cmd = argv[-1]
        calls.append(cmd)
        # FRR memory: first 'show memory' OK in real life; simulate first fails.
        if cmd == "show memory":
            return 1, "", "boom"
        return 0, "OK", ""

    monkeypatch.setattr(cl, "_docker_exec", fake)
    r = cl.run_intent("de-fra-core-01", "memory")  # frr: [show memory, show memory summary]
    assert r["ok"] is True
    assert r["command"] == "show memory summary"
    assert len(calls) == 2  # tried both


def test_run_intent_srl_uses_sr_cli(monkeypatch):
    monkeypatch.setattr(cl, "_docker_exec", lambda c, argv, timeout=20: (0, "x", ""))
    r = cl.run_intent("spine1", "version")  # srl
    assert r["ok"] is True
    assert r["wrapper"] == "sr_cli"
    assert r["command"] == "info from state /system information version"


def test_run_intent_unknown_node():
    r = cl.run_intent("nope", "bgp")
    assert r["ok"] is False and "allowlist" in r["error"]


def test_run_intent_unknown_intent():
    r = cl.run_intent("leaf1", "teleport")
    assert r["ok"] is False and "unknown intent" in r["error"]


def test_catalog_exposes_universal():
    cat = cl.catalog()
    assert "universal" in cat
    assert {i["intent"] for i in cat["universal"]} >= {"version", "bgp", "routes"}
