#!/usr/bin/env python3
"""Hermetic pytest suite for the NAPALM live-lab backend.

These tests NEVER touch a real `docker` binary or a live container. The single
side-effecting primitive — ``napalm_lab._docker_exec`` — is monkeypatched to
return canned outputs, so every test is deterministic and offline.

Run:  pytest -q tests/test_napalm_lab.py

Shapes asserted here are matched against the real source in:
  * config.py      -> FABRICS, NODE_INDEX, NAPALM_SUPPORT, STANDARD_GETTERS, SITES
  * napalm_lab.py  -> collect_node, collect_fabric_parallel, napalm_matrix,
                      lab_topology, lab_collect_device, lab_collect_parallel,
                      _docker_exec
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

# ── make `import config` / `import napalm_lab` work no matter the CWD ─────────────
PKG_DIR = Path(__file__).resolve().parent.parent  # .../napalm_network
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

import config  # noqa: E402
import napalm_lab  # noqa: E402

# Every test in this module is offline/hermetic.
pytestmark = pytest.mark.hermetic


# ════════════════════════════════════════════════════════════════════════════════
# 1. Security / config
# ════════════════════════════════════════════════════════════════════════════════

class TestSecurityAndConfig:
    def test_node_counts_per_fabric(self):
        clos = config.FABRICS["clos"]["nodes"]
        dcn = config.FABRICS["dcn"]["nodes"]
        assert len(clos) == 9, f"expected 9 clos nodes, got {len(clos)}"
        assert len(dcn) == 10, f"expected 10 dcn nodes, got {len(dcn)}"

    def test_node_index_total_is_19(self):
        assert len(config.NODE_INDEX) == 19, (
            f"NODE_INDEX should flatten to 9+10=19 nodes, got {len(config.NODE_INDEX)}"
        )

    def test_node_index_is_union_of_fabrics(self):
        expected = set(config.FABRICS["clos"]["nodes"]) | set(config.FABRICS["dcn"]["nodes"])
        assert set(config.NODE_INDEX) == expected, "NODE_INDEX keys must match all fabric node names"

    def test_no_hardcoded_secret_in_config_source(self):
        src = Path(config.__file__).read_text(encoding="utf-8")
        lowered = src.lower()
        assert "acronis.work" not in lowered, "config.py must not reference acronis.work"
        assert "acronis" not in lowered, "config.py must not contain an Acronis token reference"
        # No long hex blob that looks like a leaked API token (40+ hex chars).
        hex_blobs = re.findall(r"\b[0-9a-fA-F]{40,}\b", src)
        assert not hex_blobs, f"config.py contains a long hex token-like string: {hex_blobs!r}"

    def test_netbox_token_comes_from_env_and_defaults_empty(self, monkeypatch):
        monkeypatch.delenv("NETBOX_TOKEN", raising=False)
        import importlib
        reloaded = importlib.reload(config)
        try:
            assert reloaded.NETBOX_TOKEN == "", "NETBOX_TOKEN must default to empty (env-only)"
            monkeypatch.setenv("NETBOX_TOKEN", "from-env-xyz")
            reloaded2 = importlib.reload(config)
            assert reloaded2.NETBOX_TOKEN == "from-env-xyz", "NETBOX_TOKEN must be read from env"
        finally:
            # Restore module state and the napalm_lab references to it.
            monkeypatch.delenv("NETBOX_TOKEN", raising=False)
            importlib.reload(config)
            importlib.reload(napalm_lab)

    def test_every_node_has_required_keys(self):
        required = {"container", "ip", "vendor", "driver", "tier"}
        for name, nd in config.NODE_INDEX.items():
            missing = required - set(nd)
            assert not missing, f"node {name!r} missing keys {missing}"
            # flattened index also carries fabric + hostname
            assert nd["fabric"] in config.FABRICS, f"node {name!r} has bad fabric {nd['fabric']!r}"
            assert nd["hostname"] == name, f"node {name!r} hostname mismatch: {nd['hostname']!r}"


# ════════════════════════════════════════════════════════════════════════════════
# 2. Driver / vendor mapping
# ════════════════════════════════════════════════════════════════════════════════

class TestDriverVendorMapping:
    @pytest.mark.parametrize(
        "vendor,driver",
        [("arista", "eos"), ("nokia", "srl"), ("frr", "frr")],
    )
    def test_vendor_to_driver(self, vendor, driver):
        nodes_for_vendor = [nd for nd in config.NODE_INDEX.values() if nd["vendor"] == vendor]
        assert nodes_for_vendor, f"no {vendor} nodes found in NODE_INDEX"
        for nd in nodes_for_vendor:
            assert nd["driver"] == driver, (
                f"{vendor} node {nd['hostname']!r} should map to driver {driver!r}, got {nd['driver']!r}"
            )

    def test_napalm_support_truth_table(self):
        # eos + srl are napalm-capable; the FRR fallback maps driver 'frr' -> 'none'.
        assert config.NAPALM_SUPPORT["eos"]["napalm"] is True
        assert config.NAPALM_SUPPORT["srl"]["napalm"] is True
        assert config.NAPALM_SUPPORT["none"]["napalm"] is False
        # DRIVER_MAP collapses the FRR pseudo-driver onto the unsupported 'none' entry.
        assert config.DRIVER_MAP["frr"] == "none"
        assert config.DRIVER_MAP["eos"] == "eos"
        assert config.DRIVER_MAP["srl"] == "srl"


# ════════════════════════════════════════════════════════════════════════════════
# Canned _docker_exec fakes  (so collect_node is hermetic)
# ════════════════════════════════════════════════════════════════════════════════

def _eos_runner_json(node: dict, getters: list[str]) -> str:
    """A good single-JSON-line response as the runner's collect.py would emit."""
    gstatus = {g: {"ok": True, "error": None} for g in getters}
    facts = {
        "hostname": node["hostname"], "vendor": "Arista", "model": "cEOS",
        "os_version": "4.33.1F", "serial_number": "ABC", "uptime": 123456,
        "interface_list": ["Ethernet1"],
    }
    data = {g: {} for g in getters}
    data["get_facts"] = facts
    payload = {
        "ok": True, "reachable": True, "method": "napalm", "driver": "eos",
        "facts": facts, "data": data, "getters": gstatus, "error": None,
    }
    # Prefix with a warning line to exercise the "last JSON line" parser.
    return "WARNING: insecure eAPI\n" + json.dumps(payload) + "\n"


