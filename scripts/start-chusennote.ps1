[CmdletBinding()]
param(
    [string]$Db = "chusennote.sqlite3",
    [int]$Port = 8765,
    [string]$HostName = "127.0.0.1",
    [switch]$Lan,
    [switch]$Open
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

if ($Lan) {
    $HostName = "0.0.0.0"
}

Set-Location $RepoRoot

$DisplayHost = if ($HostName -eq "0.0.0.0") { "127.0.0.1" } else { $HostName }
$LocalUrl = "http://$DisplayHost`:$Port"

Write-Host "Starting chusennote API and web UI..."
Write-Host "Local URL: $LocalUrl"

if ($HostName -eq "0.0.0.0") {
    try {
        $LanAddresses = Get-NetIPAddress -AddressFamily IPv4 |
            Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
            Select-Object -ExpandProperty IPAddress
        foreach ($Address in $LanAddresses) {
            Write-Host "LAN URL:   http://$Address`:$Port"
        }
    } catch {
        Write-Host "LAN URL:   use your computer's IPv4 address on port $Port"
    }
}

if ($Open) {
    Start-Process $LocalUrl
}

& python lottery_monitor.py web --db $Db --port $Port --host $HostName
