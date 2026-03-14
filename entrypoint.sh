#!/bin/sh
# Houndarr container entrypoint
# Handles PUID/PGID user remapping so /data files are owned by the host user.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# PUID=0 means the user explicitly wants to run as root (common in LXC/Proxmox
# environments where containers are already isolated at the hypervisor level).
# Skip privilege-drop entirely and run directly as root.
if [ "$PUID" = "0" ]; then
    echo "WARNING: Running as root (PUID=0). Consider using a non-root user in production."
    exec "$@"
fi

# If running as root, remap the appuser UID/GID to match host PUID/PGID
if [ "$(id -u)" = "0" ]; then
    # Update group if needed
    if ! getent group "$PGID" > /dev/null 2>&1; then
        groupmod -g "$PGID" appgroup 2>/dev/null || groupadd -g "$PGID" appgroup
    fi

    # Update user if needed
    CURRENT_UID=$(id -u appuser 2>/dev/null || echo "")
    if [ "$CURRENT_UID" != "$PUID" ]; then
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