def make_fake_docker_exec(reachable_frr_containers=None):
    """Build a fake _docker_exec dispatching on container name + argv.

    eos  -> runner emits good JSON (method napalm)
    frr  -> vtysh json for `show bgp summary` / `show interface`
    srl  -> runner returns unreachable (so collect_node surfaces a non-napalm result)
    unreachable nodes -> rc != 0
    """
    reachable_frr = reachable_frr_containers

    def fake(container: str, argv: list[str], timeout: int = 30):
        joined = " ".join(argv)

        # ── runner sidecar (eos + srl go through here) ──
        if container == config.RUNNER_CONTAINER:
            try:
                payload = json.loads(argv[-1])
            except Exception:
                return 1, "", "bad payload"
            driver = payload.get("driver")
            ip = payload.get("ip", "")
            getters = payload.get("getters", [])
            # Resolve the node from its IP for a realistic facts block.
            node = next((n for n in config.NODE_INDEX.values() if n["ip"] == ip), None)
            if driver == "eos" and node is not None:
                return 0, _eos_runner_json(node, getters), ""
            if driver == "srl":
                # Simulate srl runner failing (e.g. JSON-RPC unreachable in CI).
                return 1, "", "srl unreachable: connection refused"
            return 1, "", "unknown driver"

        # ── FRR containers via vtysh ──
        if argv[:1] == ["vtysh"]:
            # Decide reachability of this FRR container.
            up = True if reachable_frr is None else (container in reachable_frr)
            if not up:
                return 1, "", "Error connecting to vtysh"
            if "show version" in joined:
                return 0, "FRRouting 9.1 (de-fra-core-01).\nCopyright 1996-2005\n", ""
            if "show bgp summary" in joined:
                bgp = {
                    "ipv4Unicast": {
                        "peers": {
                            "10.200.0.12": {
                                "state": "Established", "peerUptimeMsec": 99999,
                                "hostname": "de-fra-core-02", "remoteAs": 65002,
                                "pfxRcd": 5, "pfxSnt": 4,
                            },
                            "10.200.0.13": {
                                "state": "Active", "peerUptimeMsec": 0,
                                "hostname": "uk-lon-core-01", "remoteAs": 65003,
                                "pfxRcd": 0, "pfxSnt": 0,
                            },
                        }
                    }
                }
                return 0, json.dumps(bgp), ""
            if "show interface" in joined:
                ifs = {
                    "eth0": {
                        "administrativeStatus": "up", "operationalStatus": "up",
                        "description": "uplink", "speed": 1000,
                        "hardwareAddress": "aa:bb:cc:dd:ee:ff", "mtu": 1500,
                        "ipAddresses": [{"address": "10.200.0.11/24"}],
                    }
                }
                return 0, json.dumps(ifs), ""
            return 0, "{}", ""

        # ── cEOS exec fallback `Cli -c "show version | json"` ──
        if argv[:1] == ["Cli"]:
            v = {"version": "4.33.1F", "modelName": "cEOS", "serialNumber": "ABC", "uptime": 42}
            return 0, json.dumps(v), ""

        return 1, "", f"unhandled exec: {container} {joined}"

    return fake


