#!/usr/bin/env python3
"""NAPALM collector sidecar runner.

Runs REAL napalm against a single live containerlab node and prints EXACTLY
ONE JSON line to stdout (the LAST line printed always starts with '{').

Invocation:
    python3 collect.py '{"ip":"172.20.20.21","driver":"eos",
                         "getters":["get_facts","get_bgp_neighbors"],
                         "username":"admin","password":"admin"}'

This script is intended to be executed via `docker exec napalm-runner ...`
from inside a container that is attached to the lab management networks,
because the macOS Docker Desktop host cannot route to container mgmt IPs.

Contract / output shape (single JSON line):
    {"ok":bool,"reachable":bool,"method":"napalm","driver":<driver>,
     "latency_ms":int,
     "facts":{"hostname","vendor","model","os_version","serial_number",
              "uptime","interface_list"},
     "data":{<getter_name>: <raw napalm getter result>},
     "getters":{<getter_name>:{"ok":bool,"error":str|null}},
     "error":str|null}

Rules enforced here:
  - Never raises. Always prints exactly one JSON line.
  - reachable=True only if driver.open() succeeds.
  - Each getter runs in its own try/except; per-getter ok/error recorded.
  - facts populated from get_facts when available.
  - driver.close() always called in finally.
"""

import json
import sys
import time
import traceback

# Default getters if caller omits the list.
DEFAULT_GETTERS = [
    "get_facts",
    "get_interfaces",
    "get_interfaces_ip",
    "get_bgp_neighbors",
    "get_lldp_neighbors",
    "get_environment",
]

# napalm get_facts keys we surface into the flat "facts" object.
FACTS_KEYS = (
    "hostname",
    "vendor",
    "model",
    "os_version",
    "serial_number",
    "uptime",
    "interface_list",
)


def _empty_facts():
    """Return a facts dict with every contract key present and empty."""
    return {
        "hostname": "",
        "vendor": "",
        "model": "",
        "os_version": "",
        "serial_number": "",
        "uptime": 0,
        "interface_list": [],
    }


def _emit(payload):
    """Print exactly one JSON line. This must be the final stdout line."""
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


def _base_result(driver):
    return {
        "ok": False,
        "reachable": False,
        "method": "napalm",
        "driver": driver,
        "latency_ms": 0,
        "facts": _empty_facts(),
        "data": {},
        "getters": {},
        "error": None,
    }


def _build_driver(get_network_driver, driver, ip, username, password):
    """Instantiate (not open) the correct napalm driver with vendor args.

    eos -> eAPI over HTTPS:443 (pyeapi does not verify the self-signed cert).
    srl -> napalm-srl community driver, prefer JSON-RPC over HTTPS:443.
    """
    if driver == "eos":
        klass = get_network_driver("eos")
        return klass(
            hostname=ip,
            username=username,
            password=password,
            optional_args={"transport": "https", "port": 443},
        )

    if driver == "srl":
        klass = get_network_driver("srl")
        # The napalm-srl driver supports several transports. We prefer the
        # JSON-RPC over HTTPS:443 path. These optional_args are tolerated by
        # recent napalm-srl releases; if a key is unknown the driver ignores
        # or rejects it, in which case the caller's try/except records the
        # connect failure. See README caveat.
        return klass(
            hostname=ip,
            username=username,
            password=password,
            optional_args={
                "jsonrpc_port": 443,
                "transport": "https",
                "insecure": True,
            },
        )

    raise ValueError("unknown driver: %r (expected 'eos' or 'srl')" % (driver,))


def main():
    started = time.monotonic()

    # ---- parse argv ----
    raw = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        req = json.loads(raw)
        if not isinstance(req, dict):
            raise ValueError("argv[1] must be a JSON object")
    except Exception as exc:  # noqa: BLE001 - never raise
        out = _base_result("")
        out["error"] = "invalid input JSON: %s" % (exc,)
        out["latency_ms"] = int((time.monotonic() - started) * 1000)
        _emit(out)
        return

    ip = req.get("ip") or ""
    driver = (req.get("driver") or "").strip().lower()
    username = req.get("username") or ""
    password = req.get("password") or ""
    getters = req.get("getters") or DEFAULT_GETTERS
    if not isinstance(getters, list):
        getters = DEFAULT_GETTERS

    result = _base_result(driver)

    # ---- import napalm late so import errors are reported as JSON ----
    try:
        from napalm import get_network_driver
    except Exception as exc:  # noqa: BLE001
        result["error"] = "napalm import failed: %s" % (exc,)
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        _emit(result)
        return

    if not ip:
        result["error"] = "missing 'ip' in request"
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        _emit(result)
        return

    # ---- build driver instance ----
    try:
        device = _build_driver(
            get_network_driver, driver, ip, username, password
        )
    except Exception as exc:  # noqa: BLE001 - unknown driver / build failure
        result["error"] = "driver init failed: %s" % (exc,)
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        _emit(result)
        return

    opened = False
    try:
        # ---- open (defines reachability) ----
        try:
            device.open()
            opened = True
            result["reachable"] = True
        except Exception as exc:  # noqa: BLE001
            result["reachable"] = False
            result["error"] = "open() failed: %s" % (exc,)
            result["latency_ms"] = int((time.monotonic() - started) * 1000)
            _emit(result)
            return

        # ---- run each requested getter independently ----
        any_ok = False
        for name in getters:
            entry = {"ok": False, "error": None}
            fn = getattr(device, name, None)
            if fn is None or not callable(fn):
                entry["error"] = "getter not available on driver"
                result["getters"][name] = entry
                continue
            try:
                raw_result = fn()
                result["data"][name] = raw_result
                if raw_result is None:
                    # napalm-srl logs+swallows some parse errors and returns None;
                    # treat that as a failed getter so the matrix is honest.
                    entry["error"] = "driver returned no data (unsupported / parse error)"
                else:
                    entry["ok"] = True
                    any_ok = True
            except NotImplementedError:
                entry["error"] = "not implemented"
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc) or exc.__class__.__name__
            result["getters"][name] = entry

        # ---- populate flat facts from get_facts result if present ----
        facts_raw = result["data"].get("get_facts")
        if isinstance(facts_raw, dict):
            flat = _empty_facts()
            for key in FACTS_KEYS:
                if key in facts_raw and facts_raw[key] is not None:
                    flat[key] = facts_raw[key]
            result["facts"] = flat

        # ok if reachable and at least one getter succeeded (or none requested).
        result["ok"] = bool(result["reachable"]) and (any_ok or not getters)

    except Exception as exc:  # noqa: BLE001 - belt and suspenders
        result["error"] = "collector failure: %s | %s" % (
            exc,
            traceback.format_exc().splitlines()[-1],
        )
    finally:
        if opened:
            try:
                device.close()
            except Exception:  # noqa: BLE001 - close must never break output
                pass
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        _emit(result)


if __name__ == "__main__":
    main()
