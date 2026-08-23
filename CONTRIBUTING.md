# Contributing

## Tests

Hermetic (no Docker, no live fabric):

```bash
pip install -r requirements.txt
pytest tests/ -q
```

CI runs the same suite on every push.

## Security rules for the command console

- `docker exec` is always an argv list. Never `shell=True`.
- Hostnames must be in `config.NODE_INDEX`.
- Read-only = first token in `{show, display, get, ping, traceroute, info}`.
- Pipe segments must be display filters. `redirect`, `append`, `bash`, `sh` are writes.
- Do not tag Cisco/Juniper catalog commands as `runnable_on` FRR unless the command is a known-good vtysh equivalent.

## Topology vs config

Node names, management IPs, and drivers in `config.py` must match `topologies/*.clab.yml`.
