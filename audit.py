#!/usr/bin/env python3
"""
Network Audit — NAPALM version
Replaces: 04_Scripts_Tools/Analysis/dc1_network_audit.py

Connects to all devices in a site via NAPALM (SSH key),
fetches live IP interfaces, compares against NetBox active prefixes,
and writes a markdown report.

Usage:
    python3 audit.py --site dc1
    python3 audit.py --site dc1 --device dc1-fw-20a
    python3 audit.py --site dc1 --no-ssh
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from config import SITES, OUTPUT_DIR
from core import (
    collect_site_parallel,
    nb_get_prefixes,
    nb_get_vlans,
    extract_live_networks,
    compare_prefixes,
)

console = Console()


# ── Report ─────────────────────────────────────────────────────────────────────

def generate_report(site: str, results: dict, nb_prefixes: list,
                    nb_vlans: list, comparison: dict) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# {site.upper()} Network Audit — Live vs NetBox",
        f"**Generated:** {ts}  ",
        f"**SSH User:** netops (NAPALM)  ",
        f"**Site:** {site.upper()}\n",
    ]

    # ── Device connectivity
    lines.append("## Device Connectivity\n")
    lines.append("| Device | IP | Driver | Status | Facts |")
    lines.append("|---|---|---|---|---|")
    for hostname, res in sorted(results.items()):
        facts = res.get("data", {}).get("get_facts") or {}
        status = "✅ OK" if not res.get("error") else f"❌ {res['error'][:50]}"
        model   = facts.get("model", "-")
        version = facts.get("os_version", "-")
        lines.append(f"| `{hostname}` | `{res.get('ip','-')}` | {res.get('driver','-')} | {status} | {model} / {version} |")
    lines.append("")

    # ── Live interfaces per device
    lines.append("## Live Interface IPs (NAPALM get_interfaces_ip)\n")
    for hostname, res in sorted(results.items()):
        lines.append(f"### {hostname}")
        if res.get("error"):
            lines.append(f"> ⚠️ {res['error']}\n")
            continue
        iface_ip = res.get("data", {}).get("get_interfaces_ip") or {}
        if not iface_ip:
            lines.append("> No IP interfaces found.\n")
            continue
        lines.append("| Interface | Address | Prefix Len | Family |")
        lines.append("|---|---|---|---|")
        for iface, families in sorted(iface_ip.items()):
            for family, addrs in families.items():
                for addr, meta in addrs.items():
                    plen = meta.get("prefix_length", "?")
                    lines.append(f"| `{iface}` | `{addr}` | `/{plen}` | {family} |")
        lines.append("")

    # ── Live interface state
    lines.append("## Live Interface State (NAPALM get_interfaces)\n")
    for hostname, res in sorted(results.items()):
        if res.get("error"):
            continue
        ifaces = res.get("data", {}).get("get_interfaces") or {}
        if not ifaces:
            continue
        lines.append(f"### {hostname}")
        lines.append("| Interface | Enabled | Up | Speed | Description |")
        lines.append("|---|---|---|---|---|")
        for iface, data in sorted(ifaces.items()):
            enabled = "✅" if data.get("is_enabled") else "❌"
            up      = "✅" if data.get("is_up") else "❌"
            speed   = data.get("speed", "-")
            desc    = (data.get("description") or "")[:40]
            lines.append(f"| `{iface}` | {enabled} | {up} | {speed} | {desc} |")
        lines.append("")

    # ── NetBox prefixes
    lines.append("## NetBox Active Prefixes\n")
    lines.append("| Prefix | VRF | Role | VLAN | Description |")
    lines.append("|---|---|---|---|---|")
    for p in sorted(nb_prefixes, key=lambda x: x["prefix"]):
        vrf  = (p.get("vrf") or {}).get("name", "-")
        role = (p.get("role") or {}).get("name", "-")
        vlan = p.get("vlan") or {}
        vlan_str = f"{vlan.get('vid','')}-{vlan.get('name','')}" if vlan else "-"
        desc = p.get("description", "") or ""
        lines.append(f"| `{p['prefix']}` | {vrf} | {role} | {vlan_str} | {desc} |")
    lines.append("")

    # ── Comparison
    lines.append("## Comparison: NetBox vs Live\n")

    lines.append(f"### ✅ Matched ({len(comparison['matched'])} prefixes)")
    lines.append("> Exist in **both** NetBox and live device interfaces.\n")
    if comparison["matched"]:
        lines.append("| Prefix |")
        lines.append("|---|")
        for n in comparison["matched"]:
            lines.append(f"| `{n}` |")
    else:
        lines.append("> None matched.")
    lines.append("")

    lines.append(f"### ⚠️ In NetBox Only ({len(comparison['netbox_only'])} prefixes)")
    lines.append("> Documented in NetBox but **not seen** as active interfaces.\n")
    if comparison["netbox_only"]:
        lines.append("| Prefix |")
        lines.append("|---|")
        for n in comparison["netbox_only"]:
            lines.append(f"| `{n}` |")
    else:
        lines.append("> None.")
    lines.append("")

    lines.append(f"### 🔴 Live Only ({len(comparison['live_only'])} prefixes)")
    lines.append("> Active on devices but **missing from NetBox**.\n")
    if comparison["live_only"]:
        lines.append("| Prefix |")
        lines.append("|---|")
        for n in comparison["live_only"]:
            lines.append(f"| `{n}` |")
    else:
        lines.append("> None — live data fully covered in NetBox. ✅")
    lines.append("")

    # ── Raw NAPALM data appendix
    lines.append("## Appendix — Raw NAPALM Data (JSON)\n")
    for hostname, res in sorted(results.items()):
        if res.get("error"):
            continue
        lines.append(f"### {hostname}\n")
        lines.append("```json")
        lines.append(json.dumps(res.get("data", {}), indent=2, default=str)[:4000])
        lines.append("```\n")

    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NAPALM network audit vs NetBox")
    parser.add_argument("--site",   required=True, help="Site slug, e.g. dc1")
    parser.add_argument("--device", help="Audit single device only")
    parser.add_argument("--no-ssh", action="store_true", help="Skip SSH, NetBox data only")
    parser.add_argument("--output", help="Output .md file name (default: <SITE>_Audit_<DATE>.md)")
    args = parser.parse_args()

    site = args.site.lower()
    if site not in SITES:
        console.print(f"[red]Unknown site '{site}'. Known sites: {list(SITES.keys())}[/red]")
        raise SystemExit(1)

    devices = SITES[site]
    if args.device:
        if args.device not in devices:
            console.print(f"[red]Unknown device '{args.device}'[/red]")
            raise SystemExit(1)
        devices = {args.device: devices[args.device]}

    console.rule(f"[bold]{site.upper()} NAPALM Audit[/bold]")

    # 1. NetBox
    console.print("\n[bold cyan][1/3] Fetching NetBox data...[/bold cyan]")
    nb_prefixes = nb_get_prefixes(site)
    nb_vlans    = nb_get_vlans(site)
    console.print(f"  Prefixes : {len(nb_prefixes)}")
    console.print(f"  VLANs    : {len(nb_vlans)}")

    # 2. SSH via NAPALM
    results = {}
    if args.no_ssh:
        console.print("\n[yellow][2/3] Skipping SSH (--no-ssh)[/yellow]")
        for h, info in devices.items():
            results[h] = {"hostname": h, "ip": info["ip"], "driver": info["driver"],
                          "data": {}, "error": "Skipped"}
    else:
        console.print(f"\n[bold cyan][2/3] Connecting to {len(devices)} devices (NAPALM)...[/bold cyan]")
        getters = ["get_facts", "get_interfaces_ip", "get_interfaces"]
        results = collect_site_parallel(devices, getters, max_workers=5)

    # 3. Compare
    console.print("\n[bold cyan][3/3] Comparing live vs NetBox...[/bold cyan]")
    live_nets  = extract_live_networks(results)
    comparison = compare_prefixes(nb_prefixes, live_nets)

    # Print summary table
    table = Table(title="Comparison Summary")
    table.add_column("Category", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("[green]Matched[/green]",      str(len(comparison["matched"])))
    table.add_row("[yellow]NetBox Only[/yellow]", str(len(comparison["netbox_only"])))
    table.add_row("[red]Live Only[/red]",         str(len(comparison["live_only"])))
    console.print(table)

    if comparison["live_only"]:
        console.print("\n[red]🔴 Live subnets MISSING from NetBox:[/red]")
        for n in comparison["live_only"]:
            console.print(f"   {n}")

    if comparison["netbox_only"]:
        console.print("\n[yellow]⚠️  NetBox prefixes NOT seen live:[/yellow]")
        for n in comparison["netbox_only"]:
            console.print(f"   {n}")

    # Write report
    fname = args.output or f"{site.upper()}_Audit_{datetime.now().strftime('%Y%m%d')}.md"
    out_path = OUTPUT_DIR / fname
    report = generate_report(site, results, nb_prefixes, nb_vlans, comparison)
    out_path.write_text(report)
    console.print(f"\n[green]✅ Report written to: {out_path}[/green]")


if __name__ == "__main__":
    main()
