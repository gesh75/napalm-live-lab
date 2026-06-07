#!/usr/bin/env python3
"""
NAPALM Network Dashboard — Flask Web UI
A unified dashboard for all NAPALM network operations.

Run:
    python3 dashboard.py
    # Open http://localhost:5959

Features:
    - Site Audit (Live vs NetBox)
    - BGP Status
    - Full Site Collection
    - Config Compliance
    - Environment Health
    - Interface Error Monitor
    - LLDP Topology Validation
    - Software Version Audit
    - Pre/Post Change Snapshots & Diff
"""

import os
import re
import json
import time
import difflib
import ipaddress
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# ── Import existing NAPALM core & config ────────────────────────────────────
import sys
sys.path.insert(0, os.path.dirname(__file__))

from config import (
    SITES, FABRICS, NODE_INDEX, OUTPUT_DIR,
    SSH_USER, SSH_KEY, NETBOX_URL, NETBOX_TOKEN, ZTASID,
)
# Collection is now backed by the live containerlab fabrics via the napalm-runner
# sidecar (real NAPALM for eos/srl) + docker exec (FRR). These drop-in shims keep
# the classic tool endpoints working against the live lab.
from napalm_lab import (
    lab_collect_device as collect_device,
    lab_collect_parallel as collect_site_parallel,
    napalm_matrix, lab_topology, collect_node, runner_available,
)
# Command Console: curated multivendor command catalog + secure live exec.
from command_lib import catalog as cmd_catalog, run_command, run_getter
# NetBox helpers remain available for the (now optional) netbox-audit tool.
from core import (
    open_device,
    nb_get, nb_get_prefixes, nb_get_devices, nb_get_vlans,
    extract_live_networks, compare_prefixes,
)

app = Flask(__name__, static_folder=".", static_url_path="/static")
CORS(app)

SNAPSHOTS_DIR = OUTPUT_DIR / "snapshots"
SNAPSHOTS_DIR.mkdir(exist_ok=True)

# Snapshot filenames are confined to SNAPSHOTS_DIR — never trust caller-supplied
# paths. Used by the snapshot list/diff endpoints to prevent path traversal.
_SNAP_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.json$")


def _safe_snapshot_path(name: str):
    """Resolve a snapshot filename to a path inside SNAPSHOTS_DIR, or None."""
    name = (name or "").strip()
    if not name or not _SNAP_NAME_RE.match(name):
        return None
    p = (SNAPSHOTS_DIR / name).resolve()
    try:
        p.relative_to(SNAPSHOTS_DIR.resolve())
    except ValueError:
        return None
    return p