@pytest.fixture
def patch_docker(monkeypatch):
    """Default hermetic docker: all FRR reachable, eos via runner, srl down."""
    monkeypatch.setattr(napalm_lab, "_docker_exec", make_fake_docker_exec())
    return monkeypatch


# ════════════════════════════════════════════════════════════════════════════════
# 3. Backend logic with NO real docker
# ════════════════════════════════════════════════════════════════════════════════

class TestCollectNodeHermetic:
    RICH_KEYS = {
        "hostname", "container", "ip", "fabric", "tier", "vendor", "model",
        "driver", "napalm_supported", "napalm_package", "transport",
        "method", "reachable", "latency_ms", "facts", "getters", "data", "error",
    }

    def test_eos_node_is_napalm_method_when_runner_succeeds(self, patch_docker):
        r = napalm_lab.collect_node("leaf1")  # arista / eos
        assert self.RICH_KEYS <= set(r), f"missing keys: {self.RICH_KEYS - set(r)}"
        assert r["driver"] == "eos"
        assert r["napalm_supported"] is True
        assert r["method"] == "napalm", "eos via successful runner must report method 'napalm'"
        assert r["reachable"] is True
        assert r["facts"]["os_version"] == "4.33.1F"
        assert r["getters"]["get_facts"]["ok"] is True

    def test_frr_node_is_exec_method_and_unsupported(self, patch_docker):
        r = napalm_lab.collect_node("de-fra-core-01")  # frr
        assert self.RICH_KEYS <= set(r)
        assert r["driver"] == "frr"
        assert r["napalm_supported"] is False, "FRR has no NAPALM driver"
        assert r["method"] == "exec", "FRR is collected via docker exec vtysh"
        assert r["reachable"] is True
        # one peer Established, one Active -> get_bgp_neighbors ok with peers present
        peers = r["data"]["get_bgp_neighbors"]["global"]["peers"]
        assert "10.200.0.12" in peers
        assert peers["10.200.0.12"]["is_up"] is True
        assert peers["10.200.0.13"]["is_up"] is False
        assert r["getters"]["get_bgp_neighbors"]["ok"] is True
        # FRR honestly reports no LLDP / environment
        assert r["getters"]["get_lldp_neighbors"]["ok"] is False
        assert r["getters"]["get_environment"]["ok"] is False

    def test_srl_runner_failure_surfaces_unreachable(self, patch_docker):
        # srl has no eos exec fallback, so a failed runner stays unreachable.
        r = napalm_lab.collect_node("spine1")  # nokia / srl
        assert r["driver"] == "srl"
        assert r["napalm_supported"] is True
        assert r["reachable"] is False
        assert r["error"], "failed srl runner should carry an error string"

    def test_eos_falls_back_to_exec_when_runner_down(self, monkeypatch):
        # Runner fails for eos too -> _ceos_exec_fallback kicks in via `Cli`.
        def fake(container, argv, timeout=30):
            if container == config.RUNNER_CONTAINER:
                return 1, "", "runner down"
            if argv[:1] == ["Cli"]:
                v = {"version": "4.33.1F", "modelName": "cEOS", "serialNumber": "ABC", "uptime": 42}
                return 0, json.dumps(v), ""
            return 1, "", "x"
        monkeypatch.setattr(napalm_lab, "_docker_exec", fake)
        r = napalm_lab.collect_node("leaf1")  # eos
        assert r["reachable"] is True
        assert r["method"] == "exec", "eAPI-down eos should degrade to exec fallback"
        assert r["facts"]["os_version"] == "4.33.1F"
        assert r["getters"]["get_facts"]["ok"] is True

    def test_unreachable_frr_node(self, monkeypatch):
        monkeypatch.setattr(napalm_lab, "_docker_exec", make_fake_docker_exec(reachable_frr_containers=set()))
        r = napalm_lab.collect_node("de-fra-core-01")
        assert r["reachable"] is False
        assert r["method"] == "exec"
        assert r["error"], "unreachable FRR must report an error"
        for g in config.STANDARD_GETTERS:
            assert r["getters"][g]["ok"] is False

    def test_unknown_node(self):
        r = napalm_lab.collect_node("does-not-exist")
        assert r["reachable"] is False
        assert r["error"] == "unknown node"


