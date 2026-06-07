#!/usr/bin/env python3
"""
Site-wide live data collection — NAPALM version
Replaces: 04_Scripts_Tools/Analysis/dc2_live_data_collection.py
          04_Scripts_Tools/Analysis/dc2_live_data_collection_auto.py
          04_Scripts_Tools/Analysis/network_device_scripts/collect_live_device_data.py

Connects to all devices in a site (or all sites), runs all standard NAPALM getters,
saves structured JSON + a Markdown summary report.

Usage:
    python3 site_collect.py --site dc1
    python3 site_collect.py --site dc2
    python3 site_collect.py --site dc3
    python3 site_collect.py --all              # all configured sites
    python3 site_collect.py --site dc1 --device dc1-sw-01a
"""

import argparse
import json
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.progress import track

from config import SITES, OUTPUT_DIR
from core import collect_site_parallel, nb_get_devices

console = Console()

# All getters to run — covers what the old paramiko/netmiko scripts collected
FULL_GETTERS = [
    "get_facts",
    "get_interfaces",
    "get_interfaces_ip",
    "get_interfaces_counters",
    "get_lldp_neighbors",
    "get_arp_table",
    "get_environment",
]


def generate_report(site: str, results: dict, nb_devices: list) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# {site.upper()} Site Collection Report",
        f"**Generated:** {ts}  ",
        f"**SSH User:** netops (NAPALM)  \n",
    ]

    # ── Device summary
    lines.append("## Device Summary\n")
    lines.append("| Device | IP | Driver | Status | Vendor | Model | OS Version | Uptime (s) |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for hostname, res in sorted(results.items()):
        facts = res.get("data", {}).get("get_facts") or {}
        status = "✅" if not res.get("error") else "❌"
        vendor  = facts.get("vendor", "-")
        model   = facts.get("model", "-")
        version = facts.get("os_version", "-")
        uptime  = facts.get("uptime", "-")
        lines.append(f"| `{hostname}` | `{res.get('ip','-')}` | {res.get('driver','-')} | {status} | {vendor} | {model} | {version} | {uptime} |")
    lines.append("")

    # ── Interface summary per device
    lines.append("## Interface Summary\n")
    for hostname, res in sorted(results.items()):
        if res.get("error"):
            continue
        ifaces = res.get("data", {}).get("get_interfaces") or {}
        if not ifaces:
            continue
        up   = sum(1 for v in ifaces.values() if v.get("is_up"))
        down = len(ifaces) - up
        lines.append(f"### {hostname} — {len(ifaces)} interfaces ({up} up / {down} down)\n")
        lines.append("| Interface | Up | Enabled | Speed | MTU | Description |")
        lines.append("|---|---|---|---|---|---|")
        for iface, data in sorted(ifaces.items()):
            is_up  = "✅" if data.get("is_up") else "❌"
            enabled = "✅" if data.get("is_enabled") else "❌"
            speed   = data.get("speed", "-")
            mtu     = data.get("mtu", "-")
            desc    = (data.get("description") or "")[:40]
            lines.append(f"| `{iface}` | {is_up} | {enabled} | {speed} | {mtu} | {desc} |")
        lines.append("")

    # ── IP addresses per device
    lines.append("## IP Addresses\n")
    for hostname, res in sorted(results.items()):
        if res.get("error"):
            continue
        iface_ip = res.get("data", {}).get("get_interfaces_ip") or {}
        if not iface_ip:
            continue
        lines.append(f"### {hostname}\n")
        lines.append("| Interface | Address | Prefix Len | Family |")
        lines.append("|---|---|---|---|")
        for iface, families in sorted(iface_ip.items()):
            for family, addrs in families.items():
                for addr, meta in addrs.items():
                    plen = meta.get("prefix_length", "?")
                    lines.append(f"| `{iface}` | `{addr}` | `/{plen}` | {family} |")
        lines.append("")

    # ── LLDP neighbors
    lines.append("## LLDP Neighbors\n")
    for hostname, res in sorted(results.items()):
        if res.get("error"):
            continue
        lldp = res.get("data", {}).get("get_lldp_neighbors") or {}
        if not lldp:
            continue
        lines.append(f"### {hostname}\n")
        lines.append("| Local Port | Remote Host | Remote Port |")
        lines.append("|---|---|---|")
        for local_port, neighbors in sorted(lldp.items()):
            for n in neighbors:
                lines.append(f"| `{local_port}` | `{n.get('hostname','-')}` | `{n.get('port','-')}` |")
        lines.append("")

    # ── Interface counters (errors/drops)
    lines.append("## Interface Errors (non-zero only)\n")
    for hostname, res in sorted(results.items()):
        if res.get("error"):
            continue
        counters = res.get("data", {}).get("get_interfaces_counters") or {}
        error_ifaces = {
            iface: data for iface, data in counters.items()
            if any(data.get(k, 0) > 0 for k in ["rx_errors", "tx_errors", "rx_discards", "tx_discards"])
        }
        if not error_ifaces:
            continue
        lines.append(f"### {hostname}\n")
        lines.append("| Interface | RX Errors | TX Errors | RX Discards | TX Discards |")
        lines.append("|---|---|---|---|---|")
        for iface, data in sorted(error_ifaces.items()):
            lines.append(f"| `{iface}` | {data.get('rx_errors',0)} | {data.get('tx_errors',0)} | {data.get('rx_discards',0)} | {data.get('tx_discards',0)} |")
        lines.append("")

    # ── Environment (fans, temp, power)
    lines.append("## Environment\n")
    for hostname, res in sorted(results.items()):
        if res.get("error"):
            continue
        env = res.get("data", {}).get("get_environment") or {}
        if not env:
            continue
        lines.append(f"### {hostname}\n")
        # CPU
        cpu = env.get("cpu", {})
        if cpu:
            lines.append("**CPU:**")
            for slot, data in cpu.items():
                lines.append(f"- Slot {slot}: {data.get('%usage', '-')}%")
        # Memory
        mem = env.get("memory", {})
        if mem:
            used = mem.get("used_ram", 0)
            avail = mem.get("available_ram", 0)
            lines.append(f"\n**Memory:** used={used} / available={avail}")
        # Temperature
        temp = env.get("temperature", {})
        alerts = {k: v for k, v in temp.items() if v.get("is_alert") or v.get("is_critical")}
        if alerts:
            lines.append("\n**⚠️ Temperature Alerts:**")
            for sensor, data in alerts.items():
                lines.append(f"- {sensor}: {data.get('temperature','-')}°C (alert={data.get('is_alert')}, critical={data.get('is_critical')})")
        lines.append("")

    # ── NetBox cross-reference
    lines.append("## NetBox Devices (cross-reference)\n")
    if nb_devices:
        lines.append("| Name | Role | Platform | Status | Primary IP |")
        lines.append("|---|---|---|---|---|")
        for d in sorted(nb_devices, key=lambda x: x.get("name", "")):
            role     = (d.get("role") or d.get("device_role") or {}).get("name", "-")
            platform = (d.get("platform") or {}).get("name", "-")
            status   = (d.get("status") or {}).get("label", "-")
            ip       = (d.get("primary_ip") or {}).get("address", "-")
            lines.append(f"| `{d['name']}` | {role} | {platform} | {status} | `{ip}` |")
    lines.append("")

    return "\n".join(lines)


def run_site(site: str, devices: dict):
    console.rule(f"[bold]{site.upper()} — Site Collection (NAPALM)[/bold]")
    console.print(f"Devices: {', '.join(devices.keys())}\n")

    # Fetch NetBox device list
    console.print("[cyan]Fetching NetBox device list...[/cyan]")
    nb_devices = nb_get_devices(site)
    console.print(f"  NetBox devices: {len(nb_devices)}")

    # Collect via NAPALM
    console.print(f"\n[cyan]Connecting via NAPALM (SSH key)...[/cyan]")
    results = collect_site_parallel(devices, FULL_GETTERS, max_workers=5)

    # Print summary table
    table = Table(title=f"{site.upper()} Device Status")
    table.add_column("Device",  style="bold")
    table.add_column("Driver")
    table.add_column("Status")
    table.add_column("Model")
    table.add_column("OS Version")
    table.add_column("Interfaces", justify="right")
    table.add_column("IPs",        justify="right")

    for hostname, res in sorted(results.items()):
        facts    = res.get("data", {}).get("get_facts") or {}
        ifaces   = res.get("data", {}).get("get_interfaces") or {}
        iface_ip = res.get("data", {}).get("get_interfaces_ip") or {}
        status   = "[green]✅[/green]" if not res.get("error") else f"[red]❌ {(res.get('error') or '')[:25]}[/red]"
        ip_count = sum(len(addrs) for f in iface_ip.values() for addrs in f.values())
        table.add_row(
            hostname,
            res.get("driver", "-"),
            status,
            facts.get("model", "-"),
            facts.get("os_version", "-")[:30],
            str(len(ifaces)),
            str(ip_count),
        )
    console.print(table)

    # Save outputs
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_out = OUTPUT_DIR / f"{site.upper()}_Collection_{ts}.json"
    md_out   = OUTPUT_DIR / f"{site.upper()}_Collection_{ts}.md"

    json_out.write_text(json.dumps(results, indent=2, default=str))
    md_out.write_text(generate_report(site, results, nb_devices))

    console.print(f"\n[green]✅ JSON: {json_out}[/green]")
    console.print(f"[green]✅ MD  : {md_out}[/green]")
    return results


def main():
    parser = argparse.ArgumentParser(description="NAPALM site-wide data collection")
    parser.add_argument("--site",   help="Site slug, e.g. dc1")
    parser.add_argument("--all",    action="store_true", help="Collect all sites")
    parser.add_argument("--device", help="Single device only")
    args = parser.parse_args()

    if not args.site and not args.all:
        parser.error("Specify --site <slug> or --all")

    if args.all:
        for site, devices in SITES.items():
            run_site(site, devices)
    else:
        site = args.site.lower()
        if site not in SITES:
            console.print(f"[red]Unknown site '{site}'. Known: {list(SITES.keys())}[/red]")
            raise SystemExit(1)
        devices = SITES[site]
        if args.device:
            devices = {args.device: devices[args.device]}
        run_site(site, devices)


if __name__ == "__main__":
    main()
