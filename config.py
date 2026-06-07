#!/usr/bin/env python3
"""Central configuration for the NAPALM Network Dashboard.

Repointed 2026-06-06 from unreachable ex-company production sites to the LIVE
local containerlab fabrics running on this host. The previous version hardcoded
an ex-employer NetBox token in source — that has been removed. NEVER hardcode
secrets here; read them from the environment.

Live targets:
  * CLOS-EVPN  — 3 spine + 6 leaf, multivendor (Arista cEOS / Nokia SR Linux / FRR)
  * 3-TIER     — core / edge / dist FRR routers (DE-FRA / UK-LON / NL-AMS / US-NYC)

Collection path: a `napalm-runner` sidecar container attached to the lab
management networks runs the real NAPALM drivers (eos via eAPI, srl via the
napalm-srl community driver). FRR nodes have no NAPALM driver and are collected
via `docker exec ... vtysh` — surfaced honestly in the coverage matrix.
"""

import os
from pathlib import Path

# ── SSH (legacy direct-connect; unused for the docker lab) ──────────────────────
SSH_USER    = os.getenv("NAPALM_SSH_USER", "admin")
SSH_KEY     = os.getenv("SSH_KEY", "")
SSH_TIMEOUT = int(os.getenv("SSH_TIMEOUT", "30"))

# ── NetBox (optional, env-only — no secrets in source) ──────────────────────────
NETBOX_URL   = os.getenv("NETBOX_URL", "")
NETBOX_TOKEN = os.getenv("NETBOX_TOKEN", "")   # scrubbed: read from env, never hardcode
ZTASID       = os.getenv("ZTASID", "")

# ── NAPALM driver / capability map ──────────────────────────────────────────────
# 'none' = no NAPALM driver exists (FRR) → docker-exec fallback.
DRIVER_MAP = {"junos": "junos", "eos": "eos", "srl": "srl", "frr": "none"}

# Per-driver NAPALM-support truth — drives the coverage matrix headline.
NAPALM_SUPPORT = {
    "eos":  {"napalm": True,  "package": "napalm (core)",          "transport": "eAPI / HTTPS"},
    "srl":  {"napalm": True,  "package": "napalm-srl (community)",  "transport": "JSON-RPC / HTTPS"},
    "junos":{"napalm": True,  "package": "napalm (core)",          "transport": "NETCONF / SSH"},
    "none": {"napalm": False, "package": "—",                       "transport": "docker exec vtysh"},
}

# Standard getters probed for every node.
STANDARD_GETTERS = [
    "get_facts", "get_interfaces", "get_interfaces_ip",
    "get_bgp_neighbors", "get_lldp_neighbors", "get_environment",
]

