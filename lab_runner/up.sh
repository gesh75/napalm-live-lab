#!/usr/bin/env bash
#
# up.sh — idempotently (re)build and run the NAPALM collector sidecar.
#
# Why: on macOS Docker Desktop the host cannot route to containerlab mgmt IPs
# (e.g. 172.20.20.0/24). This sidecar runs INSIDE Docker, attached to both lab
# management networks, so napalm can actually reach the nodes. The dashboard
# then `docker exec`s into it to run collect.py.
#
# Safe to run repeatedly: rebuilds the image, replaces the container, and
# (re)connects it to both networks tolerating already-connected errors.

set -euo pipefail

IMAGE="napalm-runner:latest"
NAME="napalm-runner"
NETWORKS=("clos-mgmt" "dcn-lab_lab-net")

# Resolve this script's directory so the build context is correct regardless of cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Building image ${IMAGE} from ${SCRIPT_DIR}"
docker build -t "${IMAGE}" "${SCRIPT_DIR}"

# Remove any pre-existing container with the same name (idempotent restart).
if docker ps -a --format '{{.Names}}' | grep -qx "${NAME}"; then
    echo "==> Removing existing container ${NAME}"
    docker rm -f "${NAME}" >/dev/null
fi

echo "==> Starting container ${NAME} (detached, --restart unless-stopped)"
docker run -d \
    --name "${NAME}" \
    --restart unless-stopped \
    "${IMAGE}" >/dev/null

# Connect to both lab management networks. Tolerate:
#   - network already connected (re-runs)
#   - network not existing yet (lab not up) -> warn, continue
for net in "${NETWORKS[@]}"; do
    echo "==> Connecting ${NAME} to network ${net}"
    if docker network connect "${net}" "${NAME}" 2>/tmp/napalm_runner_netconn.err; then
        echo "    connected to ${net}"
    else
        err="$(cat /tmp/napalm_runner_netconn.err || true)"
        if echo "${err}" | grep -qiE 'already exists|already connected'; then
            echo "    already connected to ${net} (ok)"
        elif echo "${err}" | grep -qiE 'not found|No such network'; then
            echo "    WARNING: network ${net} not found (is the lab up?) — skipping"
        else
            echo "    WARNING: could not connect to ${net}: ${err}"
        fi
    fi
done
rm -f /tmp/napalm_runner_netconn.err 2>/dev/null || true

echo
echo "==> Success: ${NAME} is running and attached to lab networks."
echo "    Image:      ${IMAGE}"
echo "    Container:  ${NAME}"
echo "    Networks:   ${NETWORKS[*]}"
echo "    Exec usage: docker exec ${NAME} python3 /runner/collect.py '<json>'"
echo

echo "==> Self-test (napalm import inside container):"
docker exec "${NAME}" python3 -c "import napalm,napalm_srl;print('napalm ok')"
