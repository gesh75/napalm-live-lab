#!/usr/bin/env python3
"""Live containerlab collection backend for the NAPALM Dashboard.

The host (macOS Docker Desktop) cannot route to container management IPs, so real
NAPALM runs inside a `napalm-runner` sidecar that sits on the lab management
networks. This module dispatches collection by driver:

  * eos  -> napalm (core)         via the runner (eAPI/HTTPS); exec fallback if eAPI down
  * srl  -> napalm-srl (community) via the runner (JSON-RPC/HTTPS)
  * frr  -> NO NAPALM driver      -> docker exec vtysh (surfaced honestly)

All collection is `docker exec` based, so it works identically on macOS and Linux.
Results are returned in two shapes:
  * legacy:  {"ip","driver","data":{getter:...},"error"}  (for the classic tools)
  * matrix:  rich per-node dict with napalm support + per-getter pass/fail
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from config import (
    FABRICS, NODE_INDEX, NAPALM_SUPPORT, STANDARD_GETTERS,
    RUNNER_CONTAINER, EOS_USER, EOS_PASS, SRL_USER, SRL_PASS,
)

def _find_docker() -> str:
    """Resolve the docker binary. launchd agents run with a minimal PATH that
    omits /usr/local/bin and Docker Desktop's bin dir, so fall back to known paths."""
    import os
    found = shutil.which("docker")
    if found:
        return found
    for cand in ("/usr/local/bin/docker", "/opt/homebrew/bin/docker",
                 os.path.expanduser("~/.docker/bin/docker"), "/usr/bin/docker"):
        if os.path.exists(cand):
            return cand
    return "docker"


DOCKER = os.getenv("DOCKER_BIN") or _find_docker()
EXEC_TIMEOUT = 30


# ── low-level docker exec ────────────────────────────────────────────────────────

def _docker_exec(container: str, argv: list[str], timeout: int = EXEC_TIMEOUT) -> tuple[int, str, str]:
    """Run `docker exec <container> <argv...>`; return (rc, stdout, stderr)."""
    try:
        p = subprocess.run(
            [DOCKER, "exec", container, *argv],
            capture_output=True, text=True, timeout=timeout,
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001 — surface any docker error to the caller
        return 1, "", str(e)


def runner_available() -> bool:
    """True if the napalm-runner sidecar is up."""
    rc, out, _ = _docker_exec(RUNNER_CONTAINER, ["python3", "-c", "print('ok')"], timeout=8)
    return rc == 0 and "ok" in out


# ── real NAPALM via the runner sidecar (eos, srl) ────────────────────────────────

def _runner_collect(node: dict, getters: list[str]) -> dict:
    """Invoke the runner's collect.py for a napalm-supported node."""
    driver = node["driver"]
    creds = (EOS_USER, EOS_PASS) if driver == "eos" else (SRL_USER, SRL_PASS)
    payload = json.dumps({
        "ip": node["ip"], "driver": driver, "getters": getters,
        "username": creds[0], "password": creds[1],
    })
    rc, out, err = _docker_exec(RUNNER_CONTAINER, ["python3", "/runner/collect.py", payload], timeout=EXEC_TIMEOUT)
    if rc != 0:
        return {"ok": False, "reachable": False, "method": "napalm",
                "error": (err or out or "runner exec failed").strip()[:300]}
    try:
        # collect.py prints a single JSON line (last non-empty line, to skip warnings)
        line = [l for l in out.strip().splitlines() if l.strip().startswith("{")][-1]
        return json.loads(line)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reachable": False, "method": "napalm",
                "error": f"bad runner output: {e}: {out[:200]}"}


# ── FRR via docker exec vtysh (no NAPALM driver) ─────────────────────────────────

def _vtysh_json(container: str, cmd: str) -> tuple[dict | list | None, str | None]:
    rc, out, err = _docker_exec(container, ["vtysh", "-c", f"{cmd} json"], timeout=15)
    if rc != 0:
        return None, (err or out or "exec failed").strip()[:200]
    try:
        return json.loads(out), None
    except Exception:  # noqa: BLE001 — command may not support json
        return None, "no-json"


