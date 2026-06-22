#!/usr/bin/env sh
# Detect the host's LAN IP and start the stack with it, so the console can
# advertise a reachable MCP address to other devices. A bridge-networked
# container can't discover the host's LAN IP, so we resolve it here and pass it
# through as LAN_HOST. Any extra args are forwarded to docker compose.
#
# Usage:  ./scripts/up.sh            # production console (nginx static build)
#         ./scripts/up.sh -d         # detached
#         ./scripts/up.sh dev        # development console (Vite hot-reload)
#         ./scripts/up.sh dev -d     # dev, detached
set -e

# `dev` as the first arg swaps the production console for the Vite dev server.
compose="-f docker-compose.yml"
if [ "$1" = "dev" ]; then
    compose="$compose -f docker-compose.dev.yml"
    shift
fi

# Linux: ask the routing table which source IP reaches a public address.
# macOS: `ip` is absent, so fall back to the primary en* interface.
ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="src") print $(i+1); exit}')
[ -z "$ip" ] && ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)

if [ -n "$ip" ]; then
    export LAN_HOST="$ip"
    echo "LAN_HOST=$ip"
else
    echo "Could not detect a LAN IP; the console will fall back to socket detection." >&2
fi

exec docker compose $compose up "$@"
