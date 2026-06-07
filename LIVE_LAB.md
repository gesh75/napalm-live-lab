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
| `lab.html` / `lab.js` / `lab.css` | The `/lab` UI (GitHub-dark, matrix + SVG topology, auto-refresh 15s) |
| `lab_runner/` | `Dockerfile` + `collect.py` (real NAPALM) + `up.sh` (build & attach) + README |
| `tests/test_napalm_lab.py` | 28 hermetic tests (config/security, mapping, backend, matrix math, topology) |

## Endpoints (added)

- `GET /api/lab/fabrics` · `GET /api/lab/matrix?fabric=clos|dcn|all`
- `GET /api/lab/topology?fabric=clos|dcn` · `GET /api/lab/node/<hostname>`

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
