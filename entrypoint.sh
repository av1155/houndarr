#!/bin/sh
# Houndarr container entrypoint
# Supports two startup modes:
#   1. Compat mode (default): starts as root, remaps UID/GID via PUID/PGID, drops to appuser
#   2. Explicit non-root mode: starts as non-root via user:/runAsUser, skips remapping
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

CURRENT_UID=$(id -u)

# ---------------------------------------------------------------------------
# Non-root startup (explicit non-root mode)
# Reached when the container runtime sets user: or runAsUser.
# PUID/PGID remapping is not possible without root, so skip it entirely.
# ---------------------------------------------------------------------------
if [ "$CURRENT_UID" != "0" ]; then
    echo "INFO: Container started as non-root (UID=${CURRENT_UID}). PUID/PGID remapping is not available in this mode."

    # Warn if PUID or PGID were explicitly changed from defaults
    if [ "${PUID}" != "1000" ] || [ "${PGID}" != "1000" ]; then
        echo "WARNING: PUID/PGID are set but will be ignored; the container is not running as root."
    fi

    # Preflight: verify /data exists
    if [ ! -d /data ]; then
        echo "ERROR: /data does not exist. Create and chown it to UID ${CURRENT_UID} before starting." >&2
        exit 1
    fi

    # Preflight: verify /data is writable (needed for SQLite DB + WAL + master key)
    if ! touch /data/.houndarr-preflight-check 2>/dev/null; then
        echo "ERROR: /data is not writable by UID ${CURRENT_UID}." >&2
        echo "  For bind mounts: chown -R ${CURRENT_UID}:$(id -g) /path/to/data on the host" >&2
        echo "  For existing installs: chown houndarr.db, houndarr.db-wal, houndarr.db-shm, and houndarr.masterkey" >&2
        echo "  For Proxmox/LXC/root-based hosts: use the default startup (remove 'user:' from compose)" >&2
        exit 1
    fi
    rm -f /data/.houndarr-preflight-check

    # Preflight: verify existing files are accessible
    if [ -f /data/houndarr.masterkey ] && [ ! -r /data/houndarr.masterkey ]; then
        echo "ERROR: /data/houndarr.masterkey exists but is not readable by UID ${CURRENT_UID}." >&2
        echo "  Fix ownership: chown ${CURRENT_UID}:$(id -g) /data/houndarr.masterkey" >&2
        exit 1
    fi
    if [ -f /data/houndarr.db ] && [ ! -w /data/houndarr.db ]; then
        echo "ERROR: /data/houndarr.db exists but is not writable by UID ${CURRENT_UID}." >&2
        echo "  Fix ownership: chown -R ${CURRENT_UID}:$(id -g) /data/" >&2
        exit 1
    fi

    exec "$@"
fi

# ---------------------------------------------------------------------------
# Root startup with PUID=0 (intentional root execution)
# Common in LXC/Proxmox where containers are already isolated at the
# hypervisor level. Skip privilege-drop entirely.
# ---------------------------------------------------------------------------
if [ "$PUID" = "0" ]; then
    echo "WARNING: Running as root (PUID=0). Consider using a non-root user in production."
    exec "$@"
fi

# ---------------------------------------------------------------------------
# Root startup with PUID/PGID remapping (compat mode, default)
# Remap the appuser UID/GID to match host PUID/PGID, chown /data,
# then drop privileges via gosu.
# ---------------------------------------------------------------------------

# Update group if needed
if ! getent group "$PGID" > /dev/null 2>&1; then
    groupmod -g "$PGID" appgroup 2>/dev/null || groupadd -g "$PGID" appgroup
fi

# Update user if needed
APPUSER_UID=$(id -u appuser 2>/dev/null || echo "")
if [ "$APPUSER_UID" != "$PUID" ]; then
    usermod -u "$PUID" -g "$PGID" appuser 2>/dev/null || true
fi

# Ensure /data is owned by the mapped user
mkdir -p /data
chown -R "${PUID}:${PGID}" /data

# Drop privileges and exec the application
exec gosu appuser "$@"