def _safe_label(label: str) -> str:
    """Sanitize a snapshot label to a filename-safe token."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", (label or ""))[:32]
    return cleaned or "snapshot"

# ── In-memory job tracking ──────────────────────────────────────────────────
_jobs = {}
_jobs_lock = threading.Lock()


def _new_job(job_type: str, site: str) -> str:
    job_id = f"{job_type}_{site}_{int(time.time())}"
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "type": job_type,
            "site": site,
            "status": "running",
            "progress": 0,
            "message": "Starting...",
            "result": None,
            "started": datetime.now().isoformat(),
        }
    return job_id


def _update_job(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


# ── Serve frontend ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "dashboard.html")


@app.route("/dashboard.js")
def serve_js():
    return send_from_directory(".", "dashboard.js")


# ── Live Lab view (CLOS-EVPN + 3-Tier vs NAPALM) ────────────────────────────────

@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/lab")
def lab_page():
    return send_from_directory(".", "lab.html")


@app.route("/lab.js")
def lab_js():
    return send_from_directory(".", "lab.js")


@app.route("/lab.css")
def lab_css():
    return send_from_directory(".", "lab.css")


@app.route("/api/lab/fabrics")
def api_lab_fabrics():
    fabrics = []
    for fid, fab in FABRICS.items():
        vendors = {}
        for nd in fab["nodes"].values():
            vendors[nd["vendor"]] = vendors.get(nd["vendor"], 0) + 1
        fabrics.append({
            "id": fid,
            "name": fab["name"],
            "kind": fab["kind"],
            "node_count": len(fab["nodes"]),
            "tiers": fab["tiers"],
            "vendors": vendors,
            "mgmt_subnet": fab.get("mgmt_subnet", ""),
        })
    return jsonify({"fabrics": fabrics, "runner_up": runner_available()})


@app.route("/api/lab/matrix")
def api_lab_matrix():
    fabric = (request.args.get("fabric") or "all").lower()
    if fabric != "all" and fabric not in FABRICS:
        return jsonify({"error": f"unknown fabric: {fabric}"}), 400
    return jsonify(napalm_matrix(fabric))


@app.route("/api/lab/topology")
def api_lab_topology():
    fabric = (request.args.get("fabric") or "clos").lower()
    if fabric not in FABRICS:
        return jsonify({"error": f"unknown fabric: {fabric}"}), 400
    return jsonify(lab_topology(fabric))


@app.route("/api/lab/node/<hostname>")
def api_lab_node(hostname):
    if hostname not in NODE_INDEX:
        return jsonify({"error": f"unknown node: {hostname}"}), 404
    return jsonify(collect_node(hostname))


# ── Command Console (curated multivendor catalog + secure live exec) ─────────────

@app.route("/api/lab/commands")
def api_lab_commands():
    """Curated multivendor command catalog + live read-only policy."""
    return jsonify(cmd_catalog())


@app.route("/api/lab/console/nodes")
def api_lab_console_nodes():
    """Run targets for the console dropdown (hostname + how it's reached)."""
    wrapper = {"eos": "Cli", "frr": "vtysh", "srl": "sr_cli"}
    nodes = [{
        "hostname": h, "fabric": n["fabric"], "tier": n["tier"],
        "vendor": n["vendor"], "driver": n["driver"],
        "wrapper": wrapper.get(n["driver"], "—"),
    } for h, n in NODE_INDEX.items()]
    return jsonify({"nodes": nodes})


@app.route("/api/lab/run", methods=["POST"])
def api_lab_run():
    """Run one CLI command against a live lab node (read-only by default)."""
    body = request.get_json(silent=True) or {}
    hostname = (body.get("hostname") or "").strip()
    command = body.get("command") or ""
    allow_write = bool(body.get("allow_write"))
    if not hostname or not command:
        return jsonify({"error": "hostname and command are required"}), 400
    if hostname not in NODE_INDEX:
        return jsonify({"error": f"unknown node: {hostname}"}), 404
    result = run_command(hostname, command, allow_write=allow_write)
    # 200 for a successful run or a deliberately-blocked (policy) command;
    # 502 when the upstream node was unreachable / docker errored.
    status = 200 if (result.get("ok") or result.get("blocked")) else 502
    return jsonify(result), status


@app.route("/api/lab/getter", methods=["POST"])
def api_lab_getter():
    """Run a single NAPALM getter against a live lab node (structured JSON)."""
    body = request.get_json(silent=True) or {}
    hostname = (body.get("hostname") or "").strip()
    getter = (body.get("getter") or "").strip()
    if not hostname or not getter:
        return jsonify({"error": "hostname and getter are required"}), 400
    if hostname not in NODE_INDEX:
        return jsonify({"error": f"unknown node: {hostname}"}), 404
    return jsonify(run_getter(hostname, getter))


# ── API: Status & Config ────────────────────────────────────────────────────

@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "tool": "NAPALM Network Dashboard",
        "version": "2.0.0-lab",
        "mode": "live-containerlab",
        "runner_up": runner_available(),
        "netbox_url": NETBOX_URL or "(unset)",
        "ztasid_set": bool(ZTASID),
        "fabrics": {fid: {"name": f["name"], "nodes": len(f["nodes"])} for fid, f in FABRICS.items()},
        "sites": list(SITES.keys()),
        "total_devices": sum(len(d) for d in SITES.values()),
    })


@app.route("/api/sites")
def get_sites():
    result = {}
    for site, devices in SITES.items():
        result[site] = {
            "device_count": len(devices),
            "devices": {
                h: {"ip": d["ip"], "driver": d["driver"]}
                for h, d in devices.items()
            }
        }
    return jsonify(result)


