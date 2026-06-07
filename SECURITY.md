# Security Policy

The NAPALM Live Lab is a **local lab / learning tool**. It is designed to run on
`127.0.0.1` against a local [containerlab](https://containerlab.dev/) fabric. The
notes below describe the security model and how to harden it if you expose it.

## Security model

| Control | Implementation |
|---|---|
| **No shell injection** | Every device command runs as an `argv` **list** (`docker exec <c> <wrapper> -c <command>`) via `subprocess.run` — never `shell=True`, never string-concatenated. |
| **Target allowlist** | `command_lib.run_command` rejects any hostname not in `config.NODE_INDEX`. Arbitrary container names can never reach `docker exec`. |
| **Read-only by default** | Only operational commands (`show`/`display`/`get`/`ping`/…) run by default. Mutating verbs (`configure`, `no`, `delete`, `commit`, `write`, `reload`, `clear`, …) are blocked unless write mode is explicitly enabled. |
| **No command smuggling** | Commands containing newlines or control characters are rejected, so a second command cannot be appended past the read-only check. Commands are length-capped. |
| **Hard read-only switch** | Set `LAB_CONSOLE_READONLY=1` to disable write mode entirely, regardless of the UI toggle. Recommended for any shared/exposed deployment. |
| **No hardcoded secrets** | NetBox URL/token and all device credentials are read from environment variables. The repository contains no real secrets. |

## Credentials

The default lab credentials (`admin`/`admin` for Arista cEOS, `admin`/`NokiaSrl1!`
for Nokia SR Linux) are the **well-known public containerlab defaults** for a local
sandbox. They are **not** secrets and are overridable via environment variables
(`NAPALM_EOS_USER/PASS`, `NAPALM_SRL_USER/PASS`). Do not point this tool at
production devices with these defaults.

## Hardening checklist (if you expose it beyond localhost)

- [ ] `export LAB_CONSOLE_READONLY=1` to disable write commands.
- [ ] Put it behind an authenticating reverse proxy (it has no built-in auth).
- [ ] Run `python3 dashboard.py` with `debug=False`.
- [ ] Restrict the Docker socket — the tool can `docker exec` into the lab nodes.
- [ ] Override the default lab credentials via environment variables.

## Reporting a vulnerability

This is a personal lab project. If you find a security issue, please open a
GitHub issue (omit any sensitive details) or contact the maintainer directly.
