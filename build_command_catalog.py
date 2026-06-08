#!/usr/bin/env python3
"""Build ``command_catalog.json`` from the multivendor CLI corpus.

The corpus (``DCN_Network_Tool/cli_corpus/commands.json`` — 7,821 records of
``{os, role, vendor, cat, title, cmd, desc}``) is a private working asset. This
script distills it into a *curated, public-safe* catalog the NAPALM Live Lab
Command Console ships with, so the public repo never carries the raw corpus.

What it produces (``command_catalog.json``):
  * ``library``  — deduped, classified multivendor commands (browse + search)
  * ``curated``  — hand-picked, known-good operational commands per lab vendor
                   that are safe to run live against the containerlab nodes
  * ``napalm_getters`` — the structured NAPALM getters exposed as "commands"
  * ``lab_targets`` — which live nodes each CLI wrapper reaches
  * ``stats``    — counts for the UI

Run:
    python3 build_command_catalog.py            # uses the default corpus path
    CORPUS=/path/to/commands.json python3 build_command_catalog.py

The script is import-safe: classification helpers are unit-tested in
``tests/test_command_lib.py`` without needing the corpus on disk.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_CORPUS = (
    HERE.parent / "DCN_Network_Tool" / "cli_corpus" / "commands.json"
)
OUT = HERE / "command_catalog.json"

# Read-only operational prefixes (network-CLI verbs only). Must stay in sync
# with command_lib.READ_PREFIXES — both are unit-tested for agreement.
READ_PREFIXES = (
    "show", "display", "get", "ping", "traceroute", "monitor", "info",
)

# ── Curated, known-good operational commands per lab vendor ──────────────────
# These are guaranteed-runnable against the live containerlab nodes and form the
# "Quick commands" rail in the console. Kept small and dependable on purpose.
CURATED = {
    "arista": {  # cEOS via `Cli -c`
        "label": "Arista EOS (cEOS)",
        "wrapper": "Cli",
        "commands": [
            ("show version", "System / version", "Facts"),
            ("show hostname", "Configured hostname", "Facts"),
            ("show interfaces status", "Interface status table", "Interfaces"),
            ("show ip interface brief", "L3 interface summary", "Interfaces"),
            ("show ip bgp summary", "BGP session summary", "BGP"),
            ("show bgp evpn summary", "EVPN address-family peers", "EVPN"),
            ("show ip route summary", "RIB size by protocol", "Routing"),
            ("show lldp neighbors", "LLDP adjacencies", "Topology"),
            ("show vlan", "VLAN database", "Switching"),
            ("show vxlan vtep", "VXLAN remote VTEPs", "EVPN"),
            ("show mac address-table", "Learned MAC table", "Switching"),
            ("show running-config", "Full running configuration", "Config"),
        ],
    },
    "frr": {  # FRR via `vtysh -c`
        "label": "FRRouting (vtysh)",
        "wrapper": "vtysh",
        "commands": [
            ("show version", "FRR version + daemons", "Facts"),
            ("show interface brief", "Interface summary", "Interfaces"),
            ("show ip interface brief", "L3 interface addresses", "Interfaces"),
            ("show ip bgp summary", "BGP session summary", "BGP"),
            ("show bgp ipv4 unicast summary", "IPv4 unicast peers", "BGP"),
            ("show ip ospf neighbor", "OSPF adjacencies", "Routing"),
            ("show ip route", "Routing table (RIB)", "Routing"),
            ("show ip route bgp", "BGP-learned routes", "Routing"),
            ("show running-config", "Full running configuration", "Config"),
            ("show bgp summary json", "BGP summary as JSON", "BGP"),
        ],
    },
    "nokia": {  # SR Linux via `sr_cli`
        "label": "Nokia SR Linux (sr_cli)",
        "wrapper": "sr_cli",
        "commands": [
            ("show version", "Platform + software version", "Facts"),
            ("show interface brief", "Interface brief", "Interfaces"),
            ("show network-instance default protocols bgp neighbor",
             "BGP neighbors (default NI)", "BGP"),
            ("show network-instance default route-table ipv4-unicast summary",
             "IPv4 route-table summary", "Routing"),
            ("show platform", "Platform / chassis state", "Facts"),
            ("show system lldp neighbor", "LLDP adjacencies", "Topology"),
            ("info from state interface mgmt0", "Mgmt interface state", "Interfaces"),
            ("info from running", "Full running configuration (SRL — not 'show running-config')", "Config"),
            ("info from running network-instance default protocols bgp",
             "BGP config (running)", "BGP"),
        ],
    },
}

# NAPALM getters surfaced as runnable "commands" (structured JSON output).
NAPALM_GETTERS = [
    ("get_facts", "Vendor, model, OS, serial, uptime, interface list"),
    ("get_interfaces", "Per-interface up/enabled/speed/MAC/MTU"),
    ("get_interfaces_ip", "IPv4/IPv6 addresses per interface"),
    ("get_bgp_neighbors", "BGP peers: state, AS, prefixes, uptime"),
    ("get_lldp_neighbors", "LLDP neighbor map"),
    ("get_environment", "PSU/fan/temperature/CPU/memory"),
]


def first_line(cmd: str) -> str:
    """First non-empty line of a (possibly multi-line) corpus cmd."""
    for ln in (cmd or "").splitlines():
        s = ln.strip()
        if s:
            # Strip an inline '# comment' that the corpus uses to annotate.
            s = re.split(r"\s{2,}#", s, maxsplit=1)[0].strip()
            return s
    return ""


def classify(cmd: str) -> str:
    """Return 'show' | 'config' | 'multi' for a corpus command body."""
    lines = [l for l in (cmd or "").splitlines() if l.strip()]
    if len(lines) > 1:
        return "multi"
    fl = first_line(cmd).lower()
    if any(fl.startswith(p) for p in READ_PREFIXES):
        return "show"
    return "config"


def is_read_only(command: str) -> bool:
    """True if a single command only reads state (safe to run by default).

    Mirrors command_lib.is_read_only — kept in sync; both are unit-tested.
    A command that starts with an operational verb is a read.
    """
    c = (command or "").strip().lower()
    if not c:
        return False
    return c.startswith(READ_PREFIXES)


VENDOR_KEY = {"arista": "arista", "cisco": "cisco", "juniper": "juniper",
              "nokia": "nokia", "frr": "frr"}


def build(corpus_path: Path) -> dict:
    records = json.loads(corpus_path.read_text())
    library = []
    seen = set()  # dedup by (vendor, normalized-cmd)
    by_vendor: dict[str, int] = {}

    for r in records:
        vendor = (r.get("vendor") or "").strip()
        cmd_body = r.get("cmd") or ""
        fl = first_line(cmd_body)
        if not fl:
            continue
        kind = classify(cmd_body)
        # Keep only single-line *operational* (show/display/...) commands — the
        # runnable-interesting set. Drops multi-line tutorials and config-prompt
        # fragments ("Device# configure terminal") that add noise, not value.
        if kind != "show":
            continue
        key = (vendor.lower(), fl.lower())
        if key in seen:
            continue
        seen.add(key)
        vk = VENDOR_KEY.get(vendor.lower(), vendor.lower())
        runnable_on = []
        if vk == "arista":
            runnable_on = ["arista"]          # runs on cEOS
        elif vk == "cisco" and kind == "show":
            runnable_on = ["frr"]             # FRR mimics Cisco show
        library.append({
            "id": len(library),
            "vendor": vendor,
            "os": r.get("os", ""),
            "role": r.get("role", ""),
            "cat": r.get("cat") or "General",
            "title": (r.get("title") or fl)[:80],
            "cmd": fl,
            "desc": (r.get("desc") or "")[:160],
            "kind": kind,
            "read_only": is_read_only(fl),
            "runnable_on": runnable_on,
        })
        by_vendor[vendor] = by_vendor.get(vendor, 0) + 1

    curated = {}
    for vk, spec in CURATED.items():
        curated[vk] = {
            "label": spec["label"],
            "wrapper": spec["wrapper"],
            "commands": [
                {"cmd": c, "desc": d, "cat": cat, "read_only": is_read_only(c)}
                for (c, d, cat) in spec["commands"]
            ],
        }

    return {
        "generated": _stamp(),
        "source": f"gesh multivendor CLI corpus ({len(records)} records) — curated",
        "lab_targets": {
            "arista": {"wrapper": "Cli", "nodes": _nodes_for("eos")},
            "frr": {"wrapper": "vtysh", "nodes": _nodes_for("frr")},
            "nokia": {"wrapper": "sr_cli", "nodes": _nodes_for("srl")},
        },
        "curated": curated,
        "napalm_getters": [{"name": n, "desc": d} for n, d in NAPALM_GETTERS],
        "library": library,
        "stats": {
            "total": len(library),
            "by_vendor": by_vendor,
            "runnable": sum(1 for c in library if c["runnable_on"]),
            "read_only": sum(1 for c in library if c["read_only"]),
            "curated": sum(len(v["commands"]) for v in curated.values()),
        },
    }


def _nodes_for(driver: str) -> list[str]:
    try:
        from config import NODE_INDEX
        return [h for h, n in NODE_INDEX.items() if n["driver"] == driver]
    except Exception:  # noqa: BLE001 — corpus build must work without config import
        return []


def _stamp() -> str:
    # Date.now-free: read from env if provided (CI), else a static marker.
    return os.getenv("BUILD_STAMP", "generated")


if __name__ == "__main__":
    corpus = Path(os.getenv("CORPUS", str(DEFAULT_CORPUS)))
    if not corpus.exists():
        raise SystemExit(f"corpus not found: {corpus}\nset CORPUS=/path/to/commands.json")
    catalog = build(corpus)
    OUT.write_text(json.dumps(catalog, indent=2))
    s = catalog["stats"]
    print(f"wrote {OUT}")
    print(f"  library={s['total']}  runnable={s['runnable']}  "
          f"read_only={s['read_only']}  curated={s['curated']}")
    print(f"  by_vendor={s['by_vendor']}")