@app.route("/api/jobs")
def get_jobs():
    with _jobs_lock:
        return jsonify(list(_jobs.values())[-20:])


@app.route("/api/jobs/<job_id>")
def get_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


# ── API: Quick Device Facts ─────────────────────────────────────────────────

@app.route("/api/device/<hostname>/facts")
def device_facts(hostname):
    """Quick facts for a single device."""
    device_info = None
    for site, devices in SITES.items():
        if hostname in devices:
            device_info = devices[hostname]
            break
    if not device_info:
        return jsonify({"error": f"Device {hostname} not in inventory"}), 404

    result = collect_device(hostname, device_info["ip"], device_info["driver"], ["get_facts"])
    return jsonify(result)


# ══════════════════════════════════════════════════════════════════════════════
# TOOL 1: VERSION AUDIT
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/tools/version-audit", methods=["POST"])
def version_audit():
    data = request.json or {}
    site = data.get("site", "").lower()
    if site and site not in SITES:
        return jsonify({"error": f"Unknown site: {site}"}), 400

    job_id = _new_job("version_audit", site or "all")

    def _run():
        try:
            sites_to_scan = {site: SITES[site]} if site else SITES
            all_results = []

            total = sum(len(d) for d in sites_to_scan.values())
            done = 0

            for s, devices in sites_to_scan.items():
                _update_job(job_id, message=f"Scanning {s.upper()}...")
                results = collect_site_parallel(devices, ["get_facts"], max_workers=5)

                for hostname, res in sorted(results.items()):
                    done += 1
                    _update_job(job_id, progress=int(done / total * 100))
                    facts = res.get("data", {}).get("get_facts") or {}
                    all_results.append({
                        "site": s.upper(),
                        "hostname": hostname,
                        "ip": res.get("ip", "-"),
                        "driver": res.get("driver", "-"),
                        "vendor": facts.get("vendor", "-"),
                        "model": facts.get("model", "-"),
                        "os_version": facts.get("os_version", "-"),
                        "serial": facts.get("serial_number", "-"),
                        "uptime": facts.get("uptime", -1),
                        "error": res.get("error"),
                    })

            # Detect version mismatches within same model
            model_versions = {}
            for r in all_results:
                if r["error"]:
                    continue
                key = (r["driver"], r["model"])
                model_versions.setdefault(key, set()).add(r["os_version"])

            mismatches = []
            for (driver, model), versions in model_versions.items():
                if len(versions) > 1:
                    mismatches.append({
                        "driver": driver,
                        "model": model,
                        "versions": sorted(versions),
                        "devices": [r["hostname"] for r in all_results
                                    if r["model"] == model and r["driver"] == driver]
                    })

            _update_job(job_id, status="done", progress=100,
                        message=f"Scanned {len(all_results)} devices",
                        result={
                            "devices": all_results,
                            "mismatches": mismatches,
                            "total": len(all_results),
                            "errors": sum(1 for r in all_results if r["error"]),
                        })
        except Exception as e:
            _update_job(job_id, status="error", message=str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


# ══════════════════════════════════════════════════════════════════════════════
# TOOL 2: BGP STATUS
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/tools/bgp-status", methods=["POST"])
def bgp_status():
    data = request.json or {}
    site = data.get("site", "").lower()
    if not site or site not in SITES:
        return jsonify({"error": f"Unknown site: {site}"}), 400

    job_id = _new_job("bgp_status", site)

    def _run():
        try:
            devices = SITES[site]
            _update_job(job_id, message=f"Collecting BGP from {len(devices)} devices...")
            results = collect_site_parallel(devices,
                                           ["get_facts", "get_bgp_neighbors"],
                                           max_workers=5)

            bgp_summary = []
            for hostname, res in sorted(results.items()):
                if res.get("error"):
                    bgp_summary.append({
                        "hostname": hostname, "error": res["error"],
                        "peers": [], "total": 0, "up": 0, "down": 0
                    })
                    continue

                neighbors = res.get("data", {}).get("get_bgp_neighbors") or {}
                peers = []
                for vrf, vrf_data in neighbors.items():
                    for peer_ip, peer_data in vrf_data.get("peers", {}).items():
                        af = peer_data.get("address_family", {})
                        ipv4 = af.get("ipv4", af.get("ipv4 unicast", {}))
                        peers.append({
                            "peer_ip": peer_ip,
                            "vrf": vrf,
                            "is_up": peer_data.get("is_up", False),
                            "is_enabled": peer_data.get("is_enabled", False),
                            "description": peer_data.get("description", ""),
                            "uptime": peer_data.get("uptime", -1),
                            "received": ipv4.get("received_prefixes", 0),
                            "sent": ipv4.get("sent_prefixes", 0),
                        })

                up = sum(1 for p in peers if p["is_up"])
                bgp_summary.append({
                    "hostname": hostname,
                    "peers": peers,
                    "total": len(peers),
                    "up": up,
                    "down": len(peers) - up,
                    "error": None,
                })

            total_peers = sum(d["total"] for d in bgp_summary)
            total_down = sum(d["down"] for d in bgp_summary)

            _update_job(job_id, status="done", progress=100,
                        message=f"{total_peers} peers, {total_down} down",
                        result={
                            "site": site.upper(),
                            "devices": bgp_summary,
                            "total_peers": total_peers,
                            "total_down": total_down,
                        })
        except Exception as e:
            _update_job(job_id, status="error", message=str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


# ══════════════════════════════════════════════════════════════════════════════
# TOOL 3: NETBOX AUDIT
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/tools/netbox-audit", methods=["POST"])
def netbox_audit():
    data = request.json or {}
    site = data.get("site", "").lower()
    if not site or site not in SITES:
        return jsonify({"error": f"Unknown site: {site}"}), 400

    job_id = _new_job("netbox_audit", site)

    def _run():
        try:
            devices = SITES[site]

            _update_job(job_id, message="Fetching NetBox prefixes & VLANs...", progress=10)
            nb_prefixes = nb_get_prefixes(site)
            nb_vlans = nb_get_vlans(site)

            _update_job(job_id, message=f"Connecting to {len(devices)} devices...", progress=30)
            results = collect_site_parallel(devices,
                                           ["get_facts", "get_interfaces_ip", "get_interfaces"],
                                           max_workers=5)

            _update_job(job_id, message="Comparing live vs NetBox...", progress=80)
            live_nets = extract_live_networks(results)
            comparison = compare_prefixes(nb_prefixes, live_nets)

            device_summary = []
            for hostname, res in sorted(results.items()):
                facts = res.get("data", {}).get("get_facts") or {}
                ifaces = res.get("data", {}).get("get_interfaces") or {}
                iface_ip = res.get("data", {}).get("get_interfaces_ip") or {}
                ip_count = sum(len(addrs) for f in iface_ip.values() for addrs in f.values())
                device_summary.append({
                    "hostname": hostname,
                    "ip": res.get("ip", "-"),
                    "driver": res.get("driver", "-"),
                    "model": facts.get("model", "-"),
                    "version": facts.get("os_version", "-"),
                    "interfaces": len(ifaces),
                    "ips": ip_count,
                    "error": res.get("error"),
                })

            _update_job(job_id, status="done", progress=100,
                        message=f"Matched: {len(comparison['matched'])}, "
                                f"NetBox only: {len(comparison['netbox_only'])}, "
                                f"Live only: {len(comparison['live_only'])}",
                        result={
                            "site": site.upper(),
                            "devices": device_summary,
                            "netbox_prefixes": len(nb_prefixes),
                            "netbox_vlans": len(nb_vlans),
                            "matched": [str(n) for n in comparison["matched"]],
                            "netbox_only": [str(n) for n in comparison["netbox_only"]],
                            "live_only": [str(n) for n in comparison["live_only"]],
                        })
        except Exception as e:
            _update_job(job_id, status="error", message=str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


# ══════════════════════════════════════════════════════════════════════════════
# TOOL 4: ENVIRONMENT HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/tools/env-health", methods=["POST"])
def env_health():
    data = request.json or {}
    site = data.get("site", "").lower()
    if not site or site not in SITES:
        return jsonify({"error": f"Unknown site: {site}"}), 400

    job_id = _new_job("env_health", site)

    def _run():
        try:
            devices = SITES[site]
            _update_job(job_id, message=f"Collecting environment from {len(devices)} devices...")
            results = collect_site_parallel(devices,
                                           ["get_facts", "get_environment"],
                                           max_workers=5)

            health_data = []
            alerts = []
            for hostname, res in sorted(results.items()):
                if res.get("error"):
                    health_data.append({"hostname": hostname, "error": res["error"]})
                    continue

                facts = res.get("data", {}).get("get_facts") or {}
                env = res.get("data", {}).get("get_environment") or {}

                # CPU
                cpu = env.get("cpu", {})
                max_cpu = max((v.get("%usage", 0) for v in cpu.values()), default=0)

                # Memory
                mem = env.get("memory", {})
                used = mem.get("used_ram", 0)
                avail = mem.get("available_ram", 0)
                total_mem = used + avail
                mem_pct = round(used / total_mem * 100, 1) if total_mem > 0 else 0

                # Temperature
                temp = env.get("temperature", {})
                temp_alerts = []
                for sensor, data in temp.items():
                    if data.get("is_alert") or data.get("is_critical"):
                        temp_alerts.append({
                            "sensor": sensor,
                            "temperature": data.get("temperature", "?"),
                            "is_critical": data.get("is_critical", False),
                        })
                        alerts.append({
                            "hostname": hostname,
                            "type": "temperature",
                            "sensor": sensor,
                            "value": data.get("temperature", "?"),
                            "critical": data.get("is_critical", False),
                        })

                # Fans
                fans = env.get("fans", {})
                fan_ok = all(v.get("status", True) for v in fans.values())
                if not fan_ok:
                    alerts.append({"hostname": hostname, "type": "fan",
                                   "sensor": "fans", "value": "FAILED", "critical": True})

                # Power
                power = env.get("power", {})
                power_ok = all(v.get("status", True) for v in power.values())
                if not power_ok:
                    alerts.append({"hostname": hostname, "type": "power",
                                   "sensor": "power", "value": "FAILED", "critical": True})

                # High CPU/memory
                if max_cpu > 80:
                    alerts.append({"hostname": hostname, "type": "cpu",
                                   "sensor": "cpu", "value": f"{max_cpu}%", "critical": max_cpu > 95})
                if mem_pct > 85:
                    alerts.append({"hostname": hostname, "type": "memory",
                                   "sensor": "memory", "value": f"{mem_pct}%", "critical": mem_pct > 95})

                health_data.append({
                    "hostname": hostname,
                    "model": facts.get("model", "-"),
                    "uptime": facts.get("uptime", -1),
                    "cpu_pct": max_cpu,
                    "memory_pct": mem_pct,
                    "memory_used": used,
                    "memory_total": total_mem,
                    "temp_alerts": temp_alerts,
                    "fans_ok": fan_ok,
                    "power_ok": power_ok,
                    "error": None,
                })

            _update_job(job_id, status="done", progress=100,
                        message=f"{len(health_data)} devices, {len(alerts)} alerts",
                        result={
                            "site": site.upper(),
                            "devices": health_data,
                            "alerts": alerts,
                        })
        except Exception as e:
            _update_job(job_id, status="error", message=str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


# ══════════════════════════════════════════════════════════════════════════════
# TOOL 5: INTERFACE ERRORS
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/tools/interface-errors", methods=["POST"])
def interface_errors():
    data = request.json or {}
    site = data.get("site", "").lower()
    if not site or site not in SITES:
        return jsonify({"error": f"Unknown site: {site}"}), 400

    job_id = _new_job("interface_errors", site)

    def _run():
        try:
            devices = SITES[site]
            _update_job(job_id, message=f"Collecting counters from {len(devices)} devices...")
            results = collect_site_parallel(devices,
                                           ["get_facts", "get_interfaces",
                                            "get_interfaces_counters"],
                                           max_workers=5)

            all_errors = []
            for hostname, res in sorted(results.items()):
                if res.get("error"):
                    continue
                counters = res.get("data", {}).get("get_interfaces_counters") or {}
                ifaces = res.get("data", {}).get("get_interfaces") or {}

                for iface, data in counters.items():
                    rx_err = data.get("rx_errors", 0)
                    tx_err = data.get("tx_errors", 0)
                    rx_dis = data.get("rx_discards", 0)
                    tx_dis = data.get("tx_discards", 0)

                    if rx_err > 0 or tx_err > 0 or rx_dis > 0 or tx_dis > 0:
                        iface_info = ifaces.get(iface, {})
                        all_errors.append({
                            "hostname": hostname,
                            "interface": iface,
                            "description": (iface_info.get("description") or "")[:50],
                            "is_up": iface_info.get("is_up", False),
                            "speed": iface_info.get("speed", 0),
                            "rx_errors": rx_err,
                            "tx_errors": tx_err,
                            "rx_discards": rx_dis,
                            "tx_discards": tx_dis,
                            "total": rx_err + tx_err + rx_dis + tx_dis,
                        })

            all_errors.sort(key=lambda x: x["total"], reverse=True)

            _update_job(job_id, status="done", progress=100,
                        message=f"{len(all_errors)} interfaces with errors",
                        result={
                            "site": site.upper(),
                            "errors": all_errors[:100],
                            "total_interfaces_with_errors": len(all_errors),
                        })
        except Exception as e:
            _update_job(job_id, status="error", message=str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


# ══════════════════════════════════════════════════════════════════════════════
# TOOL 6: LLDP TOPOLOGY VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/tools/lldp-topology", methods=["POST"])
def lldp_topology():
    data = request.json or {}
    site = data.get("site", "").lower()
    if not site or site not in SITES:
        return jsonify({"error": f"Unknown site: {site}"}), 400

    job_id = _new_job("lldp_topology", site)

    def _run():
        try:
            devices = SITES[site]
            _update_job(job_id, message=f"Collecting LLDP from {len(devices)} devices...")
            results = collect_site_parallel(devices,
                                           ["get_facts", "get_lldp_neighbors"],
                                           max_workers=5)

            links = []
            nodes = set()
            for hostname, res in sorted(results.items()):
                if res.get("error"):
                    continue
                nodes.add(hostname)
                lldp = res.get("data", {}).get("get_lldp_neighbors") or {}
                for local_port, neighbors in lldp.items():
                    for n in neighbors:
                        remote = n.get("hostname", "")
                        remote_port = n.get("port", "")
                        if remote:
                            # Normalize hostname (strip domain)
                            remote_short = remote.split(".")[0].lower()
                            nodes.add(remote_short)
                            links.append({
                                "source": hostname,
                                "source_port": local_port,
                                "target": remote_short,
                                "target_port": remote_port,
                            })

            # Deduplicate bidirectional links
            seen = set()
            unique_links = []
            for link in links:
                key = tuple(sorted([
                    f"{link['source']}:{link['source_port']}",
                    f"{link['target']}:{link['target_port']}"
                ]))
                if key not in seen:
                    seen.add(key)
                    unique_links.append(link)

            _update_job(job_id, status="done", progress=100,
                        message=f"{len(nodes)} nodes, {len(unique_links)} links",
                        result={
                            "site": site.upper(),
                            "nodes": sorted(nodes),
                            "links": unique_links,
                            "total_nodes": len(nodes),
                            "total_links": len(unique_links),
                        })
        except Exception as e:
            _update_job(job_id, status="error", message=str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


# ══════════════════════════════════════════════════════════════════════════════
# TOOL 7: FULL SITE COLLECTION
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/tools/site-collect", methods=["POST"])
def site_collect():
    data = request.json or {}
    site = data.get("site", "").lower()
    if not site or site not in SITES:
        return jsonify({"error": f"Unknown site: {site}"}), 400

    job_id = _new_job("site_collect", site)

    def _run():
        try:
            devices = SITES[site]
            _update_job(job_id, message=f"Full collection from {len(devices)} devices...")

            getters = [
                "get_facts", "get_interfaces", "get_interfaces_ip",
                "get_interfaces_counters", "get_lldp_neighbors",
                "get_arp_table", "get_environment",
            ]
            results = collect_site_parallel(devices, getters, max_workers=5)

            # Save JSON
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            json_path = OUTPUT_DIR / f"{site.upper()}_Collection_{ts}.json"
            json_path.write_text(json.dumps(results, indent=2, default=str))

            summary = []
            for hostname, res in sorted(results.items()):
                facts = res.get("data", {}).get("get_facts") or {}
                ifaces = res.get("data", {}).get("get_interfaces") or {}
                iface_ip = res.get("data", {}).get("get_interfaces_ip") or {}
                lldp = res.get("data", {}).get("get_lldp_neighbors") or {}
                arp = res.get("data", {}).get("get_arp_table") or []
                ip_count = sum(len(addrs) for f in iface_ip.values() for addrs in f.values())
                lldp_count = sum(len(n) for n in lldp.values())

                summary.append({
                    "hostname": hostname,
                    "model": facts.get("model", "-"),
                    "version": facts.get("os_version", "-"),
                    "interfaces": len(ifaces),
                    "ips": ip_count,
                    "lldp_neighbors": lldp_count,
                    "arp_entries": len(arp) if isinstance(arp, list) else 0,
                    "error": res.get("error"),
                })

            _update_job(job_id, status="done", progress=100,
                        message=f"Collected {len(summary)} devices → {json_path.name}",
                        result={
                            "site": site.upper(),
                            "devices": summary,
                            "output_file": str(json_path),
                        })
        except Exception as e:
            _update_job(job_id, status="error", message=str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


# ══════════════════════════════════════════════════════════════════════════════
# TOOL 8: PRE/POST CHANGE SNAPSHOT & DIFF
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/tools/snapshot", methods=["POST"])
def take_snapshot():
    """Take a snapshot of current device state (pre or post change)."""
    data = request.json or {}
    site = data.get("site", "").lower()
    label = _safe_label(data.get("label", "snapshot"))  # "pre" / "post" — sanitized
    if not site or site not in SITES:
        return jsonify({"error": f"Unknown site: {site}"}), 400

    job_id = _new_job("snapshot", site)

    def _run():
        try:
            devices = SITES[site]
            _update_job(job_id, message=f"Taking {label} snapshot of {len(devices)} devices...")

            getters = [
                "get_facts", "get_interfaces", "get_interfaces_ip",
                "get_bgp_neighbors", "get_lldp_neighbors",
            ]
            results = collect_site_parallel(devices, getters, max_workers=5)

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            snap_file = SNAPSHOTS_DIR / f"{site.upper()}_{label}_{ts}.json"
            snap_file.write_text(json.dumps(results, indent=2, default=str))

            _update_job(job_id, status="done", progress=100,
                        message=f"{label.upper()} snapshot saved: {snap_file.name}",
                        result={
                            "site": site.upper(),
                            "label": label,
                            "file": snap_file.name,
                            "path": str(snap_file),
                            "devices": len(results),
                            "timestamp": ts,
                        })
        except Exception as e:
            _update_job(job_id, status="error", message=str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/tools/snapshots/<site>")
def list_snapshots(site):
    """List all snapshots for a site."""
    if site.lower() not in SITES:
        return jsonify({"error": f"unknown site: {site}"}), 404
    site = site.upper()
    snaps = sorted(SNAPSHOTS_DIR.glob(f"{site}_*.json"), reverse=True)
    return jsonify([{
        "file": s.name,
        "size": s.stat().st_size,
        "modified": datetime.fromtimestamp(s.stat().st_mtime).isoformat(),
    } for s in snaps])


@app.route("/api/tools/snapshot-diff", methods=["POST"])
def snapshot_diff():
    """Diff two snapshots."""
    data = request.json or {}
    file_a = data.get("file_a", "")
    file_b = data.get("file_b", "")

    path_a = _safe_snapshot_path(file_a)
    path_b = _safe_snapshot_path(file_b)
    if path_a is None or path_b is None:
        return jsonify({"error": "Invalid snapshot filename"}), 400
    if not path_a.exists() or not path_b.exists():
        return jsonify({"error": "Snapshot file not found"}), 404

    snap_a = json.loads(path_a.read_text())
    snap_b = json.loads(path_b.read_text())

    diffs = []
    all_hosts = set(snap_a.keys()) | set(snap_b.keys())

    for host in sorted(all_hosts):
        a_data = snap_a.get(host, {})
        b_data = snap_b.get(host, {})

        if host not in snap_a:
            diffs.append({"hostname": host, "type": "device_added", "details": "New device in snapshot B"})
            continue
        if host not in snap_b:
            diffs.append({"hostname": host, "type": "device_removed", "details": "Device missing in snapshot B"})
            continue

        # Compare interfaces
        a_ifaces = (a_data.get("data", {}).get("get_interfaces") or {})
        b_ifaces = (b_data.get("data", {}).get("get_interfaces") or {})

        for iface in set(a_ifaces.keys()) | set(b_ifaces.keys()):
            a_up = a_ifaces.get(iface, {}).get("is_up")
            b_up = b_ifaces.get(iface, {}).get("is_up")
            if a_up != b_up:
                diffs.append({
                    "hostname": host,
                    "type": "interface_state",
                    "interface": iface,
                    "details": f"{'UP' if a_up else 'DOWN'} → {'UP' if b_up else 'DOWN'}",
                })

        # Compare BGP peers
        a_bgp = a_data.get("data", {}).get("get_bgp_neighbors") or {}
        b_bgp = b_data.get("data", {}).get("get_bgp_neighbors") or {}

        a_peers = {}
        for vrf, vd in a_bgp.items():
            for peer_ip, pd in vd.get("peers", {}).items():
                a_peers[peer_ip] = pd.get("is_up", False)
        b_peers = {}
        for vrf, vd in b_bgp.items():
            for peer_ip, pd in vd.get("peers", {}).items():
                b_peers[peer_ip] = pd.get("is_up", False)

        for peer_ip in set(a_peers.keys()) | set(b_peers.keys()):
            a_up = a_peers.get(peer_ip)
            b_up = b_peers.get(peer_ip)
            if a_up != b_up:
                diffs.append({
                    "hostname": host,
                    "type": "bgp_state",
                    "interface": peer_ip,
                    "details": f"{'UP' if a_up else 'DOWN'} → {'UP' if b_up else 'DOWN'}",
                })

        # Compare IPs
        a_ips = a_data.get("data", {}).get("get_interfaces_ip") or {}
        b_ips = b_data.get("data", {}).get("get_interfaces_ip") or {}

        a_flat = set()
        for iface, families in a_ips.items():
            for fam, addrs in families.items():
                for addr in addrs:
                    a_flat.add(f"{iface}:{addr}")
        b_flat = set()
        for iface, families in b_ips.items():
            for fam, addrs in families.items():
                for addr in addrs:
                    b_flat.add(f"{iface}:{addr}")

        for ip in a_flat - b_flat:
            diffs.append({"hostname": host, "type": "ip_removed", "interface": ip.split(":")[0],
                          "details": f"IP removed: {ip.split(':')[1]}"})
        for ip in b_flat - a_flat:
            diffs.append({"hostname": host, "type": "ip_added", "interface": ip.split(":")[0],
                          "details": f"IP added: {ip.split(':')[1]}"})

    return jsonify({
        "file_a": file_a,
        "file_b": file_b,
        "total_changes": len(diffs),
        "changes": diffs,
    })


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", 5959))
    # Bind to loopback by default — LAN-exposure hardening (matches :5757)
    bind_host = os.getenv("DASHBOARD_BIND_HOST", "127.0.0.1")
    print(f"\n🔧 NAPALM Network Dashboard starting on http://localhost:{port}")
    print(f"   Sites: {', '.join(SITES.keys())} ({sum(len(d) for d in SITES.values())} devices)")
    print(f"   SSH user: {SSH_USER}")
    print(f"   SSH key: {SSH_KEY} (exists: {os.path.exists(SSH_KEY)})")
    print(f"   NetBox: {NETBOX_URL} (ztasid: {'set' if ZTASID else 'NOT SET'})\n")
    app.run(host=bind_host, port=port, debug=False, threaded=True)