def _frr_collect(node: dict, getters: list[str]) -> dict:
    container = node["container"]
    rc, ver_out, _ = _docker_exec(container, ["vtysh", "-c", "show version"], timeout=12)
    reachable = rc == 0
    os_version = "-"
    if reachable and ver_out:
        first = ver_out.splitlines()[0] if ver_out.splitlines() else ""
        os_version = first.replace("Copyright", "").strip()[:60] or "FRR"

    data: dict = {}
    gstatus: dict = {}

    def mark(name: str, ok: bool, err: str | None = None):
        gstatus[name] = {"ok": ok, "error": err}

    facts = {
        "hostname": node["hostname"], "vendor": "FRR", "model": node.get("model", "FRR"),
        "os_version": os_version, "serial_number": "-", "uptime": -1,
        "interface_list": [],
    }

    for g in getters:
        if not reachable:
            mark(g, False, "unreachable")
            continue
        if g == "get_facts":
            data["get_facts"] = facts
            mark("get_facts", True)
        elif g == "get_bgp_neighbors":
            j, e = _vtysh_json(container, "show bgp summary")
            peers = {}
            if isinstance(j, dict):
                for afi in j.values():
                    if isinstance(afi, dict) and "peers" in afi:
                        for pip, pd in afi["peers"].items():
                            peers[pip] = {
                                "is_up": pd.get("state", "").lower() == "established"
                                          or pd.get("peerState", "").lower() == "established",
                                "is_enabled": True,
                                "uptime": pd.get("peerUptimeMsec", -1),
                                "description": pd.get("hostname", ""),
                                "remote_as": pd.get("remoteAs", 0),
                                "address_family": {"ipv4": {"received_prefixes": pd.get("pfxRcd", 0),
                                                            "sent_prefixes": pd.get("pfxSnt", 0)}},
                            }
            data["get_bgp_neighbors"] = {"global": {"peers": peers}}
            mark("get_bgp_neighbors", bool(peers) or j is not None, None if peers or j is not None else e)
        elif g == "get_interfaces":
            j, e = _vtysh_json(container, "show interface")
            ifaces = {}
            if isinstance(j, dict):
                for name, idata in j.items():
                    ifaces[name] = {
                        "is_up": str(idata.get("administrativeStatus", idata.get("operationalStatus", ""))).lower() in ("up", "true")
                                 or idata.get("operationalStatus", "").lower() == "up",
                        "is_enabled": idata.get("administrativeStatus", "up").lower() == "up",
                        "description": idata.get("description", ""),
                        "speed": idata.get("speed", 0) or 0,
                        "mac_address": idata.get("hardwareAddress", ""),
                        "mtu": idata.get("mtu", 0),
                    }
            data["get_interfaces"] = ifaces
            facts["interface_list"] = list(ifaces.keys())
            mark("get_interfaces", bool(ifaces), None if ifaces else (e or "empty"))
        elif g == "get_interfaces_ip":
            j, _ = _vtysh_json(container, "show interface")
            ipmap = {}
            if isinstance(j, dict):
                for name, idata in j.items():
                    addrs = idata.get("ipAddresses") or []
                    v4 = {}
                    for a in addrs:
                        addr = a.get("address", "")
                        if ":" in addr:
                            continue
                        if "/" in addr:
                            ip, pl = addr.split("/")
                            v4[ip] = {"prefix_length": int(pl)}
                    if v4:
                        ipmap[name] = {"ipv4": v4}
            data["get_interfaces_ip"] = ipmap
            mark("get_interfaces_ip", bool(ipmap), None if ipmap else "no v4 addrs")
        elif g == "get_lldp_neighbors":
            data["get_lldp_neighbors"] = {}
            mark("get_lldp_neighbors", False, "FRR has no LLDP daemon")
        elif g == "get_environment":
            data["get_environment"] = {}
            mark("get_environment", False, "not available via vtysh")
        else:
            mark(g, False, "unsupported")

    return {"ok": reachable, "reachable": reachable, "method": "exec",
            "driver": "none", "facts": facts, "data": data, "getters": gstatus,
            "error": None if reachable else "vtysh unreachable"}


