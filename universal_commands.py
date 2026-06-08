#!/usr/bin/env python3
"""Universal commands — one logical intent, the correct CLI per vendor.

The Command Console can run any command, but a command is vendor-specific: `show
running-config` is an error on Nokia SR Linux, `show ip bgp summary` differs from
`show network-instance default protocols bgp neighbor`. This module makes a small
set of common operational *intents* (version, bgp, interfaces, routes, …) work on
EVERY device by mapping each intent to the right command for the node's driver.

The per-vendor command tables are vendored from the gesh multivendor driver layer
(`DCN_Network_Tool/src/drivers/commands.py`, the single source of truth) so this
tool stays self-contained. Order matters within a list: the first command that
returns useful output wins (JSON variants lead, text fallbacks follow).
"""

from __future__ import annotations

# Maps the lab's NAPALM driver code -> the driver-layer's canonical vendor key.
DRIVER_TO_VENDOR = {"eos": "arista-eos", "frr": "frr", "srl": "nokia-srl",
                    "junos": "junos"}

# intent -> {canonical_vendor: [commands, json-first then text fallback]}
INTENTS: dict[str, dict] = {
    "version": {
        "label": "Version / facts", "cat": "Facts",
        "cmd": {
            "arista-eos": ["show version | json", "show version"],
            "frr": ["show version"],
            "nokia-srl": ["info from state /system information version"],
            "junos": ["show version | display json", "show version"],
        },
    },
    "bgp": {
        "label": "BGP summary", "cat": "BGP",
        "cmd": {
            "arista-eos": ["show ip bgp summary | json", "show ip bgp summary"],
            "frr": ["show ip bgp summary json", "show ip bgp summary"],
            "nokia-srl": ["show network-instance default protocols bgp neighbor"],
            "junos": ["show bgp summary | display json", "show bgp summary"],
        },
    },
    "ospf": {
        "label": "OSPF neighbors", "cat": "Routing",
        "cmd": {
            "arista-eos": ["show ip ospf neighbor | json", "show ip ospf neighbor"],
            "frr": ["show ip ospf neighbor json", "show ip ospf neighbor"],
            "nokia-srl": ["show network-instance default protocols ospf neighbor"],
            "junos": ["show ospf neighbor | display json", "show ospf neighbor"],
        },
    },
    "interfaces": {
        "label": "Interfaces", "cat": "Interfaces",
        "cmd": {
            "arista-eos": ["show interfaces status | json", "show interfaces status"],
            "frr": ["show interface brief json", "show interface brief"],
            "nokia-srl": ["show interface"],
            "junos": ["show interfaces terse | display json", "show interfaces terse"],
        },
    },
    "interface_counters": {
        "label": "Interface counters", "cat": "Interfaces",
        "cmd": {
            "arista-eos": ["show interfaces counters | json", "show interfaces counters"],
            "frr": ["show interface json", "show interface"],
            "nokia-srl": ["show interface detail", "show interface"],
            "junos": ["show interfaces extensive | display json", "show interfaces extensive"],
        },
    },
    "routes": {
        "label": "Route summary", "cat": "Routing",
        "cmd": {
            "arista-eos": ["show ip route summary | json", "show ip route summary"],
            "frr": ["show ip route summary json", "show ip route summary"],
            "nokia-srl": ["show network-instance default route-table ipv4-unicast summary"],
            "junos": ["show route summary | display json", "show route summary"],
        },
    },
    "memory": {
        "label": "Memory", "cat": "System",
        "cmd": {
            "arista-eos": ["show processes top once | json", "show version | json"],
            "frr": ["show memory", "show memory summary"],
            "nokia-srl": ["info from state /platform", "show platform"],
            "junos": ["show system memory | display json", "show system memory"],
        },
    },
    "cpu": {
        "label": "CPU", "cat": "System",
        "cmd": {
            "arista-eos": ["show processes top once | json", "show version | json"],
            "frr": ["show thread cpu"],
            "nokia-srl": ["info from state /platform", "show platform"],
            "junos": ["show system processes extensive"],
        },
    },
}


def commands_for(driver: str, intent: str) -> list[str]:
    """Return the ordered command candidates for an intent on a driver, or []."""
    spec = INTENTS.get(intent)
    if not spec:
        return []
    vendor = DRIVER_TO_VENDOR.get((driver or "").strip().lower())
    if not vendor:
        return []
    return list(spec["cmd"].get(vendor, []))


def intent_list() -> list[dict]:
    """Catalog-shaped list of intents for the UI."""
    return [{"intent": k, "label": v["label"], "cat": v["cat"]}
            for k, v in INTENTS.items()]
