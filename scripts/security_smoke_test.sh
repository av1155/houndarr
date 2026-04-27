#!/usr/bin/env bash
# Security smoke test for a running Houndarr instance.
#
# Usage:
#   bash scripts/security_smoke_test.sh [HOST] [PASSWORD]
#
# Defaults:
#   HOST                 http://localhost:8877
#   PASSWORD             prompted interactively if not supplied
#   USERNAME             admin (fixed; change below if different)
#   HOUNDARR_CONTAINER   container name for docker exec checks; defaults to
#                        houndarr-dev; set to "" to skip container checks
#
# Exit code: 0 if no FAILs, 1 if any FAIL.

set -euo pipefail

HOST="${1:-http://localhost:8877}"
PASSWORD="${2:-}"
USERNAME="admin"
CONTAINER="${HOUNDARR_CONTAINER:-houndarr-dev}"

PASS=0
FAIL=0
WARN=0

# Colour codes (disabled when not a tty)
if [ -t 1 ]; then
    GREEN="\033[0;32m"
    RED="\033[0;31m"
    YELLOW="\033[0;33m"
    RESET="\033[0m"
else
    GREEN="" RED="" YELLOW="" RESET=""
fi

_pass() { echo -e "${GREEN}[PASS]${RESET} $1"; PASS=$((PASS + 1)); }
_fail() { echo -e "${RED}[FAIL]${RESET} $1"; FAIL=$((FAIL + 1)); }
_warn() { echo -e "${YELLOW}[WARN]${RESET} $1"; WARN=$((WARN + 1)); }
_section() { echo; echo "== $1 =="; }

# Temporary files for curl output
BODY=$(mktemp)
trap 'rm -f "$BODY"' EXIT

# Perform a request; sets STATUS and writes body to $BODY.
# Usage: _curl METHOD URL [extra curl args...]
_curl() {
    local method="$1"; shift
    local url="$1"; shift
    STATUS=$(curl -s -X "$method" -o "$BODY" -w "%{http_code}" \
        -L \
        --max-redirs 0 \
        --cookie-jar /tmp/houndarr_smoke_cookies \
        --cookie /tmp/houndarr_smoke_cookies \
        "$@" "$url" || true)
}

# -----------------------------------------------------------------------
# Prompt for password if not provided
# -----------------------------------------------------------------------

if [ -z "$PASSWORD" ]; then
    read -rsp "Password for $USERNAME@$HOST: " PASSWORD
    echo
fi

# -----------------------------------------------------------------------
# Acquire a session cookie
# -----------------------------------------------------------------------

rm -f /tmp/houndarr_smoke_cookies

_section "Session setup"

# First hit setup to get a CSRF cookie (may redirect to login if already set up)
curl -s -c /tmp/houndarr_smoke_cookies -b /tmp/houndarr_smoke_cookies \
    -o /dev/null "$HOST/login" >/dev/null 2>&1 || true

CSRF_TOKEN=$(grep "houndarr_csrf" /tmp/houndarr_smoke_cookies 2>/dev/null | awk '{print $NF}' || true)

LOGIN_STATUS=$(curl -s -o /tmp/houndarr_smoke_login_body \
    -w "%{http_code}" \
    -L --max-redirs 5 \
    -c /tmp/houndarr_smoke_cookies \
    -b /tmp/houndarr_smoke_cookies \
    -H "X-CSRF-Token: ${CSRF_TOKEN}" \
    -d "username=${USERNAME}&password=${PASSWORD}" \
    "$HOST/login" || true)

if [[ "$LOGIN_STATUS" == "200" ]]; then
    _pass "Login succeeded (200 after redirect)"
    CSRF_TOKEN=$(grep "houndarr_csrf" /tmp/houndarr_smoke_cookies 2>/dev/null | awk '{print $NF}' || true)
    AUTHENTICATED=1
else
    _fail "Login failed (HTTP $LOGIN_STATUS); authenticated checks will be skipped"
    AUTHENTICATED=0
fi

# -----------------------------------------------------------------------
# 1. Unauthenticated endpoint sweep (fresh cookie jar)
# -----------------------------------------------------------------------

_section "1. Unauthenticated endpoint sweep"

PROTECTED_ROUTES=(
    "GET /"
    "GET /logs"
    "GET /settings"
    "GET /settings/help"
    "GET /settings/instances/add-form"
    "GET /settings/instances/1/edit"
    "POST /settings/account/password"
    "POST /settings/instances"
    "POST /settings/instances/1"
    "POST /settings/instances/1/toggle-enabled"
    "POST /api/instances/1/run-now"
    "DELETE /settings/instances/1"
    "GET /api/status"
    "GET /api/logs"
    "GET /api/logs/partial"
    "POST /settings/admin/reset-instances"
    "POST /settings/admin/clear-logs"
    "POST /settings/admin/factory-reset"
)

