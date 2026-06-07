FROM python:3.12-slim

# Real NAPALM core (eos/junos/ios/nxos/iosxr) + napalm-srl community driver (Nokia SR Linux).
RUN pip install --no-cache-dir napalm napalm-srl

# The collector script. Executed via `docker exec napalm-runner python3 /runner/collect.py '<json>'`.
COPY collect.py /runner/collect.py

WORKDIR /runner

# Verify both napalm and the srl driver import cleanly inside the running container.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python3 -c "import napalm, napalm_srl" || exit 1

# Long-lived container so the dashboard can `docker exec` into it repeatedly.
CMD ["sleep", "infinity"]
