#!/usr/bin/env python3
"""Built-in check library — reusable, data-only checks mirroring the lab's tools.

Each entry is a plain dict (checks.from_dict turns it into a Check). Suites can
reference these by composing the same dicts. The field paths resolve against the
per-node dict returned by napalm_lab.collect_node (reachable / method / facts /
getters / data).
"""

from __future__ import annotations

BUILTIN_CHECKS: list[dict] = [
    {
        "id": "all-reachable",
        "name": "All nodes reachable",
        "target": {"all": True},
        "source": {"node": True},
        "field": "reachable", "op": "eq", "expected": True,
        "quantifier": "value", "severity": "high",
        "description": "Every node answers management-plane collection.",
    },
    {
        "id": "facts-ok",
        "name": "get_facts succeeds",
        "target": {"all": True},
        "source": {"getter": "get_facts"},
        "field": "getters.get_facts.ok", "op": "eq", "expected": True,
        "quantifier": "value", "severity": "high",
        "description": "Facts collection works on every node.",
    },
    {
        "id": "os-version-present",
        "name": "OS version present",
        "target": {"all": True},
        "source": {"getter": "get_facts"},
        "field": "facts.os_version", "op": "nonempty",
        "quantifier": "value", "severity": "low",
        "description": "Each node reports a non-empty OS version.",
    },
    {
        "id": "bgp-peers-present",
        "name": "Has at least one BGP peer",
        "target": {"driver": ["eos", "frr"]},
        "source": {"getter": "get_bgp_neighbors"},
        "field": "data.get_bgp_neighbors.global.peers.*", "op": "gte", "expected": 1,
        "quantifier": "count", "severity": "med",
        "description": "Each routing node has BGP peers configured (eos/frr; srl getter is a known napalm-srl gap).",
    },
    {
        "id": "bgp-all-up",
        "name": "All BGP peers established",
        "target": {"driver": ["eos", "frr"]},
        "source": {"getter": "get_bgp_neighbors"},
        "field": "data.get_bgp_neighbors.global.peers.*.is_up", "op": "eq", "expected": True,
        "quantifier": "all", "severity": "high",
        "description": "Every BGP session on eos/frr nodes is up.",
    },
    {
        "id": "napalm-native",
        "name": "NAPALM-native collection",
        "target": {"driver": ["eos", "srl"]},
        "source": {"node": True},
        "field": "method", "op": "eq", "expected": "napalm",
        "quantifier": "value", "severity": "low",
        "description": "eos/srl nodes are collected by a real NAPALM driver, not exec fallback.",
    },
    {
        "id": "version-via-intent",
        "name": "Version command runs (universal)",
        "target": {"all": True},
        "source": {"intent": "version"},
        "field": "ok", "op": "eq", "expected": True,
        "quantifier": "value", "severity": "med",
        "description": "The universal 'version' intent runs the right command on every vendor.",
    },
]


def builtin_index() -> dict:
    return {c["id"]: c for c in BUILTIN_CHECKS}
