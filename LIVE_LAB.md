# NAPALM Live Lab — CLOS-EVPN + 3-Tier (added 2026-06-06)

The dashboard at **http://127.0.0.1:5959/lab** shows the **live containerlab fabrics**
running on this host and how each device performs against **NAPALM**.

Previously `config.py` pointed at unreachable ex-company production sites and hardcoded
an ex-employer NetBox token — both have been removed.

## What you get

- **`/lab`** — live page: a NAPALM coverage matrix + spine/leaf and core/edge/dist topology.
- **NAPALM coverage matrix** — per node: vendor, driver, method, reachability, per-getter
  pass/fail, latency. The honest story:
  | Vendor | Driver | Path | NAPALM |
  |---|---|---|---|
  | Arista cEOS | `eos` (napalm core) | eAPI / HTTPS | ✅ full (6/6 getters) |
  | Nokia SR Linux | `srl` (napalm-srl community) | JSON-RPC / gNMI | ✅ facts/intf (get_bgp_neighbors has a known napalm-srl parse gap → ⚠) |
  | FRR | **none** (no NAPALM driver) | `docker exec vtysh` | exec fallback (facts/intf/BGP; no LLDP/env) |

## Architecture — why a sidecar

macOS Docker Desktop cannot route to container management IPs (`172.20.20.x`,
`10.200.0.x`). So real NAPALM runs inside the **`napalm-runner`** container, attached to
both lab management networks (`clos-mgmt`, `dcn-lab_lab-net`). The dashboard (host) calls it
with `docker exec napalm-runner python3 /runner/collect.py '<json>'`. FRR has no NAPALM
driver, so the host collects it directly via `docker exec <node> vtysh`.

```
dashboard (:5959, host)
   ├─ eos/srl ─► docker exec napalm-runner ─► real NAPALM ─► clab mgmt API
   └─ frr     ─► docker exec clab-<node> vtysh
```

## Files

| File | Purpose |
|---|---|
| `config.py` | `FABRICS` / `NODE_INDEX` (19 live nodes), driver+vendor map, runner creds (env). **No secrets.** |
| `napalm_lab.py` | Collection backend: runner dispatch (eos/srl) + vtysh (frr), matrix, topology, legacy shims |
| `command_lib.py` | Command Console backend: catalog loader + `run_command`/`run_getter` with read-only guard |
| `build_command_catalog.py` | Builds `command_catalog.json` from the private CLI corpus (corpus is **not** shipped) |
| `command_catalog.json` | 2,381 curated single-line operational commands (public-safe; browsed + run by the console) |
| `lab.html` / `lab.js` / `lab.css` | The `/lab` UI (GitHub-dark, matrix + SVG topology + Command Console, auto-refresh 15s) |
| `lab_runner/` | `Dockerfile` + `collect.py` (real NAPALM) + `up.sh` (build & attach) + README |
| `tests/test_napalm_lab.py` | 28 hermetic tests (config/security, mapping, backend, matrix math, topology) |
| `tests/test_command_lib.py` | 35 hermetic tests (read-only guard, allowlist, wrapper-per-driver, catalog shape) |

## Command Console — run a multivendor command library against the live lab

The `/lab` page includes a **Command Console**: a curated, searchable library of
**2,381 single-line operational commands** distilled from a private multivendor CLI
corpus (Arista / Cisco / Juniper), plus per-vendor curated quick-commands and the
NAPALM getters. Pick a node, pick (or type) a command, and run it live.

How a command reaches the device — by the node's driver:

| Vendor (driver) | Wrapper | Example |
|---|---|---|
| Arista cEOS (`eos`) | `Cli -c` | `docker exec clab-clos-evpn-leaf1 Cli -c "show ip bgp summary"` |
| FRR (`frr`) | `vtysh -c` | `docker exec de-fra-core-01 vtysh -c "show ip route"` |
| Nokia SR Linux (`srl`) | `sr_cli` | `docker exec clab-clos-evpn-spine1 sr_cli "show version"` |
| any (`napalm`) | NAPALM getter | structured JSON via `collect_node(host, [getter])` |

**Security (it ships publicly, so it defaults safe):**

- Target host must be in the `NODE_INDEX` allowlist — arbitrary container names never reach `docker exec`.
- Commands run as an **argv list** (no shell) — no shell-injection surface.
- **Read-only guard:** only `show`/`display`/`get`/`ping`/… run by default. Mutating verbs
  (`configure`, `no`, `delete`, `commit`, `write`, `reload`, `clear`, …) are blocked unless write
  mode is explicitly enabled. Newlines/control chars are rejected (no command smuggling).
- `LAB_CONSOLE_READONLY=1` hard-disables write mode entirely. See `SECURITY.md`.

Rebuild the catalog after editing the corpus:

```bash
python3 build_command_catalog.py     # writes command_catalog.json
```

## Endpoints (added)

- `GET /api/lab/fabrics` · `GET /api/lab/matrix?fabric=clos|dcn|all`
- `GET /api/lab/topology?fabric=clos|dcn` · `GET /api/lab/node/<hostname>`
- `GET /api/lab/commands` (catalog) · `GET /api/lab/console/nodes` (run targets)
- `POST /api/lab/run` `{hostname, command, allow_write}` · `POST /api/lab/getter` `{hostname, getter}`

## Operate

```bash
# (Re)build the collector sidecar after any clab redeploy:
cd lab_runner && ./up.sh

# Restart the dashboard (launchd):
launchctl kickstart -k gui/$(id -u)/com.geshlab.napalm

# Tests:
./venv/bin/python -m pytest tests/test_napalm_lab.py -q
```

## cEOS eAPI — durable now (lesson learned)

cEOS ships with eAPI disabled and its uwsgi backend only spawns at boot, so eAPI is now
baked into the cEOS **startup-configs** (`containerlab-multivendor/configs/{leaf,spine}/*-ceos.cfg`):

```
username admin privilege 15 role network-admin secret admin
management api http-commands
   no shutdown
```

⚠️ **Do not `docker restart` a clab cEOS node to fix eAPI** — it destroys the containerlab
veth pairs (and their peers' interfaces), breaking the fabric. Re-wire with a full
`containerlab deploy --reconfigure` (via the `ghcr.io/srl-labs/clab` image on macOS), then
re-run `scripts/post-deploy-srl.sh` + `scripts/setup_frr_vtep.sh`. Because eAPI is in the
startup-config, a clean redeploy brings it up correctly with no manual restart.
