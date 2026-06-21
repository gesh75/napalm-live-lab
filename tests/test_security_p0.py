#!/usr/bin/env python3
"""P0 security regression tests.

1. Stored XSS: dashboard.js renderers must HTML-escape device-controlled
   fields before assigning to innerHTML.
2. TLS: core.nb_get() must verify NetBox TLS certs by default.
"""

import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.hermetic
@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_dashboard_escapes_device_model_xss():
    """A malicious device model must not reach innerHTML unescaped."""
    result = subprocess.run(
        ["node", os.path.join(HERE, "dashboard_xss_assert.js")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"XSS assertion failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    assert "OK:" in result.stdout


@pytest.mark.hermetic
def test_netbox_tls_verify_defaults_true(monkeypatch):
    """NETBOX_TLS_VERIFY must default to True (secure)."""
    monkeypatch.delenv("NETBOX_TLS_VERIFY", raising=False)
    import importlib
    import core
    importlib.reload(core)
    assert core.NETBOX_TLS_VERIFY is True


@pytest.mark.hermetic
def test_netbox_tls_verify_opt_out(monkeypatch):
    """Env opt-out only disables verification when explicitly set."""
    import importlib
    import core
    monkeypatch.setenv("NETBOX_TLS_VERIFY", "0")
    importlib.reload(core)
    assert core.NETBOX_TLS_VERIFY is False
    monkeypatch.setenv("NETBOX_TLS_VERIFY", "1")
    importlib.reload(core)
    assert core.NETBOX_TLS_VERIFY is True


@pytest.mark.hermetic
def test_nb_get_passes_verify_flag(monkeypatch):
    """nb_get must hand the verify flag to requests.get (not hardcoded False)."""
    import importlib
    import core
    monkeypatch.delenv("NETBOX_TLS_VERIFY", raising=False)
    importlib.reload(core)

    seen = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"results": [], "next": None}

    def fake_get(url, **kwargs):
        seen["verify"] = kwargs.get("verify")
        return FakeResp()

    monkeypatch.setattr(core.requests, "get", fake_get)
    monkeypatch.setattr(core, "NETBOX_URL", "https://netbox.example", raising=False)
    core.nb_get("dcim/devices/")
    assert seen["verify"] is True
