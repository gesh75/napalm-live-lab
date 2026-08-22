# Lab topologies

These files are the **public** bring-up path. Dashboard node names, IPs,
drivers, and **container names** in `config.py` are derived from them —
keep them in sync.

| Fabric | Topology | Container names |
|---|---|---|
| CLOS-EVPN | `clos-evpn.clab.yml` | `clab-clos-evpn-<node>` (default clab prefix — do **not** set `prefix: ""`) |
| 3-Tier FRR | `dcn-3tier.clab.yml` | bare `<node>` (`prefix: ""` is required) |

```bash
# CLOS-EVPN (Arista cEOS + Nokia SR Linux + FRR)
sudo containerlab deploy -t topologies/clos-evpn.clab.yml

# 3-Tier FRR
sudo containerlab deploy -t topologies/dcn-3tier.clab.yml

# Collector sidecar (real NAPALM for eos/srl)
./lab_runner/up.sh
```

What these files actually give you:

- Management IPs matching `config.py`
- FRR `zebra` + `bgpd` enabled (`frr/daemons`) so `vtysh` works
- cEOS eAPI enabled (`startup/ceos.cfg`) so the core `eos` driver can connect
- Designed CLOS / 3-tier links

What they do **not** give you (yet): full BGP/EVPN neighbor configs. Those
still live in post-deploy scripts. Without them, nodes are reachable and
NAPALM facts work; BGP sessions will be empty until you push config.
`leaf6↔spine2` Idle is an intentional live-lab fault, not something this
YAML encodes.

Images (`ceos:4.33.1F`, `ghcr.io/nokia/srlinux`, `quay.io/frrouting/frr`) are
not shipped. cEOS is licensed from Arista; SR Linux is Nokia's public container.
