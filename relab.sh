#!/usr/bin/env bash
# ============================================================================
#  relab.sh — bring the NAPALM Live Lab back to GREEN in one command.
#
#  WHY THIS EXISTS
#  After a Docker Desktop restart (or a reboot), the heavy containerlab nodes
#  (Arista cEOS, Nokia SR Linux) often don't recover, and even when they do, a
#  plain `docker start` leaves the clab data-plane veths DESTROYED — so BGP
#  comes up Idle(NoIf). The only correct fix is `containerlab deploy
#  --reconfigure`, followed by the SRL/FRR post-deploy config push. This script
#  automates that whole sequence and then verifies the dashboard is green.
#
#  USAGE
#    ./relab.sh            # full repair: redeploy CLOS + post-deploy + verify
#    ./relab.sh verify     # just check the live coverage matrix (no changes)
#
#  CONFIG (env overrides)
#    CLAB_DIR       path to the companion containerlab-multivendor dir
#                   (default: ../DCN_Network_Tool/containerlab-multivendor)
#    CLOS_TOPO      topology filename                (default: clos-evpn.clab.yml)
#    CLAB_IMAGE     clab DooD image    (default: ghcr.io/srl-labs/clab:latest)
#    DASHBOARD_URL  dashboard base url      (default: http://127.0.0.1:5959)
#    SRL_WAIT       seconds to wait for SRL boot before config push (default 90)
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAB_DIR="${CLAB_DIR:-$SCRIPT_DIR/../DCN_Network_Tool/containerlab-multivendor}"
CLOS_TOPO="${CLOS_TOPO:-clos-evpn.clab.yml}"
CLAB_IMAGE="${CLAB_IMAGE:-ghcr.io/srl-labs/clab:latest}"
DASHBOARD_URL="${DASHBOARD_URL:-http://127.0.0.1:5959}"
SRL_WAIT="${SRL_WAIT:-90}"

B=$'\033[1m'; G=$'\033[0;32m'; Y=$'\033[1;33m'; R=$'\033[0;31m'; C=$'\033[0;36m'; N=$'\033[0m'
say()  { echo -e "${C}[relab]${N} $*"; }
ok()   { echo -e "${G}[ ok ]${N} $*"; }
warn() { echo -e "${Y}[warn]${N} $*"; }
die()  { echo -e "${R}[fail]${N} $*"; exit 1; }

# ── verify: print the live coverage matrix summary ──────────────────────────
verify() {
  say "checking live matrix at ${DASHBOARD_URL}/api/lab/matrix ..."
  curl -fsS -m120 "${DASHBOARD_URL}/api/lab/matrix?fabric=all" | python3 -c '
import sys, json
d = json.load(sys.stdin); s = d["summary"]
print("  total=%d  reachable=%d  napalm_native=%d  exec_fallback=%d" % (
      s["total"], s["reachable"], s["napalm_native"], s["exec_fallback"]))
bad = [n["hostname"] for n in d["nodes"] if not n.get("reachable")]
print("  unreachable:", ", ".join(bad) if bad else "none - all green")
sys.exit(0 if not bad else 2)
' && ok "all nodes reachable" || warn "some nodes still unreachable (see above)"
}

if [[ "${1:-}" == "verify" ]]; then verify; exit $?; fi

# ── 0. preflight ────────────────────────────────────────────────────────────
[[ -d "$CLAB_DIR" ]] || die "CLAB_DIR not found: $CLAB_DIR (set CLAB_DIR=... to the containerlab-multivendor dir)"
[[ -f "$CLAB_DIR/topologies/$CLOS_TOPO" ]] || die "topology not found: $CLAB_DIR/topologies/$CLOS_TOPO"

if ! docker info >/dev/null 2>&1; then
  warn "Docker is not running — attempting to start Docker Desktop ..."
  open -a "Docker Desktop" 2>/dev/null || open -a Docker 2>/dev/null || die "could not start Docker"
  for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 6; done
  docker info >/dev/null 2>&1 || die "Docker did not come up in time"
fi
ok "Docker daemon up"

# ── 1. napalm-runner sidecar ────────────────────────────────────────────────
if docker ps --format '{{.Names}}' | grep -qx napalm-runner; then
  ok "napalm-runner already up"
elif [[ -x "$SCRIPT_DIR/lab_runner/up.sh" ]]; then
  say "starting napalm-runner sidecar ..."
  ( cd "$SCRIPT_DIR/lab_runner" && ./up.sh ) && ok "napalm-runner up"
else
  warn "napalm-runner not running and lab_runner/up.sh not found — eos/srl collection will fail"
fi

# ── 2. 3-Tier FRR network (docker-compose) — usually auto-recovers ──────────
if docker ps --format '{{.Names}}' | grep -qx de-fra-core-01; then
  ok "3-Tier FRR network up"
else
  COMPOSE="$CLAB_DIR/../network-lab/docker-compose.yml"
  if [[ -f "$COMPOSE" ]]; then
    say "bringing up 3-Tier FRR network ..."
    docker compose -f "$COMPOSE" up -d >/dev/null 2>&1 && ok "3-Tier up" || warn "3-Tier compose up failed"
  else
    warn "3-Tier compose not found at $COMPOSE — skipping"
  fi
fi

# ── 3. CLOS fabric: clab deploy --reconfigure (the veth-repair fix) ─────────
say "redeploying CLOS fabric via clab --reconfigure (recreates veths) ..."
docker run --rm \
  --privileged --network host --pid host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$CLAB_DIR":"$CLAB_DIR" \
  -w "$CLAB_DIR/topologies" \
  "$CLAB_IMAGE" \
  containerlab deploy -t "$CLOS_TOPO" --reconfigure
ok "CLOS fabric deployed"

# ── 4. post-deploy config push (SRL + FRR VTEP) ─────────────────────────────
if [[ -f "$CLAB_DIR/scripts/post-deploy-srl.sh" ]]; then
  say "pushing SR Linux config (waits ${SRL_WAIT}s for boot) ..."
  ( cd "$CLAB_DIR/scripts" && bash post-deploy-srl.sh "$SRL_WAIT" ) && ok "SRL config pushed"
else
  warn "post-deploy-srl.sh not found — SRL nodes may have no BGP config"
fi
if [[ -f "$CLAB_DIR/scripts/setup_frr_vtep.sh" ]]; then
  say "setting up FRR VTEPs ..."
  ( cd "$CLAB_DIR/scripts" && bash setup_frr_vtep.sh ) && ok "FRR VTEPs set"
fi

# ── 5. converge + verify ────────────────────────────────────────────────────
say "waiting 45s for BGP/EVPN convergence + eAPI/JSON-RPC readiness ..."
sleep 45
echo
echo -e "${B}── NAPALM Live Lab — coverage ──${N}"
verify
echo
ok "relab complete → open ${DASHBOARD_URL}/lab"
