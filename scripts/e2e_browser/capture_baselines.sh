#!/usr/bin/env bash
# Orchestrate the Playwright browser-e2e stack locally.
#
# Usage:
#   bash scripts/e2e_browser/capture_baselines.sh <mode>
#
# Modes:
#   up        Build the houndarr:e2e image if missing, create arr-net,
#             start mock-sonarr + mock-radarr + houndarr-e2e on the
#             bridge.  Waits for Houndarr /api/health.  Leaves the
#             stack running for interactive debugging; no pytest
#             is invoked.  Does NOT auto-teardown on exit.
#   down      Tear the stack down (docker rm -f the three containers,
#             docker network rm arr-net, rm -rf /tmp/houndarr-e2e-data).
#             Idempotent; safe to run when the stack is already down.
#
# Defaults the maintainer usually does not override:
#   IMAGE                  houndarr:e2e
#   NETWORK                arr-net
#   DATA_DIR               /tmp/houndarr-e2e-data
#   HOUNDARR_HOST_PORT     8877
#
# Exit codes:
#   0  success
#   1  any failure (propagated from docker / curl)
#
# ``up`` leaves the stack running on purpose; pair with ``down`` when
# finished.  Used by ``just e2e-up`` and ``just e2e-down``.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

IMAGE="${IMAGE:-houndarr:e2e}"
NETWORK="${NETWORK:-arr-net}"
DATA_DIR="${DATA_DIR:-/tmp/houndarr-e2e-data}"
HOUNDARR_HOST_PORT="${HOUNDARR_HOST_PORT:-8877}"

# Colour codes (disabled when not a tty)
if [ -t 1 ]; then
    GREEN="\033[0;32m"
    RED="\033[0;31m"
    YELLOW="\033[0;33m"
    RESET="\033[0m"
else
    GREEN="" RED="" YELLOW="" RESET=""
fi

_info() { echo -e "${GREEN}[ e2e ]${RESET} $1"; }
_warn() { echo -e "${YELLOW}[ e2e ]${RESET} $1"; }
_fail() { echo -e "${RED}[ e2e ]${RESET} $1" >&2; }

_require_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        _fail "docker is required but not found on PATH"
        exit 1
    fi
    if ! docker info >/dev/null 2>&1; then
        _fail "docker is installed but the daemon is not reachable"
        exit 1
    fi
}

_build_image() {
    # Always `docker build`; Docker's layer cache makes a no-op fast
    # when nothing changed, while a real code change (CSS, template,
    # Python) rebuilds the affected layers.
    _info "building ${IMAGE} from ${REPO_ROOT} (uses docker cache)"
    docker build -t "${IMAGE}" "${REPO_ROOT}" >/dev/null
}

_ensure_network() {
    if docker network inspect "${NETWORK}" >/dev/null 2>&1; then
        return
    fi
    _info "creating docker network ${NETWORK}"
    docker network create "${NETWORK}" >/dev/null
}

_start_mocks() {
    for entry in "mock-sonarr:Sonarr:8989" "mock-radarr:Radarr:7878"; do
        local name app port
        name="${entry%%:*}"
        app="$(echo "${entry}" | cut -d: -f2)"
        port="${entry##*:}"
        if docker ps --format '{{.Names}}' | grep -qx "${name}"; then
            _info "${name} already running"
            continue
        fi
        _info "starting ${name} on port ${port}"
        docker run -d --name "${name}" --network "${NETWORK}" \
            -v "${REPO_ROOT}/tests/e2e_browser/mock_arr.py:/app/mock_arr.py:ro" \
            --entrypoint python \
            "${IMAGE}" \
            /app/mock_arr.py --app "${app}" --port "${port}" >/dev/null
    done
}

_wait_for_mocks() {
    for entry in "mock-sonarr:8989" "mock-radarr:7878"; do
        local name port
        name="${entry%:*}"
        port="${entry#*:}"
        local i
        for i in $(seq 1 60); do
            if docker exec "${name}" python -c \
                "from urllib.request import urlopen; urlopen('http://localhost:${port}/api/v3/system/status').read()" \
                >/dev/null 2>&1; then
                _info "${name} ready"
                break
            fi
            if [ "${i}" -eq 60 ]; then
                docker logs "${name}" >&2 || true
                _fail "${name} did not come up within 60s"
                exit 1
            fi
            sleep 1
        done
    done
}

_start_houndarr() {
    if docker ps --format '{{.Names}}' | grep -qx houndarr-e2e; then
        _info "houndarr-e2e already running"
        return
    fi
    mkdir -p "${DATA_DIR}"
    _info "starting houndarr-e2e on 0.0.0.0:${HOUNDARR_HOST_PORT}"
    docker run -d --name houndarr-e2e --network "${NETWORK}" \
        -p "${HOUNDARR_HOST_PORT}:8877" \
        -v "${DATA_DIR}:/data" \
        -e TZ=UTC \
        "${IMAGE}" >/dev/null
}

_wait_for_houndarr() {
    local i
    for i in $(seq 1 60); do
        if curl -sf "http://localhost:${HOUNDARR_HOST_PORT}/api/health" 2>/dev/null \
            | grep -q '"status":"ok"'; then
            _info "houndarr-e2e healthy"
            return
        fi
        sleep 2
    done
    docker logs houndarr-e2e >&2 || true
    _fail "houndarr-e2e did not report healthy within 120s"
    exit 1
}

_teardown() {
    _info "tearing down e2e stack"
    docker rm -f houndarr-e2e mock-sonarr mock-radarr >/dev/null 2>&1 || true
    docker network rm "${NETWORK}" >/dev/null 2>&1 || true
    # Only rm the default ``/tmp/houndarr-e2e-data`` location.  An
    # operator who overrode ``DATA_DIR`` (e.g. to iterate against a
    # specific dataset in their home dir) should not have that
    # directory wiped on teardown; leave it in place and warn instead.
    if [ "${DATA_DIR}" = "/tmp/houndarr-e2e-data" ]; then
        rm -rf "${DATA_DIR}"
    else
        _warn "DATA_DIR override detected (${DATA_DIR}); leaving contents intact"
    fi
}

_up() {
    _require_docker
    _build_image
    _ensure_network
    _start_mocks
    _wait_for_mocks
    _start_houndarr
    _wait_for_houndarr
    _info "stack up.  docker ps | grep -E 'houndarr-e2e|mock-'"
    _info "when finished, tear down with: just e2e-down"
}

_down() {
    _require_docker
    _teardown
    _info "teardown complete"
}

main() {
    if [ "$#" -lt 1 ]; then
        _fail "usage: $0 <up|down>"
        exit 1
    fi
    case "$1" in
        up)   _up ;;
        down) _down ;;
        *)
            _fail "unknown mode: $1 (expected up|down)"
            exit 1
            ;;
    esac
}

main "$@"
