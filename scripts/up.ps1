# Detect the host's LAN IP and start the stack with it, so the console can
# advertise a reachable MCP address to other devices. A bridge-networked
# container can't discover the host's LAN IP, so we resolve it here and pass it
# through as LAN_HOST. Any extra args are forwarded to docker compose.
#
# Usage:  .\scripts\up.ps1            # foreground
#         .\scripts\up.ps1 -d         # detached
$ErrorActionPreference = 'Stop'

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

docker compose up @args
