# NAPALM Collector Sidecar (`lab_runner`)

A long-lived container that runs **real NAPALM** against live containerlab nodes
on behalf of the DCN dashboard (Flask, `http://127.0.0.1:5959`).

## Why this exists

On **macOS Docker Desktop the host cannot route to container management IPs**
(e.g. `172.20.20.0/24` for the CLOS lab, or the `dcn-lab` net). The Docker VM
NAT does not expose those subnets to the Mac host network stack, so napalm run
directly on the Mac would simply time out.

The fix: run napalm **inside a container that is attached to the lab management
networks**. The dashboard backend then shells in via
`docker exec napalm-runner python3 /runner/collect.py '<json>'` to collect data.

## What it is

- `collect.py` — self-contained collector (stdlib + `napalm` + `napalm-srl`).
- `Dockerfile` — `python:3.12-slim` + `pip install napalm napalm-srl`, copies
  `collect.py` to `/runner/collect.py`, `sleep infinity` so it stays up, with a
  healthcheck that imports `napalm` + `napalm_srl`.
- `up.sh` — idempotent build + (re)run + attach to both lab networks.

## How to (re)build / run

```bash
./up.sh
```

This builds `napalm-runner:latest`, removes any existing `napalm-runner`
container, runs a fresh one detached (`--restart unless-stopped`), connects it to
both `clos-mgmt` and `dcn-lab_lab-net` (tolerating already-connected / missing
networks), then prints a self-test (`import napalm,napalm_srl;print('napalm ok')`).

## `collect.py` contract

Takes **one** JSON argument on `argv[1]` and prints **exactly one** JSON line to
stdout — the **last** stdout line always starts with `{`.

**Input:**
```json
{"ip":"172.20.20.21","driver":"eos",
 "getters":["get_facts","get_interfaces","get_bgp_neighbors"],
 "username":"admin","password":"admin"}
```

**Output:**
```json
{"ok":true,"reachable":true,"method":"napalm","driver":"eos","latency_ms":412,
 "facts":{"hostname":"leaf1","vendor":"Arista","model":"cEOS",
          "os_version":"4.33.1F","serial_number":"ABC","uptime":123456,
          "interface_list":["Ethernet1","Management0"]},
 "data":{"get_facts":{...},"get_interfaces":{...},"get_bgp_neighbors":{...}},
 "getters":{"get_facts":{"ok":true,"error":null},
            "get_bgp_neighbors":{"ok":true,"error":null}},
 "error":null}
```

Guarantees:
- Never raises — always emits one JSON line (even on bad input or import failure).
- `reachable` is `true` **only** if `driver.open()` succeeds.
- Each getter runs in its own try/except; per-getter `ok`/`error` recorded under
  `getters`, raw result stored under `data[<getter>]`.
- `facts` is flattened from `get_facts` (`hostname, vendor, model, os_version,
  serial_number, uptime, interface_list`); all keys always present.
- `driver.close()` always runs in a `finally`. `latency_ms` = total elapsed.

## Per-vendor driver mapping

| Vendor | Driver | NAPALM source | Transport | Notes |
|--------|--------|---------------|-----------|-------|
| Arista | `eos` | napalm (core) | eAPI / HTTPS:443 | pyeapi does not verify the self-signed cert by default — fine. |
| Nokia  | `srl` | napalm-srl (community) | JSON-RPC / gNMI over HTTPS:443 | `optional_args` prefer JSON-RPC; verify keys against installed napalm-srl version (see caveat below). |
| FRR    | `none` | **no napalm driver** | — | Handled by the backend via `docker exec <node> vtysh`, **not** this runner. |

`collect.py` only knows `eos` and `srl`. FRR nodes must be collected by the
backend's exec fallback path; calling this runner with `driver:"none"`/`"frr"`
returns `ok:false` with `"unknown driver"`.

## napalm-srl optional_args caveat

The `srl` driver's `optional_args` keys have changed across napalm-srl releases.
This runner currently passes `{"jsonrpc_port":443,"transport":"https","insecure":true}`.
Some versions instead expect `port` / `gnmi_port` / `tls=False` / different key
names. If `srl` nodes return `open() failed`, verify the exact accepted keys for
the installed napalm-srl version (`pip show napalm-srl`) and adjust
`_build_driver()` accordingly.