for route in "${PROTECTED_ROUTES[@]}"; do
    METHOD=$(echo "$route" | cut -d' ' -f1)
    PATH_PART=$(echo "$route" | cut -d' ' -f2)
    # Use a fresh cookie jar (no session)
    SC=$(curl -s -X "$METHOD" -o /dev/null -w "%{http_code}" \
        --max-redirs 0 "$HOST$PATH_PART" || true)
    if [[ "$SC" == "302" || "$SC" == "307" ]]; then
        _pass "$METHOD $PATH_PART -> $SC (redirect to auth)"
    else
        _fail "$METHOD $PATH_PART -> $SC (expected 302/307)"
    fi
done

# -----------------------------------------------------------------------
# 2. Public endpoint content checks
# -----------------------------------------------------------------------

_section "2. Public endpoint content"

# /api/health must return exactly {"status":"ok"}
SC=$(curl -s -o "$BODY" -w "%{http_code}" "$HOST/api/health" || true)
HEALTH_BODY=$(cat "$BODY")
if [[ "$SC" == "200" && "$HEALTH_BODY" == '{"status":"ok"}' ]]; then
    _pass "/api/health returns exactly {\"status\":\"ok\"}"
else
    _fail "/api/health returned HTTP $SC body: $HEALTH_BODY"
fi

# Public HTML pages must not contain sensitive patterns
for path in /login /setup; do
    SC=$(curl -s -o "$BODY" -w "%{http_code}" "$HOST$path" || true)
    BODY_CONTENT=$(cat "$BODY" | tr '[:upper:]' '[:lower:]')
    FOUND=""
    for pattern in api_key gaaaaaa masterkey fernet; do
        if echo "$BODY_CONTENT" | grep -q "$pattern"; then
            FOUND="$FOUND $pattern"
        fi
    done
    if [ -z "$FOUND" ]; then
        _pass "GET $path: no sensitive patterns in response"
    else
        _fail "GET $path: sensitive patterns found:$FOUND"
    fi
done

# -----------------------------------------------------------------------
# 3. Path traversal
# -----------------------------------------------------------------------

_section "3. Path traversal"

TRAVERSAL_PATHS=(
    "/../../../etc/passwd"
    "/static/../../etc/passwd"
    "/api/logs/../../../etc/passwd"
)

for tpath in "${TRAVERSAL_PATHS[@]}"; do
    SC=$(curl -s -o /dev/null -w "%{http_code}" --max-redirs 0 \
        "$HOST$tpath" || true)
    if [[ "$SC" != "200" ]]; then
        _pass "GET $tpath -> $SC (not 200)"
    else
        _fail "GET $tpath -> 200; path traversal may have succeeded"
    fi
done

# -----------------------------------------------------------------------
# 4. X-Forwarded-For spoofing
# -----------------------------------------------------------------------

_section "4. X-Forwarded-For spoofing"

SC=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-redirs 0 \
    -H "X-Forwarded-For: 127.0.0.1" \
    "$HOST/" || true)
if [[ "$SC" == "302" || "$SC" == "307" ]]; then
    _pass "GET / with X-Forwarded-For: 127.0.0.1 -> $SC (auth still required)"
else
    _fail "GET / with X-Forwarded-For: 127.0.0.1 -> $SC (expected redirect)"
fi

# -----------------------------------------------------------------------
# 5. Rate limiting runs last among the HTTP-layer sections so it does not
# poison the per-IP login bucket that the factory-reset checks in 6b rely
# on (admin.py gates factory-reset through the same check_login_rate_limit
# counter, so tripping the bucket here before running 6b makes wrong-
# password -> 422 flake into wrong-password -> 429).
# -----------------------------------------------------------------------

# -----------------------------------------------------------------------
# 6. Authenticated checks (requires successful login above)
# -----------------------------------------------------------------------

_section "6. Authenticated API key exposure checks"

if [[ "$AUTHENTICATED" == "1" ]]; then
    # /api/status must not include api_key
    SC=$(curl -s -o "$BODY" -w "%{http_code}" \
        -c /tmp/houndarr_smoke_cookies \
        -b /tmp/houndarr_smoke_cookies \
        "$HOST/api/status" || true)
    if echo "$(cat "$BODY")" | grep -q '"api_key"'; then
        _fail "/api/status response contains api_key field"
    else
        _pass "/api/status: no api_key field in response (HTTP $SC)"
    fi

    # /settings HTML must not contain gAAAAA (Fernet prefix)
    SC=$(curl -s -o "$BODY" -w "%{http_code}" \
        -c /tmp/houndarr_smoke_cookies \
        -b /tmp/houndarr_smoke_cookies \
        "$HOST/settings" || true)
    if grep -q "gAAAAA" "$BODY"; then
        _fail "/settings HTML contains Fernet-encrypted key value (gAAAAA prefix)"
    else
        _pass "/settings: no Fernet token in HTML (HTTP $SC)"
    fi
