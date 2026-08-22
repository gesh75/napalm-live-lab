# Lab topologies

These files are the **public** bring-up path. The dashboard node names, IPs,
and drivers in `config.py` are derived from them — keep them in sync.

```bash
# CLOS-EVPN (Arista cEOS + Nokia SR Linux + FRR)
sudo containerlab deploy -t topologies/clos-evpn.clab.yml

# 3-Tier FRR
sudo containerlab deploy -t topologies/dcn-3tier.clab.yml

# Collector sidecar (real NAPALM for eos/srl)
./lab_runner/up.sh
```

Images (`ceos:4.33.1F`, `ghcr.io/nokia/srlinux`, `quay.io/frrouting/frr`) are
not shipped. cEOS is licensed from Arista; SR Linux is Nokia's public container.
If a kind is missing locally, containerlab will pull it.

Startup-config snippets that enable cEOS eAPI live under `startup/` next to the
topology when you add them; without them, the dashboard falls back to `Cli`.