def _ceos_exec_fallback(node: dict, getters: list[str]) -> dict:
    """When cEOS eAPI is down, still pull facts via `Cli` so the node isn't blank."""
    container = node["container"]
    rc, out, _ = _docker_exec(container, ["Cli", "-c", "show version | json"], timeout=15)
    facts = {"hostname": node["hostname"], "vendor": "Arista", "model": node.get("model", "cEOS"),
             "os_version": "-", "serial_number": "-", "uptime": -1, "interface_list": []}
    reachable = rc == 0
    if reachable:
        try:
            v = json.loads(out)
            facts["os_version"] = v.get("version", "-")
            facts["model"] = v.get("modelName", facts["model"])
            facts["serial_number"] = v.get("serialNumber", "-")
            facts["uptime"] = int(v.get("uptime", -1))
        except Exception:  # noqa: BLE001
            pass
    gstatus = {g: {"ok": g == "get_facts" and reachable,
                   "error": None if (g == "get_facts" and reachable) else "eAPI down — exec fallback"}
               for g in getters}
    return {"ok": reachable, "reachable": reachable, "method": "exec",
            "driver": "eos", "facts": facts, "data": {"get_facts": facts}, "getters": gstatus,
            "error": None if reachable else "cEOS unreachable (eAPI + exec)"}


# ── unified per-node collection ──────────────────────────────────────────────────

def collect_node(hostname: str, getters: list[str] | None = None) -> dict:
    """Collect one lab node. Returns a rich matrix-ready dict."""
    getters = getters or STANDARD_GETTERS
    node = NODE_INDEX.get(hostname)
    if not node:
        return {"hostname": hostname, "error": "unknown node", "reachable": False}

    driver = node["driver"]
    support = NAPALM_SUPPORT.get(driver, NAPALM_SUPPORT["none"])
    t0 = time.time()

    if driver == "frr":
        res = _frr_collect(node, getters)
    elif driver in ("eos", "srl"):
        res = _runner_collect(node, getters)
        if not res.get("reachable") and driver == "eos":
            res = _ceos_exec_fallback(node, getters)  # graceful: eAPI down
    else:
        res = {"ok": False, "reachable": False, "method": "napalm",
               "error": f"unhandled driver {driver}", "data": {}, "getters": {}, "facts": {}}

    latency_ms = int((time.time() - t0) * 1000)
    facts = res.get("facts") or res.get("data", {}).get("get_facts") or {}
    return {
        "hostname": hostname,
        "container": node["container"],
        "ip": node["ip"],
        "fabric": node["fabric"],
        "tier": node["tier"],
        "vendor": node["vendor"],
        "model": node.get("model", "-"),
        "driver": driver,
        "napalm_supported": support["napalm"],
        "napalm_package": support["package"],
        "transport": support["transport"],
        "method": res.get("method", "exec"),
        "reachable": bool(res.get("reachable")),
        "latency_ms": latency_ms,
        "facts": facts,
        "getters": res.get("getters", {}),
        "data": res.get("data", {}),
        "error": res.get("error"),
    }


def collect_fabric_parallel(fabric_id: str, getters: list[str] | None = None, max_workers: int = 8) -> list[dict]:
    nodes = list(NODE_INDEX) if fabric_id in ("all", "", None) else list(FABRICS[fabric_id]["nodes"])
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(collect_node, h, getters): h for h in nodes}
        for fut in futs:
            h = futs[fut]
            try:
                out[h] = fut.result()
            except Exception as e:  # noqa: BLE001
                out[h] = {"hostname": h, "error": str(e), "reachable": False}
    return [out[h] for h in nodes]


# ── headline: NAPALM coverage matrix ─────────────────────────────────────────────

def napalm_matrix(fabric_id: str = "all", getters: list[str] | None = None) -> dict:
    getters = getters or STANDARD_GETTERS
    nodes = collect_fabric_parallel(fabric_id, getters)

    by_driver: dict[str, int] = {}
    getter_support = {g: {"ok": 0, "total": 0} for g in getters}
    napalm_native = exec_fallback = reachable = 0
    for n in nodes:
        by_driver[n["driver"]] = by_driver.get(n["driver"], 0) + 1
        if n.get("reachable"):
            reachable += 1
        if n.get("method") == "napalm" and n.get("reachable"):
            napalm_native += 1
        elif n.get("reachable"):
            exec_fallback += 1
        for g in getters:
            getter_support[g]["total"] += 1
            if n.get("getters", {}).get(g, {}).get("ok"):
                getter_support[g]["ok"] += 1

    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "fabric": fabric_id,
        "getters": getters,
        "nodes": nodes,
        "summary": {
            "total": len(nodes),
            "napalm_native": napalm_native,
            "exec_fallback": exec_fallback,
            "reachable": reachable,
            "by_driver": by_driver,
            "getter_support": getter_support,
        },
    }


