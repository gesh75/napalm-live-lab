#!/usr/bin/env python3
"""
BGP Live Collection — NAPALM version
Replaces: 04_Scripts_Tools/Analysis/dc3_bgp_live_collection.py
          04_Scripts_Tools/Analysis/dc3_bgp_live_verification.py

Connects to routers/switches via NAPALM (SSH key),
fetches structured BGP neighbor data, prints a summary table,
and saves a JSON + Markdown report.

Usage:
    python3 bgp.py --site dc3
    python3 bgp.py --site dc1
    python3 bgp.py --site dc3 --device dc3-rt-01
"""

import argparse
import json
from datetime import datetime

from rich.console import Console
from rich.table import Table

from config import SITES, OUTPUT_DIR
from core import collect_site_parallel

console = Console()

BGP_GETTERS = ["get_facts", "get_bgp_neighbors", "get_bgp_neighbors_detail"]


def summarize_bgp(results: dict) -> dict:
    """Extract BGP peer counts per device."""
    summary = {}
    for hostname, res in results.items():
        if res.get("error"):
            summary[hostname] = {"error": res["error"]}
            continue
        neighbors = res.get("data", {}).get("get_bgp_neighbors") or {}
        peers = {}
        for vrf, vrf_data in neighbors.items():
            for peer_ip, peer_data in vrf_data.get("peers", {}).items():
                peers[peer_ip] = {
                    "vrf":         vrf,
                    "up":          peer_data.get("is_up", False),
                    "enabled":     peer_data.get("is_enabled", False),
                    "description": peer_data.get("description", ""),
                    "uptime":      peer_data.get("uptime", -1),
                    "received":    peer_data.get("address_family", {}).get("ipv4", {}).get("received_prefixes", 0),
                    "sent":        peer_data.get("address_family", {}).get("ipv4", {}).get("sent_prefixes", 0),
                }
        summary[hostname] = {"peers": peers}
    return summary


def generate_report(site: str, results: dict, summary: dict) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# {site.upper()} BGP Live Status",
        f"**Generated:** {ts}  ",
        f"**SSH User:** netops (NAPALM)  \n",
    ]

    # ── Per-device BGP peer table
    for hostname, data in sorted(summary.items()):
        lines.append(f"## {hostname}")
        if "error" in data:
            lines.append(f"> ❌ {data['error']}\n")
            continue
        peers = data.get("peers", {})
        if not peers:
            lines.append("> No BGP peers found.\n")
            continue
        lines.append(f"| Peer IP | VRF | Up | Description | Uptime (s) | Rcvd | Sent |")
        lines.append(f"|---|---|---|---|---|---|---|")
        for peer_ip, p in sorted(peers.items()):
            up   = "✅" if p["up"] else "❌"
            desc = (p["description"] or "")[:35]
            uptime = p["uptime"] if p["uptime"] >= 0 else "-"
            lines.append(f"| `{peer_ip}` | {p['vrf']} | {up} | {desc} | {uptime} | {p['received']} | {p['sent']} |")
        lines.append("")

    # ── Raw JSON appendix
    lines.append("## Appendix — Raw NAPALM BGP Data\n")
    for hostname, res in sorted(results.items()):
        if res.get("error"):
            continue
        lines.append(f"### {hostname}\n```json")
        lines.append(json.dumps(res.get("data", {}).get("get_bgp_neighbors", {}), indent=2, default=str)[:3000])
        lines.append("```\n")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="NAPALM BGP live collection")
    parser.add_argument("--site",   required=True, help="Site slug, e.g. dc3")
    parser.add_argument("--device", help="Single device only")
    args = parser.parse_args()

    site = args.site.lower()
    if site not in SITES:
        console.print(f"[red]Unknown site '{site}'. Known: {list(SITES.keys())}[/red]")
        raise SystemExit(1)

    devices = SITES[site]
    if args.device:
        devices = {args.device: devices[args.device]}

    console.rule(f"[bold]{site.upper()} BGP Live Collection (NAPALM)[/bold]")
    console.print(f"\nDevices: {len(devices)}\n")

    # Collect
    results = collect_site_parallel(devices, BGP_GETTERS, max_workers=5)

    # Summarize
    summary = summarize_bgp(results)

    # Print summary table
    table = Table(title=f"{site.upper()} BGP Summary")
    table.add_column("Device",      style="bold")
    table.add_column("Total Peers", justify="right")
    table.add_column("Up",          justify="right", style="green")
    table.add_column("Down",        justify="right", style="red")

    for hostname, data in sorted(summary.items()):
        if "error" in data:
            table.add_row(hostname, "-", "-", f"[red]{data['error'][:30]}[/red]")
            continue
        peers  = data.get("peers", {})
        total  = len(peers)
        up     = sum(1 for p in peers.values() if p["up"])
        down   = total - up
        table.add_row(hostname, str(total), str(up), str(down))

    console.print(table)

    # Highlight down peers
    for hostname, data in sorted(summary.items()):
        down_peers = [ip for ip, p in data.get("peers", {}).items() if not p["up"]]
        if down_peers:
            console.print(f"\n[red]❌ {hostname} — Down peers:[/red]")
            for ip in down_peers:
                desc = data["peers"][ip].get("description", "")
                console.print(f"   {ip}  {desc}")

    # Save outputs
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUTPUT_DIR / f"{site.upper()}_BGP_{ts}.json"
    md_path   = OUTPUT_DIR / f"{site.upper()}_BGP_{ts}.md"

    json_path.write_text(json.dumps(results, indent=2, default=str))
    md_path.write_text(generate_report(site, results, summary))

    console.print(f"\n[green]✅ JSON: {json_path}[/green]")
    console.print(f"[green]✅ MD  : {md_path}[/green]")


if __name__ == "__main__":
    main()
