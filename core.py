#!/usr/bin/env python3
"""
Core NAPALM connection and data collection helpers.
Used by all other scripts — handles connect/disconnect, retries, structured getters.
"""

import os
import ipaddress
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import napalm
import requests
import urllib3
urllib3.disable_warnings()

from config import SSH_USER, SSH_KEY, SSH_TIMEOUT, NETBOX_URL, NETBOX_TOKEN, ZTASID


# ── NAPALM device connection ───────────────────────────────────────────────────

def get_driver(driver_name: str):
    return napalm.get_network_driver(driver_name)


def open_device(hostname: str, ip: str, driver_name: str):
    """
    Open a NAPALM connection to a device.
    Returns an open device object or None on failure.
    """
    driver = get_driver(driver_name)
    optional_args = {
        "key_file": SSH_KEY,
        "ssh_config_file": None,
        "timeout": SSH_TIMEOUT,
    }
    if driver_name == "junos":
        optional_args["port"] = 22

    device = driver(
        hostname=ip,
        username=SSH_USER,
        password="",
        optional_args=optional_args,
    )
    try:
        device.open()
        return device
    except Exception as e:
        print(f"  ✗ {hostname} ({ip}): {e}")
        return None


def collect_device(hostname: str, ip: str, driver_name: str, getters: list) -> dict:
    """
    Connect to a device and run a list of NAPALM getter names.
    Returns a dict with results keyed by getter name.

    getters: list of strings, e.g. ["get_facts", "get_interfaces_ip", "get_bgp_neighbors"]
    """
    print(f"  → {hostname} ({ip}) [{driver_name}] ...", end=" ", flush=True)
    result = {
        "hostname": hostname,
        "ip": ip,
        "driver": driver_name,
        "data": {},
        "error": None,
    }

    device = open_device(hostname, ip, driver_name)
    if device is None:
        result["error"] = "Connection failed"
        print("FAILED")
        return result

    print("OK")
    for getter in getters:
        try:
            fn = getattr(device, getter)
            result["data"][getter] = fn()
        except Exception as e:
            result["data"][getter] = None
            print(f"    ⚠ {getter} failed: {e}")

    try:
        device.close()
    except Exception:
        pass

    return result


def collect_site_parallel(site_devices: dict, getters: list, max_workers: int = 5) -> dict:
    """
    Collect data from all devices in a site in parallel.
    site_devices: { hostname: { "ip": ..., "driver": ... } }
    Returns: { hostname: result_dict }
    """
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(collect_device, hostname, info["ip"], info["driver"], getters): hostname
            for hostname, info in site_devices.items()
            if info.get("ip")
        }
        for future in as_completed(futures):
            hostname = futures[future]
            try:
                results[hostname] = future.result()
            except Exception as e:
                results[hostname] = {"hostname": hostname, "error": str(e), "data": {}}
    return results


# ── NetBox helpers ─────────────────────────────────────────────────────────────

def _nb_headers():
    h = {
        "Authorization": f"Token {NETBOX_TOKEN}",
        "Accept": "application/json",
    }
    if ZTASID:
        h["Cookie"] = f"ztasid={ZTASID}"
    return h


def nb_get(path: str, params: dict = None) -> list:
    """Paginated NetBox GET — returns all results."""
    url = f"{NETBOX_URL}/api/{path}"
    results = []
    while url:
        r = requests.get(url, headers=_nb_headers(), params=params, verify=False, timeout=15)
        if r.status_code == 401:
            raise PermissionError(
                "NetBox returned 401 — session cookie has expired.\n"
                "  Refresh from browser DevTools → Application → Cookies on your NetBox host\n"
                "  Then pass it: ZTASID='eyJ...' python3 audit.py --site <site>"
            )
        r.raise_for_status()
        d = r.json()
        results.extend(d.get("results", []))
        url = d.get("next")
        params = None  # only pass params on first request
    return results


def nb_get_prefixes(site: str, status: str = "active") -> list:
    return nb_get("ipam/prefixes/", {"site": site, "status": status, "limit": 200})


def nb_get_devices(site: str) -> list:
    return nb_get("dcim/devices/", {"site": site, "limit": 200})


def nb_get_vlans(site: str) -> list:
    return nb_get("ipam/vlans/", {"site": site, "limit": 200})


# ── Subnet extraction from NAPALM data ────────────────────────────────────────

def extract_live_networks(results: dict) -> list:
    """
    From a collect_site_parallel() results dict,
    extract all unique ip_network objects from get_interfaces_ip data.
    """
    networks = set()
    for hostname, res in results.items():
        if res.get("error"):
            continue
        iface_ip = res.get("data", {}).get("get_interfaces_ip") or {}
        for iface, families in iface_ip.items():
            for family, addrs in families.items():
                for addr, meta in addrs.items():
                    try:
                        prefix_len = meta.get("prefix_length", 32)
                        net = ipaddress.ip_interface(f"{addr}/{prefix_len}").network
                        networks.add(net)
                    except ValueError:
                        pass
    return sorted(networks, key=lambda n: (n.version, n))


def compare_prefixes(netbox_prefixes: list, live_networks: list) -> dict:
    """
    Compare NetBox prefixes vs live subnets.
    Returns: { matched, netbox_only, live_only }
    """
    nb_nets = set()
    for p in netbox_prefixes:
        try:
            nb_nets.add(ipaddress.ip_network(p["prefix"]))
        except ValueError:
            pass

    live_nets = set(live_networks)
    return {
        "matched":     sorted(nb_nets & live_nets,     key=lambda n: (n.version, n)),
        "netbox_only": sorted(nb_nets - live_nets,     key=lambda n: (n.version, n)),
        "live_only":   sorted(live_nets - nb_nets,     key=lambda n: (n.version, n)),
    }
