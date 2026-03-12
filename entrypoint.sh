#!/bin/sh
# Houndarr container entrypoint
# Handles PUID/PGID user remapping so /data files are owned by the host user.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# If running as root, remap the appuser UID/GID to match host PUID/PGID
if [ "$(id -u)" = "0" ]; then
    # Update group if needed
    if ! getent group "$PGID" > /dev/null 2>&1; then
        groupmod -g "$PGID" appgroup 2>/dev/null || groupadd -g "$PGID" appgroup
    fi

    # Update user if needed
    CURRENT_UID=$(id -u appuser 2>/dev/null || echo "")
    if [ "$CURRENT_UID" != "$PGID" ]; then
        usermod -u "$PUID" -g "$PGID" appuser 2>/dev/null || true
    fi

    # Ensure /data is owned by the mapped user
    mkdir -p /data
    chown -R "${PUID}:${PGID}" /data

    # Drop privileges and exec the application
    exec gosu appuser "$@"
else
    # Already non-root (e.g., in dev), just exec directly
    exec "$@"
fi
