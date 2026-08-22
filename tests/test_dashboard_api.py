#!/usr/bin/env python3
"""Hermetic Flask tests for the live-lab HTTP surface.

No Docker. Collection is mocked. These lock in: hostname allowlist, write gate,
pipe-write rejection, topology reuse of the matrix cache.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PKG_DIR = Path(__file__).resolve().parent.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

pytestmark = pytest.mark.hermetic


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LAB_ALLOW_WRITE", "0")
    monkeypatch.setenv("LAB_CONSOLE_READONLY", "1")
    import dashboard
    dashboard._matrix_cache.clear()
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def test_unknown_fabric_is_400(client):
    r = client.get("/api/lab/matrix?fabric=nope")
    assert r.status_code == 400
    assert "unknown fabric" in r.get_json()["error"]


def test_unknown_node_is_404(client):
    r = client.post("/api/lab/run", json={"hostname": "evil", "command": "show version"})
    assert r.status_code == 404


def test_run_blocks_pipe_redirect(client, monkeypatch):
    monkeypatch.setattr("command_lib._docker_exec", lambda *a, **k: (0, "SHOULD-NOT-RUN", ""))
    r = client.post("/api/lab/run", json={"hostname": "leaf1", "command": "show run | redirect flash:x"})
    body = r.get_json()
    assert body.get("ok") is False
    assert "redirect" in (body.get("error") or "").lower()
    assert "SHOULD-NOT-RUN" not in (body.get("output") or "")


def test_run_ignores_client_allow_write_without_env(client, monkeypatch):
    monkeypatch.setattr("command_lib._docker_exec", lambda *a, **k: (0, "WROTE", ""))
    r = client.post("/api/lab/run", json={
        "hostname": "leaf1", "command": "configure terminal", "allow_write": True,
    })
    body = r.get_json()
    assert body.get("blocked") is True
    assert "WROTE" not in (body.get("output") or "")


def test_topology_reuses_matrix_cache(client, monkeypatch):
    import dashboard
    calls = {"n": 0}

    def fake_matrix(fabric="all", getters=None):
        calls["n"] += 1
        return {
            "generated": "t", "fabric": fabric, "getters": [],
            "nodes": [{
                "hostname": "leaf1", "tier": "leaf", "vendor": "arista",
                "driver": "eos", "model": "cEOS", "reachable": True,
                "method": "napalm", "napalm_supported": True,
                "data": {"get_bgp_neighbors": {"global": {"peers": {}}}},
            }],
            "summary": {"total": 1, "napalm_native": 1, "exec_fallback": 0, "reachable": 1,
                        "by_driver": {"eos": 1}, "getter_support": {}},
        }

    monkeypatch.setattr(dashboard, "napalm_matrix", fake_matrix)
    monkeypatch.setattr("napalm_lab.napalm_matrix", fake_matrix)
    dashboard._matrix_cache.clear()
    a = client.get("/api/lab/matrix?fabric=clos")
    b = client.get("/api/lab/topology?fabric=clos")
    assert a.status_code == 200 and b.status_code == 200
    assert calls["n"] == 1, f"topology must reuse the matrix cache, collected {calls['n']} times"
