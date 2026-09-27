#!/bin/sh
# FrameForge entrypoint: run as PUID:PGID, give access to GPU device nodes, start the right role.
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
FF_ROLE="${FF_ROLE:-all}"
APP_USER=frameforge
RUN_AS=""

if [ "$(id -u)" = "0" ]; then
    # Create/adjust the runtime user to match the host's media owner.
    if ! getent group "$PGID" >/dev/null 2>&1; then
        groupadd -o -g "$PGID" "$APP_USER"
    fi
    GROUP_NAME="$(getent group "$PGID" | cut -d: -f1)"
    if ! id "$APP_USER" >/dev/null 2>&1; then
        useradd -o -u "$PUID" -g "$PGID" -d /config -s /usr/sbin/nologin -M "$APP_USER"
    else
        usermod -o -u "$PUID" -g "$PGID" "$APP_USER" >/dev/null 2>&1 || true
    fi

    # Join whatever groups own the GPU device nodes (render/video), whatever their GIDs are on this host.
    for dev in /dev/dri/renderD* /dev/dri/card* /dev/nvidia*; do
        [ -e "$dev" ] || continue
        gid="$(stat -c '%g' "$dev")"
        [ "$gid" = "0" ] && continue
        gname="$(getent group "$gid" | cut -d: -f1 || true)"
        if [ -z "$gname" ]; then
            gname="hostgpu$gid"
            groupadd -o -g "$gid" "$gname" 2>/dev/null || true
        fi
        usermod -aG "$gname" "$APP_USER" 2>/dev/null || true
    done

    mkdir -p /config
    # Only fix ownership of our own state, never of media.
    chown -R "$PUID:$PGID" /config 2>/dev/null || echo "WARN: could not chown /config (read-only or network filesystem?)"
    echo "FrameForge: role=$FF_ROLE uid=$PUID gid=$PGID group=$GROUP_NAME"
    RUN_AS="gosu $APP_USER"
fi

# Explicit command (e.g. `docker compose run frameforge pytest`) runs as the app user.
if [ "$#" -gt 0 ]; then
    exec $RUN_AS "$@"
fi

case "$FF_ROLE" in
    node)
        exec $RUN_AS python -m frameforge_node
        ;;
    server|all)
        exec $RUN_AS python -m frameforge_server
        ;;
    *)
        echo "Unknown FF_ROLE '$FF_ROLE' (expected all, server or node)" >&2
        exit 2
        ;;
esac
