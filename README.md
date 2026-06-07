# NAPALM Network Automation

Unified network data collection and auditing using [NAPALM](https://napalm.readthedocs.io/).  
Replaces all legacy `paramiko` / `netmiko` / raw SSH scripts in this workspace.

Includes a **Web Dashboard** (Flask, port 5959) with 8 network operations tools.

---

## Web Dashboard

```bash
cd 04_Scripts_Tools/napalm_network
source venv/bin/activate
python3 dashboard.py
# Open http://localhost:5959
```

### Tools (8 total)

| # | Tool | Badge | What It Does |
|---|------|-------|-------------|
| 1 | **Version Audit** | NEW | OS version inventory across all sites. Detects mismatches within same model/platform |
| 2 | **BGP Status** | CORE | Live BGP neighbor table — peer state, prefix counts, uptime. Highlights down peers |
| 3 | **NetBox Audit** | CORE | Compare live IP interfaces vs NetBox prefixes. Find undocumented/missing subnets |
| 4 | **Environment Health** | NEW | CPU, memory, temperature, fans, PSU status. Flags critical thresholds |
| 5 | **Interface Errors** | NEW | Find ports with CRC errors, discards, drops. Sorted by severity |
| 6 | **LLDP Topology** | NEW | Discover physical topology via LLDP neighbors. Validate cabling |
| 7 | **Full Collection** | CORE | Pull all data (interfaces, IPs, LLDP, ARP, counters, env). Saves JSON + Markdown |
| 8 | **Pre/Post Diff** | NEW | Take snapshots before/after maintenance. Compare interface state, BGP, IPs |

### How It Works

1. Select a **site** (ACH1, AUH1, PHX1) from the sidebar
2. Click a **tool** button
3. Press **▶ Run** — jobs execute in background with live progress
4. Results render as tables, alerts, and topology maps
5. Recent jobs are tracked in the sidebar

---

## What's Here

| File | Purpose |
|---|---|
| `dashboard.py` | Flask web dashboard — 8 tools, async jobs, REST API (~500 lines) |
| `dashboard.html` | Dark-themed single-page UI with sidebar navigation |
| `dashboard.js` | Frontend JS — API calls, progress polling, result renderers (~600 lines) |
| `audit.py` | CLI: Live IP interfaces vs NetBox comparison |
| `bgp.py` | CLI: BGP neighbor status across a site |
| `site_collect.py` | CLI: Full site data collection |
| `core.py` | Shared library — NAPALM connection, NetBox API, parallel collection |
| `config.py` | Central config — SSH credentials, device inventory, NetBox token |

---

## Setup

```bash
# Create virtual environment
cd 04_Scripts_Tools/napalm_network
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## Authentication

**SSH:** Uses `gandalf` user with key at `~/Downloads/gandalf.txt` (set in `config.py`).  
Override with environment variable:
```bash
export SSH_KEY=/path/to/your/key
```

**NetBox:** API token already set in `config.py`.  
Override:
```bash
export NETBOX_TOKEN=your_token_here
export ZTASID=your_ztasid_jwt_here   # if ztasid has expired
```

---

## Usage

### 1. Audit — Live vs NetBox (start here for ACH1)

```bash
# Full audit: SSH to all ACH1 devices + compare against NetBox
python3 audit.py --site dc1

# Single device only
python3 audit.py --site dc1 --device dc1-fw-20a

# NetBox only (no SSH) — useful to test auth first
python3 audit.py --site dc1 --no-ssh

# Other sites
python3 audit.py --site dc2
python3 audit.py --site dc3
```

### 2. BGP Live Status

```bash
# PHX1 BGP neighbors (Juniper + Arista)
python3 bgp.py --site dc3

# ACH1 firewall BGP
python3 bgp.py --site dc1

# Single device
python3 bgp.py --site dc3 --device dc3-rt-01
```

### 3. Full Site Collection

```bash
# Collect everything: interfaces, IPs, LLDP, ARP, counters, environment
python3 site_collect.py --site dc1
python3 site_collect.py --site dc2
python3 site_collect.py --site dc3

# All sites at once
python3 site_collect.py --all

# Single device
python3 site_collect.py --site dc1 --device dc1-sw-01a
```

---

## Output

All output files go to `output/` directory:

| File | Contents |
|---|---|
| `ACH1_Audit_YYYYMMDD.md` | NetBox vs live comparison report |
| `PHX1_BGP_YYYYMMDD_HHMMSS.md` | BGP peer status table |
| `PHX1_BGP_YYYYMMDD_HHMMSS.json` | Raw NAPALM BGP data |
| `ACH1_Collection_YYYYMMDD_HHMMSS.md` | Full site collection report |
| `ACH1_Collection_YYYYMMDD_HHMMSS.json` | Raw NAPALM structured data |

---

## Adding a New Site

Edit `config.py` and add to the `SITES` dict:

```python
SITES = {
    ...
    "lhr3": {
        "lhr3-fw-20a": {"ip": "10.x.x.x",  "driver": "junos"},
        "lhr3-sw-01a": {"ip": "10.x.x.x",  "driver": "junos"},
        "lhr3-rt-01":  {"ip": "10.x.x.x",  "driver": "junos"},
    },
}
```

Then run any script with `--site lhr3`.

---

## NAPALM Getters Reference

| Getter | Returns |
|---|---|
| `get_facts` | hostname, vendor, model, os_version, uptime, serial |
| `get_interfaces_ip` | all IP addresses per interface (structured dict) |
| `get_interfaces` | admin/oper state, speed, MTU, description |
| `get_interfaces_counters` | TX/RX bytes, errors, discards |
| `get_bgp_neighbors` | BGP peers, state, prefix counts per VRF |
| `get_bgp_neighbors_detail` | detailed BGP peer data |
| `get_lldp_neighbors` | LLDP neighbor table |
| `get_arp_table` | ARP table with MAC/IP/interface |
| `get_environment` | CPU, memory, temperature, fans, power |

---

## Supported Drivers

| Driver | OS | Sites |
|---|---|---|
| `junos` | Juniper JunOS (SRX, EX, MX, QFX) | ACH1 fw/sw, PHX1 rt/sw, AUH1 fw/sw |
| `eos` | Arista EOS | PHX1-rt-02, AUH1-sw-04 |