# ── LIVE LAB FABRICS ────────────────────────────────────────────────────────────
FABRICS = {
    "clos": {
        "name": "CLOS-EVPN Fabric",
        "kind": "clos",
        "mgmt_subnet": "172.20.20.0/24",
        "tiers": ["spine", "leaf"],
        "nodes": {
            "spine1": {"container": "clab-clos-evpn-spine1", "ip": "172.20.20.11", "vendor": "nokia",  "driver": "srl", "tier": "spine", "model": "SR Linux ixrd3l"},
            "spine2": {"container": "clab-clos-evpn-spine2", "ip": "172.20.20.12", "vendor": "arista", "driver": "eos", "tier": "spine", "model": "cEOS 4.33.1F"},
            "spine3": {"container": "clab-clos-evpn-spine3", "ip": "172.20.20.13", "vendor": "frr",    "driver": "frr", "tier": "spine", "model": "FRR"},
            "leaf1":  {"container": "clab-clos-evpn-leaf1",  "ip": "172.20.20.21", "vendor": "arista", "driver": "eos", "tier": "leaf",  "model": "cEOS 4.33.1F"},
            "leaf2":  {"container": "clab-clos-evpn-leaf2",  "ip": "172.20.20.22", "vendor": "nokia",  "driver": "srl", "tier": "leaf",  "model": "SR Linux ixrd3l"},
            "leaf3":  {"container": "clab-clos-evpn-leaf3",  "ip": "172.20.20.23", "vendor": "frr",    "driver": "frr", "tier": "leaf",  "model": "FRR"},
            "leaf4":  {"container": "clab-clos-evpn-leaf4",  "ip": "172.20.20.24", "vendor": "arista", "driver": "eos", "tier": "leaf",  "model": "cEOS 4.33.1F"},
            "leaf5":  {"container": "clab-clos-evpn-leaf5",  "ip": "172.20.20.25", "vendor": "nokia",  "driver": "srl", "tier": "leaf",  "model": "SR Linux ixrd3l"},
            "leaf6":  {"container": "clab-clos-evpn-leaf6",  "ip": "172.20.20.26", "vendor": "frr",    "driver": "frr", "tier": "leaf",  "model": "FRR"},
        },
    },
    "dcn": {
        "name": "3-Tier Network",
        "kind": "3tier",
        "mgmt_subnet": "10.200.0.0/24",
        "tiers": ["core", "edge", "dist"],
        "nodes": {
            "de-fra-core-01": {"container": "de-fra-core-01", "ip": "10.200.0.11", "vendor": "frr", "driver": "frr", "tier": "core", "model": "FRR", "site": "DE-FRA"},
            "de-fra-core-02": {"container": "de-fra-core-02", "ip": "10.200.0.12", "vendor": "frr", "driver": "frr", "tier": "core", "model": "FRR", "site": "DE-FRA"},
            "uk-lon-core-01": {"container": "uk-lon-core-01", "ip": "10.200.0.13", "vendor": "frr", "driver": "frr", "tier": "core", "model": "FRR", "site": "UK-LON"},
            "nl-ams-core-01": {"container": "nl-ams-core-01", "ip": "10.200.0.14", "vendor": "frr", "driver": "frr", "tier": "core", "model": "FRR", "site": "NL-AMS"},
            "us-nyc-core-01": {"container": "us-nyc-core-01", "ip": "10.200.0.15", "vendor": "frr", "driver": "frr", "tier": "core", "model": "FRR", "site": "US-NYC"},
            "de-fra-edge-01": {"container": "de-fra-edge-01", "ip": "10.200.0.21", "vendor": "frr", "driver": "frr", "tier": "edge", "model": "FRR", "site": "DE-FRA"},
            "uk-lon-edge-01": {"container": "uk-lon-edge-01", "ip": "10.200.0.22", "vendor": "frr", "driver": "frr", "tier": "edge", "model": "FRR", "site": "UK-LON"},
            "nl-ams-edge-01": {"container": "nl-ams-edge-01", "ip": "10.200.0.23", "vendor": "frr", "driver": "frr", "tier": "edge", "model": "FRR", "site": "NL-AMS"},
            "uk-lon-dist-01": {"container": "uk-lon-dist-01", "ip": "10.200.0.31", "vendor": "frr", "driver": "frr", "tier": "dist", "model": "FRR", "site": "UK-LON"},
            "de-fra-dist-01": {"container": "de-fra-dist-01", "ip": "10.200.0.33", "vendor": "frr", "driver": "frr", "tier": "dist", "model": "FRR", "site": "DE-FRA"},
        },
    },
}

# Flat node index: hostname -> node dict (+ fabric id). Used by the lab collector.
NODE_INDEX = {}
for _fid, _fab in FABRICS.items():
    for _name, _nd in _fab["nodes"].items():
        NODE_INDEX[_name] = {**_nd, "fabric": _fid, "hostname": _name}

# Backwards-compat: legacy code imports SITES as {site: {host: {ip, driver}}}.
SITES = {
    fid: {n: {"ip": nd["ip"], "driver": nd["driver"]} for n, nd in fab["nodes"].items()}
    for fid, fab in FABRICS.items()
}

# ── Lab runner (collector sidecar on the docker mgmt networks) ───────────────────
RUNNER_CONTAINER = os.getenv("NAPALM_RUNNER", "napalm-runner")
EOS_USER = os.getenv("NAPALM_EOS_USER", "admin")
EOS_PASS = os.getenv("NAPALM_EOS_PASS", "admin")
SRL_USER = os.getenv("NAPALM_SRL_USER", "admin")
SRL_PASS = os.getenv("NAPALM_SRL_PASS", "NokiaSrl1!")

# ── Output directory ─────────────────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
