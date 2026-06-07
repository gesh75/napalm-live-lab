#!/usr/bin/env python3
"""Command Console backend for the NAPALM Live Lab.

Serves a curated multivendor command catalog and runs commands against live
containerlab nodes — securely.

Security model (this is a lab tool, but it ships publicly, so it defaults safe):
  * Target host MUST be in the fabric allowlist (config.NODE_INDEX) — no
    arbitrary container names reach `docker exec`.
  * Commands run as an argv LIST (``docker exec <c> <wrapper> -c <command>``),
    never through a shell — so there is no shell-injection surface.
  * Read-only guard: only operational (show/display/…) commands run by default.
    Mutating verbs (configure/no/delete/commit/write/reload/…) are blocked
    unless the caller explicitly opts in AND the deployment allows it.
  * ``LAB_CONSOLE_READONLY=1`` hard-disables write mode entirely (recommended
    for any shared/exposed deployment). The console binds to localhost by default.
  * Newlines / control characters in a command are rejected (prevents smuggling
    a second command past the read-only check).

The catalog (``command_catalog.json``) is produced by ``build_command_catalog.py``
from the private CLI corpus, so the public repo never carries the raw corpus.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from config import NODE_INDEX
from napalm_lab import _docker_exec, collect_node

HERE = Path(__file__).parent
CATALOG_PATH = HERE / "command_catalog.json"

# Per-driver CLI wrapper used by `docker exec`.
WRAPPER = {
    "eos": ["Cli", "-c"],      # Arista cEOS
    "frr": ["vtysh", "-c"],    # FRRouting
    "srl": ["sr_cli"],         # Nokia SR Linux (command is the next argv)
}

# Operational read prefixes — network-CLI verbs ONLY (no Unix ls/cat/file/more,
# which have no place on a network device and could be dangerous behind a future
# shell-wrapping driver). A command that starts with one of these is a read.
# Keep in sync with build_command_catalog.py.
READ_PREFIXES = (
    "show", "display", "get", "ping", "traceroute", "monitor", "info",
)

# Characters that could let a CLI wrapper chain or redirect a second action.
# (Exec is argv-list / no shell, so this is defense-in-depth; '|' stays allowed
# because it is a legitimate CLI output filter, e.g. `show run | section bgp`.)
UNSAFE_CHARS = (";", "`", ">", "<", "\n", "\r", "\x00")

MAX_CMD_LEN = 300
CMD_TIMEOUT = 20


def is_read_only(command: str) -> bool:
    """True if ``command`` only reads state (safe to run by default).

    Network-CLI rule: a command that *starts* with an operational verb
    (show/display/get/ping/traceroute/monitor/info) is a read — regardless of
    later words. This avoids false-positives like ``show commit history`` or
    ``show reboot reason`` while still blocking ``configure``, ``reload``,
    ``no …``, ``write``, ``delete`` (none of which start with a read verb).
    """
    c = (command or "").strip().lower()
    if not c:
        return False
    return c.startswith(READ_PREFIXES)


def _validate(command: str) -> str | None:
    """Return an error string if the command is structurally unsafe, else None."""
    if not command or not command.strip():
        return "empty command"
    if len(command) > MAX_CMD_LEN:
        return f"command too long (>{MAX_CMD_LEN} chars)"
    bad = next((ch for ch in UNSAFE_CHARS if ch in command), None)
    if bad is not None:
        label = {"\n": "newline", "\r": "carriage-return", "\x00": "null"}.get(bad, repr(bad))
        return f"command contains an unsafe character ({label})"
    return None


def write_mode_allowed() -> bool:
    """Whether write commands may ever run (env hard-switch)."""
    return os.getenv("LAB_CONSOLE_READONLY", "0") not in ("1", "true", "yes")


# ── catalog ──────────────────────────────────────────────────────────────────

_catalog_cache: dict | None = None


def load_catalog() -> dict:
    """Load (and cache) the curated command catalog. Degrades gracefully."""
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache
    try:
        _catalog_cache = json.loads(CATALOG_PATH.read_text())
    except Exception as e:  # noqa: BLE001 — never 500 the page over a missing file
        _catalog_cache = {
            "generated": "unavailable", "source": "catalog not built",
            "lab_targets": {}, "curated": {}, "napalm_getters": [],
            "library": [], "stats": {"total": 0},
            "error": f"command_catalog.json not loaded: {e}",
        }
    return _catalog_cache


def catalog() -> dict:
    """Catalog for the API, annotated with the live read-only policy."""
    cat = dict(load_catalog())
    cat["policy"] = {
        "write_mode_allowed": write_mode_allowed(),
        "max_cmd_len": MAX_CMD_LEN,
        "default": "read_only",
    }
    return cat


# ── execution ────────────────────────────────────────────────────────────────

def run_command(hostname: str, command: str, allow_write: bool = False) -> dict:
    """Run one CLI command against a live lab node.

    Returns a result dict (never raises). ``blocked`` is set when the read-only
    guard refuses a mutating command.
    """
    t0 = time.time()
    node = NODE_INDEX.get(hostname)
    if not node:
        return _err(hostname, command, "unknown node (not in fabric allowlist)")

    structural = _validate(command)
    if structural:
        return _err(hostname, command, structural)

    command = command.strip()
    driver = node["driver"]
    wrapper = WRAPPER.get(driver)
    if not wrapper:
        return _err(hostname, command, f"no CLI wrapper for driver '{driver}'")

    read_only = is_read_only(command)
    if not read_only:
        if not (allow_write and write_mode_allowed()):
            reason = ("write commands disabled on this deployment "
                      "(LAB_CONSOLE_READONLY)") if allow_write else \
                     "blocked: not a read-only command (enable write mode to run)"
            return {
                "ok": False, "blocked": True, "hostname": hostname,
                "command": command, "driver": driver,
                "wrapper": " ".join(wrapper), "read_only": False,
                "output": "", "error": reason,
                "took_ms": int((time.time() - t0) * 1000),
            }

    container = node["container"]
    argv = [*wrapper, command]
    rc, out, err = _docker_exec(container, argv, timeout=CMD_TIMEOUT)
    took = int((time.time() - t0) * 1000)
    output = out if out else ""
    ok = rc == 0
    return {
        "ok": ok, "blocked": False, "hostname": hostname, "container": container,
        "command": command, "driver": driver, "wrapper": " ".join(wrapper),
        "read_only": read_only, "rc": rc,
        "output": output.rstrip("\n"),
        "error": None if ok else (err or "non-zero exit").strip()[:300],
        "took_ms": took,
    }


def run_getter(hostname: str, getter: str) -> dict:
    """Run a single NAPALM getter against a node; return its structured slice."""
    t0 = time.time()
    node = NODE_INDEX.get(hostname)
    if not node:
        return _err(hostname, getter, "unknown node (not in fabric allowlist)")
    cat = load_catalog()
    if cat.get("error"):
        return _err(hostname, getter, f"catalog unavailable: {cat['error']}")
    valid = {g["name"] for g in cat.get("napalm_getters", [])}
    if getter not in valid:
        return _err(hostname, getter, f"unknown getter '{getter}'")
    res = collect_node(hostname, [getter])
    gstatus = res.get("getters", {}).get(getter, {})
    data = res.get("data", {}).get(getter)
    return {
        "ok": bool(gstatus.get("ok")),
        "blocked": False,
        "hostname": hostname,
        "command": getter,
        "driver": res.get("driver"),
        "wrapper": "napalm",
        "method": res.get("method"),
        "read_only": True,
        "reachable": res.get("reachable"),
        "output": json.dumps(data, indent=2, default=str) if data is not None else "",
        "error": gstatus.get("error"),
        "took_ms": int((time.time() - t0) * 1000),
    }


def _err(hostname: str, command: str, msg: str) -> dict:
    return {"ok": False, "blocked": False, "hostname": hostname,
            "command": command, "output": "", "error": msg, "took_ms": 0}
