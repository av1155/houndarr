#!/usr/bin/env bash
# URL-base smoke test for a Houndarr container started with HOUNDARR_URL_BASE.
#
# Talks to the container port directly: prefixed and unprefixed requests must
# both route, and every redirect must carry the base, exactly, so a proxy that
# serves nothing outside the prefix keeps working.  Creates the admin account.
#
# Usage:
#   bash scripts/url_base_smoke_test.sh HOST BASE CONTAINER PASSWORD
#
#   HOST       container address without the base, e.g. http://localhost:8878
#   BASE       the container's HOUNDARR_URL_BASE, e.g. /houndarr
#   CONTAINER  container name; the image HEALTHCHECK command runs inside it
#   PASSWORD   password for the admin account this script sets up
#
# Exit code: 0 if every check passes, 1 if any fails.

set -euo pipefail

HOST="$1"
BASE="$2"
CONTAINER="$3"
PASSWORD="$4"

FAIL=0
JAR=$(mktemp)
BODY=$(mktemp)
trap 'rm -f "$JAR" "$BODY"' EXIT

_pass() { echo "[PASS] $1"; }
_fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

# Usage: _expect METHOD PATH STATUS [LOCATION] [extra curl args...]
# Never follows redirects; LOCATION "-" skips the Location check.
_expect() {
    local method="$1" path="$2" want_status="$3" want_location="${4:--}"
    shift 4 || shift $#
    local out status location
    out=$(curl -s -o "$BODY" -X "$method" -b "$JAR" -c "$JAR" \
        -w '%{http_code} %header{location}' "$@" "$HOST$path" || true)
    status="${out%% *}"
    location="${out#* }"
    if [[ "$status" != "$want_status" ]]; then
        _fail "$method $path -> $status (expected $want_status)"
    elif [[ "$want_location" != "-" && "$location" != "$want_location" ]]; then
        _fail "$method $path -> Location '$location' (expected '$want_location')"
    else
        _pass "$method $path -> $status${location:+ $location}"
    fi
}

_csrf() { awk '$6 == "houndarr_csrf" { print $7 }' "$JAR"; }

for i in $(seq 1 30); do
    if curl -sf "$HOST/api/health" | grep -q '"status":"ok"'; then
        break
    fi
    if [[ "$i" == "30" ]]; then
        echo "Houndarr did not answer /api/health within 60s"
        exit 1
    fi
    sleep 2
done

HEALTHCHECK=$(docker inspect --format '{{index .Config.Healthcheck.Test 1}}' "$CONTAINER")
if docker exec "$CONTAINER" sh -c "$HEALTHCHECK" >/dev/null; then
    _pass "image HEALTHCHECK passes: $HEALTHCHECK"
else
    _fail "image HEALTHCHECK fails: $HEALTHCHECK"
fi

_expect GET "/api/health" 200
_expect GET "$BASE/api/health" 200
_expect GET "$BASE" 302 "$BASE/setup"
_expect GET "$BASE/" 302 "$BASE/setup"
_expect GET "/" 302 "$BASE/setup"
_expect GET "$BASE/login" 302 "$BASE/setup"

_expect POST "$BASE/setup" 303 "$BASE/login" \
    --data-urlencode "username=admin" \
    --data-urlencode "password=$PASSWORD" \
    --data-urlencode "password_confirm=$PASSWORD"
_expect GET "$BASE/settings" 302 "$BASE/login"
_expect GET "/settings" 302 "$BASE/login"
_expect GET "$BASE/api/health/x" 302 "$BASE/login"
_expect POST "$BASE/login" 303 "$BASE/" \
    --data-urlencode "username=admin" --data-urlencode "password=$PASSWORD"

_expect GET "$BASE/settings/" 307 "$BASE/settings"
_expect GET "$BASE/" 200
if grep -q "basePath: \"$BASE\"" "$BODY" && grep -q "href=\"$BASE/static/css/app.built.css" "$BODY"; then
    _pass "dashboard carries the base in basePath and asset URLs"
else
    _fail "dashboard is missing the base in basePath or asset URLs"
fi
_expect GET "$BASE/static/css/app.built.css" 200
_expect GET "/logs" 200
_expect POST "$BASE/logout" 302 "$BASE/login" -H "X-CSRF-Token: $(_csrf)"

_expect GET "$BASE/api/v1/widget" 401
_expect GET "/api/v1/widget" 401

if [[ "$FAIL" -gt 0 ]]; then
    echo "$FAIL URL-base check(s) failed"
    exit 1
fi
echo "All URL-base checks passed"