# ════════════════════════════════════════════════════════════════════════════════
# 4. Matrix math
# ════════════════════════════════════════════════════════════════════════════════

class TestMatrixMath:
    def test_matrix_summary_totals_add_up(self, patch_docker):
        m = napalm_lab.napalm_matrix("all")
        s = m["summary"]
        nodes = m["nodes"]

        assert s["total"] == len(nodes) == 19
        # native + fallback counts only count reachable nodes; cannot exceed reachable.
        assert s["napalm_native"] + s["exec_fallback"] == s["reachable"], (
            "every reachable node is either napalm_native or exec_fallback"
        )
        assert s["reachable"] <= s["total"]

        # by_driver counts sum to total
        assert sum(s["by_driver"].values()) == s["total"]
        # there are 13 frr nodes (3 clos + 10 dcn), 3 eos, 3 srl
        assert s["by_driver"].get("frr") == 13
        assert s["by_driver"].get("eos") == 3
        assert s["by_driver"].get("srl") == 3

        # getter_support totals must equal node count for every getter
        for g, gs in s["getter_support"].items():
            assert gs["total"] == s["total"], f"getter {g} total {gs['total']} != {s['total']}"
            assert 0 <= gs["ok"] <= gs["total"]

    def test_matrix_native_vs_fallback_split(self, patch_docker):
        # default fake: 3 eos via runner (napalm), 13 frr via exec, srl(3) unreachable.
        m = napalm_lab.napalm_matrix("all")
        s = m["summary"]
        assert s["napalm_native"] == 3, "only the 3 eos nodes go native in the default fake"
        assert s["exec_fallback"] == 13, "13 FRR nodes use exec fallback"
        assert s["reachable"] == 16, "16 reachable (3 eos + 13 frr); srl down"

    def test_matrix_clos_only(self, patch_docker):
        m = napalm_lab.napalm_matrix("clos")
        assert m["summary"]["total"] == 9
        assert m["fabric"] == "clos"
        assert set(m["getters"]) == set(config.STANDARD_GETTERS)

    def test_matrix_via_monkeypatched_collect_node(self, monkeypatch):
        """Exercise the math independently of collect_node internals."""
        def fake_collect(hostname, getters=None):
            getters = getters or config.STANDARD_GETTERS
            nd = config.NODE_INDEX[hostname]
            method = "napalm" if nd["driver"] in ("eos", "srl") else "exec"
            return {
                "hostname": hostname, "driver": nd["driver"], "reachable": True,
                "method": method,
                "getters": {g: {"ok": True, "error": None} for g in getters},
                "data": {}, "facts": {},
            }
        monkeypatch.setattr(napalm_lab, "collect_node", fake_collect)
        m = napalm_lab.napalm_matrix("all")
        s = m["summary"]
        assert s["total"] == 19
        assert s["reachable"] == 19
        assert s["napalm_native"] == 6, "3 eos + 3 srl are napalm-method here"
        assert s["exec_fallback"] == 13
        for gs in s["getter_support"].values():
            assert gs["ok"] == 19 and gs["total"] == 19