else
    _warn "Authenticated checks skipped (login failed)"
fi

# -----------------------------------------------------------------------
# 6b. Admin endpoints: CSRF + behavior gates
# -----------------------------------------------------------------------

_section "6b. Admin endpoints CSRF"

if [[ "$AUTHENTICATED" == "1" ]]; then
    for admin_path in /settings/admin/reset-instances /settings/admin/clear-logs /settings/admin/factory-reset; do
        # Authenticated POST without X-CSRF-Token must return 403.
        SC=$(curl -s -o /dev/null -w "%{http_code}" \
            -X POST \
            --max-redirs 0 \
            -c /tmp/houndarr_smoke_cookies \
            -b /tmp/houndarr_smoke_cookies \
            "$HOST$admin_path" || true)
        if [[ "$SC" == "403" ]]; then
            _pass "POST $admin_path without CSRF -> 403"
        else
            _fail "POST $admin_path without CSRF -> $SC (expected 403)"
        fi
    done

    # factory-reset with valid session + CSRF but wrong password must reject.
    # Section 5 just maxed the IP-scoped rate-limit bucket with 7 failed
    # logins; factory-reset shares that same bucket (security fix #496),
    # so the first attempt here returns 429 instead of the 422 a fresh-IP
    # call would get.  Both responses prove the endpoint refused the
    # destructive action; either is acceptable as a security-gate signal.
    SC=$(curl -s -o "$BODY" -w "%{http_code}" \
        -X POST \
        --max-redirs 0 \
        -c /tmp/houndarr_smoke_cookies \
        -b /tmp/houndarr_smoke_cookies \
        -H "X-CSRF-Token: ${CSRF_TOKEN}" \
        -d "confirm_phrase=RESET&current_password=definitely-not-the-password" \
        "$HOST/settings/admin/factory-reset" || true)
    if [[ "$SC" == "422" || "$SC" == "429" ]]; then
        _pass "POST /settings/admin/factory-reset with wrong password -> $SC (rejected)"
    else
        _fail "POST /settings/admin/factory-reset with wrong password -> $SC (expected 422 or 429)"
    fi

    # factory-reset with wrong phrase + right password must still reject.
    SC=$(curl -s -o "$BODY" -w "%{http_code}" \
        -X POST \
        --max-redirs 0 \
        -c /tmp/houndarr_smoke_cookies \
        -b /tmp/houndarr_smoke_cookies \
        -H "X-CSRF-Token: ${CSRF_TOKEN}" \
        -d "confirm_phrase=nope&current_password=${PASSWORD}" \
        "$HOST/settings/admin/factory-reset" || true)
    if [[ "$SC" == "422" || "$SC" == "429" ]]; then
        _pass "POST /settings/admin/factory-reset with wrong phrase -> $SC (rejected)"
    else
        _fail "POST /settings/admin/factory-reset with wrong phrase -> $SC (expected 422 or 429)"
    fi

    # Rate limit regression guard: six wrong-password factory-reset
    # attempts must trip the shared /login IP bucket so a session-
    # compromised attacker cannot brute-force the admin password
    # through the destructive endpoint. The first five return 422;
    # the sixth must return 429.
    #
    # Uses a dedicated cookie jar so login-attempt noise from earlier
    # sections (Section 5's rate-limit probe) doesn't mix with the
    # counter this section is trying to assert on.
    rm -f /tmp/houndarr_smoke_rl2_cookies
    curl -s -c /tmp/houndarr_smoke_rl2_cookies \
        -b /tmp/houndarr_smoke_rl2_cookies \
        -o /dev/null \
        -d "username=${USERNAME}&password=${PASSWORD}" \
        "$HOST/login" >/dev/null 2>&1 || true
    RL2_CSRF=$(grep "houndarr_csrf" /tmp/houndarr_smoke_rl2_cookies 2>/dev/null | awk '{print $NF}' || true)

    LAST_FR_SC="000"
    for i in $(seq 1 6); do
        LAST_FR_SC=$(curl -s -o /dev/null -w "%{http_code}" \
            -X POST \
            --max-redirs 0 \
            -c /tmp/houndarr_smoke_rl2_cookies \
            -b /tmp/houndarr_smoke_rl2_cookies \
            -H "X-CSRF-Token: ${RL2_CSRF}" \
            -d "confirm_phrase=RESET&current_password=definitely-not-the-password" \
            "$HOST/settings/admin/factory-reset" || true)
    done

    if [[ "$LAST_FR_SC" == "429" ]]; then
        _pass "6 rapid wrong-password factory-reset attempts -> 429"
    else
        _fail "6 rapid wrong-password factory-reset attempts -> $LAST_FR_SC (expected 429)"
    fi
