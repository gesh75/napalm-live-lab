#!/usr/bin/env python3
"""Hermetic tests for the Command Console backend (command_lib).

No Docker / no live lab needed — `_docker_exec` and `collect_node` are
monkeypatched. Run: ./venv/bin/python -m pytest tests/test_command_lib.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import command_lib as cl  # noqa: E402
import build_command_catalog as bcc  # noqa: E402

pytestmark = pytest.mark.hermetic


# ── read-only guard ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd,expect", [
    ("show version", True),
    ("show ip bgp summary", True),
    ("show run | section bgp", True),
    ("display interface brief", True),       # Huawei/Comware style read
    ("ping 10.0.0.1", True),
    ("info from state interface mgmt0", True),  # SR Linux read
    # Previously FALSE-POSITIVE blocked (substring match on write verbs) — now allowed:
    ("show commit history", True),           # had 'commit' substring
    ("show apply-groups", True),             # had 'apply' substring
    ("show install pending", True),          # had 'install' substring
    ("show reboot reason", True),            # had 'reboot' substring
    ("show reload schedule", True),          # had 'reload' substring
    ("configure terminal", False),
    ("conf t", False),
    ("no router bgp 65000", False),
    ("delete flash:/startup", False),
    ("write memory", False),
    ("reload", False),
    ("clear ip bgp *", False),
    ("set interface eth1 disabled", False),
    ("commit", False),
    ("", False),
    ("random words", False),
])
def test_is_read_only(cmd, expect):
    assert cl.is_read_only(cmd) is expect


def test_read_prefixes_are_network_only():
    """Unix shell verbs must NOT be read prefixes (no ls/cat/file/more)."""
    for unix in ("ls", "cat ", "file ", "more "):
        assert unix not in cl.READ_PREFIXES


def test_build_and_lib_guards_agree():
    """The catalog builder and runtime must classify identically."""
    for c in ["show version", "configure terminal", "no shutdown", "ping 1.1.1.1"]:
        assert bcc.is_read_only(c) == cl.is_read_only(c)


@pytest.mark.parametrize("cmd,bad", [
    ("show version", False),
    ("show run | section bgp", False),            # pipe filter stays allowed
    ("", True),
    ("show " + "x" * 400, True),
    ("show version\nconfigure terminal", True),   # newline smuggling
    ("show version\rfoo", True),
    ("show \x00 version", True),
    ("show version; reload", True),               # semicolon chaining
    ("show version `reload`", True),              # backtick
    ("show version > /etc/passwd", True),         # redirect
    ("show version < /etc/passwd", True),
])
def test_validate(cmd, bad):
    assert (cl._validate(cmd) is not None) is bad


def test_run_rejects_chaining_before_exec(monkeypatch):
    reached = {"exec": False}
    monkeypatch.setattr(cl, "_docker_exec", lambda *a, **k: (reached.__setitem__("exec", True), (0, "", ""))[1])
    r = cl.run_command("leaf1", "show version; reload")
    assert r["ok"] is False and "unsafe character" in r["error"]
    assert reached["exec"] is False


# ── run_command (allowlist + wrapper + guard) ────────────────────────────────

@pytest.fixture
def fake_exec(monkeypatch):
    calls = {}

    def _fake(container, argv, timeout=20):
        calls["container"] = container
        calls["argv"] = argv
        return 0, "FAKE-OUTPUT for: " + " ".join(argv[2:]), ""

    monkeypatch.setattr(cl, "_docker_exec", _fake)
    return calls


def test_run_rejects_unknown_node(fake_exec):
    r = cl.run_command("not-a-node", "show version")
    assert r["ok"] is False and "allowlist" in r["error"]
    assert "argv" not in fake_exec  # never reached docker


def test_run_blocks_write_by_default(fake_exec):
    r = cl.run_command("leaf1", "configure terminal")
    assert r["blocked"] is True and r["ok"] is False
    assert "argv" not in fake_exec  # guard fired before exec


def test_run_allows_write_when_opted_in(fake_exec, monkeypatch):
    monkeypatch.setenv("LAB_CONSOLE_READONLY", "0")
    r = cl.run_command("leaf1", "configure terminal", allow_write=True)
    assert r["blocked"] is False and r["ok"] is True


def test_readonly_env_hard_disables_write(fake_exec, monkeypatch):
    monkeypatch.setenv("LAB_CONSOLE_READONLY", "1")
    r = cl.run_command("leaf1", "configure terminal", allow_write=True)
    assert r["blocked"] is True
    assert "LAB_CONSOLE_READONLY" in r["error"]


def test_wrapper_per_driver(fake_exec):
    # leaf1 = eos -> Cli ; de-fra-core-01 = frr -> vtysh ; spine1 = srl -> sr_cli
    cl.run_command("leaf1", "show version")
    assert fake_exec["argv"][:2] == ["Cli", "-c"]
    cl.run_command("de-fra-core-01", "show version")
    assert fake_exec["argv"][:2] == ["vtysh", "-c"]
    cl.run_command("spine1", "show version")
    assert fake_exec["argv"][0] == "sr_cli"


def test_run_passes_command_as_single_argv(fake_exec):
    cl.run_command("leaf1", "show ip bgp summary")
    # command is the LAST argv element — never split, never shelled.
    assert fake_exec["argv"][-1] == "show ip bgp summary"
    assert fake_exec["container"] == "clab-clos-evpn-leaf1"


def test_run_newline_blocked_before_exec(fake_exec):
    r = cl.run_command("leaf1", "show version\nreload")
    assert r["ok"] is False and "newline" in r["error"]
    assert "argv" not in fake_exec


# ── run_getter ───────────────────────────────────────────────────────────────

def test_run_getter_valid(monkeypatch):
    monkeypatch.setattr(cl, "collect_node", lambda h, g: {
        "driver": "eos", "method": "napalm", "reachable": True,
        "getters": {"get_facts": {"ok": True, "error": None}},
        "data": {"get_facts": {"hostname": h, "vendor": "Arista"}},
    })
    r = cl.run_getter("leaf1", "get_facts")
    assert r["ok"] is True and "Arista" in r["output"]


def test_run_getter_unknown(monkeypatch):
    r = cl.run_getter("leaf1", "get_bananas")
    assert r["ok"] is False and "unknown getter" in r["error"]


def test_run_getter_unknown_node():
    r = cl.run_getter("nope", "get_facts")
    assert r["ok"] is False and "allowlist" in r["error"]


# ── catalog ──────────────────────────────────────────────────────────────────

def test_catalog_shape():
    cat = cl.catalog()
    assert cat["stats"]["total"] > 0
    assert set(cat["curated"]) >= {"arista", "frr", "nokia"}
    assert {g["name"] for g in cat["napalm_getters"]} >= {"get_facts", "get_bgp_neighbors"}
    assert cat["policy"]["default"] == "read_only"


def test_catalog_has_no_multiline_commands():
    """Library must be single-line operational commands (no smuggling vectors)."""
    for c in cl.load_catalog().get("library", []):
        assert "\n" not in c["cmd"]