# ── topology (for the fabric diagram) ────────────────────────────────────────────

def _structural_links(fabric_id: str) -> list[dict]:
    """Ground-truth designed adjacencies (used for a complete diagram; LLDP augments)."""
    if fabric_id == "clos":
        spines = [n for n, d in FABRICS["clos"]["nodes"].items() if d["tier"] == "spine"]
        leaves = [n for n, d in FABRICS["clos"]["nodes"].items() if d["tier"] == "leaf"]
        return [{"source": s, "target": l} for s in spines for l in leaves]  # full Clos mesh
    if fabric_id == "dcn":
        return [{"source": a, "target": b} for a, b in [
            ("de-fra-core-01", "de-fra-core-02"), ("de-fra-core-01", "uk-lon-core-01"),
            ("de-fra-core-02", "nl-ams-core-01"), ("de-fra-core-01", "us-nyc-core-01"),
            ("de-fra-core-01", "de-fra-edge-01"), ("de-fra-core-02", "de-fra-edge-01"),
            ("uk-lon-core-01", "uk-lon-edge-01"), ("nl-ams-core-01", "nl-ams-edge-01"),
            ("uk-lon-core-01", "uk-lon-dist-01"), ("de-fra-core-01", "de-fra-dist-01"),
        ]]
    return []


def lab_topology(fabric_id: str) -> dict:
    if fabric_id not in FABRICS:
        return {"fabric": fabric_id, "nodes": [], "links": [], "error": "unknown fabric"}
    matrix = napalm_matrix(fabric_id, ["get_facts", "get_bgp_neighbors"])
    nodes = []
    for n in matrix["nodes"]:
        peers = n.get("data", {}).get("get_bgp_neighbors") or {}
        all_peers = {}
        if isinstance(peers, dict):
            for vrf in peers.values():
                if isinstance(vrf, dict):
                    all_peers.update(vrf.get("peers", {}) or {})
        bgp_up = sum(1 for p in all_peers.values() if p.get("is_up"))
        nodes.append({
            "id": n["hostname"], "tier": n["tier"], "vendor": n["vendor"],
            "driver": n["driver"], "model": n["model"],
            "up": n.get("reachable", False), "method": n.get("method"),
            "napalm_supported": n.get("napalm_supported"),
            "bgp_up": bgp_up, "bgp_total": len(all_peers),
        })
    return {
        "fabric": fabric_id,
        "name": FABRICS[fabric_id]["name"],
        "tiers": FABRICS[fabric_id]["tiers"],
        "nodes": nodes,
        "links": _structural_links(fabric_id),
    }


# ── legacy-compat shims for the classic dashboard tools ──────────────────────────

def lab_collect_device(hostname: str, ip: str, driver: str, getters: list) -> dict:
    """Drop-in for core.collect_device — returns {ip,driver,data,error}."""
    r = collect_node(hostname, getters)
    return {"ip": r.get("ip", ip), "driver": r.get("driver", driver),
            "data": r.get("data", {}), "error": r.get("error")}


def lab_collect_parallel(devices: dict, getters: list, max_workers: int = 8) -> dict:
    """Drop-in for core.collect_site_parallel — devices is {hostname:{ip,driver}}."""
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(lab_collect_device, h, info.get("ip", ""), info.get("driver", ""), getters): h
                for h, info in devices.items()}
        for fut in futs:
            h = futs[fut]
            try:
                out[h] = fut.result()
            except Exception as e:  # noqa: BLE001
                out[h] = {"ip": devices[h].get("ip", ""), "driver": devices[h].get("driver", ""),
                          "data": {}, "error": str(e)}
    return out
