# Detect the host's LAN IP and start the stack with it, so the console can
# advertise a reachable MCP address to other devices. A bridge-networked
# container can't discover the host's LAN IP, so we resolve it here and pass it
# through as LAN_HOST. Any extra args are forwarded to docker compose.
#
# Usage:  .\scripts\up.ps1            # production console (nginx static build)
#         .\scripts\up.ps1 -d         # detached
#         .\scripts\up.ps1 dev        # development console (Vite hot-reload)
#         .\scripts\up.ps1 dev -d     # dev, detached
$ErrorActionPreference = 'Stop'

# `dev` as the first arg swaps the production console for the Vite dev server.
$composeArgs = @('-f', 'docker-compose.yml')
$rest = $args
if ($args.Count -gt 0 -and $args[0] -eq 'dev') {
    $composeArgs += @('-f', 'docker-compose.dev.yml')
    if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] } else { $rest = @() }
}

# The real LAN adapter is the one that's Up and has a default gateway; virtual
# switches (Hyper-V, WSL, Docker) have no gateway, so this skips them.
$ip = (Get-NetIPConfiguration |
    Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } |
    Select-Object -First 1).IPv4Address.IPAddress

if ($ip) {
    $env:LAN_HOST = $ip
    Write-Host "LAN_HOST=$ip"
} else {
    Write-Warning "Could not detect a LAN IP; the console will fall back to socket detection."
}

docker compose @composeArgs up @rest
