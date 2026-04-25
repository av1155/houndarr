#!/usr/bin/env bash
# Orchestrate the Playwright browser-e2e stack locally and (optionally)
# capture / verify the login + setup visual baselines under
# tests/e2e_browser/_screenshots/.
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
#   capture   Bring the stack up (pre-admin state) and run pytest
#             inside the Playwright Linux container twice: first
#             -k test_setup_page_visual --update-snapshots (no admin
#             yet, /setup renders), then curl POST /setup to create
#             the admin, then -k test_login_page_visual
#             --update-snapshots.  Tears the stack down on exit.
#   verify    Like ``capture`` but without ``--update-snapshots``.
#             The two visual tests must pass unguarded against the
#             already-committed PNGs.  Tears the stack down on exit.
#
# Defaults the maintainer usually does not override:
#   IMAGE                  houndarr:e2e
#   NETWORK                arr-net
#   DATA_DIR               /tmp/houndarr-e2e-data
#   HOUNDARR_HOST_PORT     8877
#   PLAYWRIGHT_IMAGE       mcr.microsoft.com/playwright/python:v1.58.0-jammy
#                          (matches ``playwright==1.58.0`` pinned in
#                          tests/e2e_browser/requirements.txt)
#   HOUNDARR_ADMIN_USER    admin
#   HOUNDARR_ADMIN_PASS    CITestPass1!
#
# OS-parity note: the pytest invocation runs inside a Linux Playwright
# container so fonts antialias the same way as GitHub Actions'
# ubuntu-latest runner.  Capturing directly on the macOS host would
# produce pixel diffs against CI; this container detour keeps the
# baselines strictly byte-equal between local and CI.
#
# Exit codes:
#   0  success
#   1  any failure (propagated from docker / pytest / curl)
#
# The script traps EXIT in capture / verify modes so a mid-flow failure
# still tears the stack down.  ``up`` leaves the stack running on
# purpose.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

IMAGE="${IMAGE:-houndarr:e2e}"
NETWORK="${NETWORK:-arr-net}"
DATA_DIR="${DATA_DIR:-/tmp/houndarr-e2e-data}"
HOUNDARR_HOST_PORT="${HOUNDARR_HOST_PORT:-8877}"
PLAYWRIGHT_IMAGE="${PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright/python:v1.58.0-jammy}"
HOUNDARR_ADMIN_USER="${HOUNDARR_ADMIN_USER:-admin}"
HOUNDARR_ADMIN_PASS="${HOUNDARR_ADMIN_PASS:-CITestPass1!}"

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
    # Python) rebuilds the affected layers.  An "only build if missing"
    # guard would silently capture baselines against a stale image
    # whenever a maintainer iterates on CSS between captures.
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

_create_admin() {
    _info "creating admin account via POST /setup"
    curl -sf -X POST "http://localhost:${HOUNDARR_HOST_PORT}/setup" \
        -d "username=${HOUNDARR_ADMIN_USER}&password=${HOUNDARR_ADMIN_PASS}&password_confirm=${HOUNDARR_ADMIN_PASS}" \
        -o /dev/null
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

_run_pytest() {
    # Args:
    #   $1 selector    pytest -k expression
    #   $2 update_mode "update" (captures baselines) or "verify" (compares)
    local selector="$1"
    local update_mode="${2:-verify}"
    local update_env=""
    if [ "${update_mode}" = "update" ]; then
        update_env="-e HOUNDARR_E2E_CAPTURE=1"
    fi
    _info "pytest inside ${PLAYWRIGHT_IMAGE} (-k ${selector}, mode=${update_mode})"
    docker run --rm --network "${NETWORK}" \
        -v "${REPO_ROOT}:/workspace" \
        -w /workspace \
        -e HOUNDARR_URL="http://houndarr-e2e:8877" \
        -e MOCK_SONARR_URL="http://mock-sonarr:8989" \
        -e MOCK_RADARR_URL="http://mock-radarr:7878" \
        -e HOUNDARR_E2E_USER="${HOUNDARR_ADMIN_USER}" \
        -e HOUNDARR_E2E_PASS="${HOUNDARR_ADMIN_PASS}" \
        ${update_env} \
        "${PLAYWRIGHT_IMAGE}" \
        bash -lc "pip install --quiet --disable-pip-version-check -r tests/e2e_browser/requirements.txt \
            && pytest tests/e2e_browser/ --confcutdir tests/e2e_browser \
               -k '${selector}' --browser chromium -v"
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

_capture() {
    _require_docker
    trap _teardown EXIT
    _up
    # Setup baseline first: fresh /data means is_setup_complete is False
    # and /setup renders the first-run form.
    _run_pytest test_setup_page_visual update
    # test_setup_page_visual's ``finally`` block already calls
    # ``_recreate_admin`` to restore admin state.  The curl below is a
    # belt-and-braces re-confirmation: POST /setup is idempotent
    # server-side (returns 200/303 on a second call; see
    # src/houndarr/routes/pages.py:77), so running it again is a no-op.
    _create_admin
    # Login baseline: admin now exists; logged_in_page fixture logs in,
    # the test logs out and navigates to /login.
    _run_pytest test_login_page_visual update
    _info "baselines captured under tests/e2e_browser/_screenshots/"
}

_verify() {
    _require_docker
    trap _teardown EXIT
    _up
    # Same two-pytest flow in verify mode.  The captured baselines
    # must satisfy the byte-equal assertion; otherwise pytest fails.
    _run_pytest test_setup_page_visual verify
    # Idempotent re-confirmation; see _capture for rationale.
    _create_admin
    _run_pytest test_login_page_visual verify
    _info "baselines verified"
}

main() {
    if [ "$#" -lt 1 ]; then
        _fail "usage: $0 <up|down|capture|verify>"
        exit 1
    fi
    case "$1" in
        up)      _up ;;
        down)    _down ;;
        capture) _capture ;;
        verify)  _verify ;;
        *)
            _fail "unknown mode: $1 (expected up|down|capture|verify)"
            exit 1
            ;;
    esac
}

main "$@"
