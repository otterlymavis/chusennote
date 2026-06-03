[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8765",
    [int]$TimeoutSec = 5
)

$ErrorActionPreference = "Stop"
$HealthUrl = "$($BaseUrl.TrimEnd('/'))/api/health"

try {
    $Health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec $TimeoutSec
    $Health | ConvertTo-Json -Depth 6
    exit 0
} catch {
    Write-Error "chusennote health check failed at $HealthUrl. $($_.Exception.Message)"
    exit 1
}