# ════════════════════════════════════════════════════════════════════════════════
# 5. Topology + legacy shims
# ════════════════════════════════════════════════════════════════════════════════

class TestTopologyAndLegacy:
    def test_clos_topology_full_spine_leaf_mesh(self, patch_docker):
        t = napalm_lab.lab_topology("clos")
        assert t["fabric"] == "clos"
        assert t["name"] == "CLOS-EVPN Fabric"
        assert t["tiers"] == ["spine", "leaf"]
        assert len(t["nodes"]) == 9
        # 3 spines * 6 leaves = 18 designed links
        assert len(t["links"]) == 18, f"expected full 3x6 Clos mesh = 18, got {len(t['links'])}"
        spines = {n["id"] for n in t["nodes"] if n["tier"] == "spine"}
        leaves = {n["id"] for n in t["nodes"] if n["tier"] == "leaf"}
        assert len(spines) == 3 and len(leaves) == 6
        for link in t["links"]:
            assert link["source"] in spines and link["target"] in leaves

    def test_dcn_topology_has_documented_links(self, patch_docker):
        t = napalm_lab.lab_topology("dcn")
        assert t["fabric"] == "dcn"
        assert len(t["nodes"]) == 10
        assert len(t["links"]) == 10, f"dcn has 10 documented links, got {len(t['links'])}"
        names = set(config.FABRICS["dcn"]["nodes"])
        for link in t["links"]:
            assert link["source"] in names and link["target"] in names

    def test_topology_node_shape(self, patch_docker):
        t = napalm_lab.lab_topology("clos")
        n = t["nodes"][0]
        for key in ("id", "tier", "vendor", "driver", "model", "up", "method",
                    "napalm_supported", "bgp_up", "bgp_total"):
            assert key in n, f"topology node missing {key!r}"

    def test_topology_unknown_fabric(self):
        t = napalm_lab.lab_topology("nope")
        assert t["nodes"] == [] and t["links"] == []
        assert t.get("error") == "unknown fabric"

    def test_lab_collect_device_legacy_shape(self, patch_docker):
        r = napalm_lab.lab_collect_device("leaf1", "172.20.20.21", "eos", config.STANDARD_GETTERS)
        assert set(r) == {"ip", "driver", "data", "error"}, "legacy shape must be exactly these 4 keys"
        assert r["ip"] == "172.20.20.21"
        assert r["driver"] == "eos"
        assert isinstance(r["data"], dict)

    def test_lab_collect_parallel_legacy_shape(self, patch_docker):
        devices = {
            "leaf1": {"ip": "172.20.20.21", "driver": "eos"},
            "de-fra-core-01": {"ip": "10.200.0.11", "driver": "frr"},
        }
        out = napalm_lab.lab_collect_parallel(devices, config.STANDARD_GETTERS)
        assert set(out) == set(devices), "result keyed by hostname"
        for host, rec in out.items():
            assert set(rec) == {"ip", "driver", "data", "error"}, f"{host} bad legacy shape: {set(rec)}"
            assert rec["ip"] == devices[host]["ip"]
            assert rec["driver"] == devices[host]["driver"]


# ════════════════════════════════════════════════════════════════════════════════
# Parallel collector sanity
# ════════════════════════════════════════════════════════════════════════════════

class TestParallelCollector:
    def test_collect_fabric_parallel_preserves_order_and_count(self, patch_docker):
        rows = napalm_lab.collect_fabric_parallel("dcn")
        assert len(rows) == 10
        assert [r["hostname"] for r in rows] == list(config.FABRICS["dcn"]["nodes"]), (
            "collect_fabric_parallel must return rows in fabric node order"
        )

    def test_collect_fabric_parallel_all(self, patch_docker):
        rows = napalm_lab.collect_fabric_parallel("all")
        assert len(rows) == 19
        assert {r["hostname"] for r in rows} == set(config.NODE_INDEX)