else
    _warn "Admin endpoint CSRF checks skipped (login failed)"
fi

# -----------------------------------------------------------------------
# 5. Rate limiting (runs last among HTTP tests: trips the per-IP login
# bucket, so it must not precede the factory-reset checks in 6b).
# -----------------------------------------------------------------------

_section "5. Rate limiting"

# Acquire a CSRF token for login attempts (fresh jar)
rm -f /tmp/houndarr_smoke_rl_cookies
curl -s -c /tmp/houndarr_smoke_rl_cookies -b /tmp/houndarr_smoke_rl_cookies \
    -o /dev/null "$HOST/login" >/dev/null 2>&1 || true
RL_CSRF=$(grep "houndarr_csrf" /tmp/houndarr_smoke_rl_cookies 2>/dev/null | awk '{print $NF}' || true)

LAST_SC="000"
for i in $(seq 1 7); do
    LAST_SC=$(curl -s -X POST -o /dev/null -w "%{http_code}" \
        --max-redirs 0 \
        -c /tmp/houndarr_smoke_rl_cookies \
        -b /tmp/houndarr_smoke_rl_cookies \
        -H "X-CSRF-Token: ${RL_CSRF}" \
        -d "username=${USERNAME}&password=wrong_password_smoke_test" \
        "$HOST/login" || true)
done

if [[ "$LAST_SC" == "429" ]]; then
    _pass "7 rapid failed logins -> 429 (rate limit triggered)"
else
    _fail "7 rapid failed logins -> $LAST_SC (expected 429)"
fi

# -----------------------------------------------------------------------
# 7. Container-level security (requires docker)
# -----------------------------------------------------------------------

_section "7. Container security"

if [ -z "$CONTAINER" ]; then
    _warn "CONTAINER is empty; skipping docker exec checks"
elif ! command -v docker &>/dev/null; then
    _warn "docker not found; skipping container checks"
elif ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
    _warn "Container '$CONTAINER' not running; skipping container checks"
else
    # masterkey file permissions must be 600
    KEY_PERMS=$(docker exec "$CONTAINER" stat -c '%a' /data/houndarr.masterkey 2>/dev/null || true)
    if [[ "$KEY_PERMS" == "600" ]]; then
        _pass "masterkey file permissions: $KEY_PERMS"
    else
        _fail "masterkey permissions: $KEY_PERMS (expected 600)"
    fi

    # All encrypted_api_key values must start with gAAAAA (Fernet prefix)
    BAD_KEYS=$(docker exec "$CONTAINER" python3 -c "
import sqlite3, sys
try:
    conn = sqlite3.connect('/data/houndarr.db')
    rows = conn.execute(
        \"SELECT name FROM instances WHERE encrypted_api_key NOT LIKE 'gAAAAA%'\"
    ).fetchall()
    conn.close()
    print(','.join(r[0] for r in rows))
except Exception as e:
    print('ERROR:' + str(e), file=sys.stderr)
" 2>/dev/null || true)
    if [ -z "$BAD_KEYS" ]; then
        _pass "All encrypted_api_key values have Fernet gAAAAA prefix"
    else
        _fail "Instances with non-Fernet api key storage: $BAD_KEYS"
    fi

    # Application process UID: check PID 1 inside the container, not the exec'd shell
    # (docker exec starts a new root process; /proc/1/status shows the real app UID)
    CONTAINER_UID=$(docker exec "$CONTAINER" \
        awk '/^Uid:/{print $2}' /proc/1/status 2>/dev/null || true)
    if [[ "$CONTAINER_UID" == "0" ]]; then
        _warn "Application process (PID 1) is running as root (uid=0); consider setting PUID to a non-root value"
    else
        _pass "Application process is not running as root (uid=$CONTAINER_UID)"
    fi

    # No added capabilities
    CAPS=$(docker inspect "$CONTAINER" \
        --format '{{.HostConfig.CapAdd}}' 2>/dev/null || true)
    if [[ "$CAPS" == "[]" || -z "$CAPS" ]]; then
        _pass "No added capabilities"
    else
        _fail "Container has added capabilities: $CAPS"
    fi

    # Not privileged
    PRIVILEGED=$(docker inspect "$CONTAINER" \
        --format '{{.HostConfig.Privileged}}' 2>/dev/null || true)
    if [[ "$PRIVILEGED" == "false" ]]; then
        _pass "Container is not privileged"
    else
        _fail "Container is running in privileged mode"
    fi
fi

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------

echo
echo "================================================="
echo "Security smoke test: ${PASS} passed, ${FAIL} failed, ${WARN} warnings"
echo "================================================="

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
