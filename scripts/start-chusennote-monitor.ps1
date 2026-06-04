[CmdletBinding()]
param(
    [string]$Db = "chusennote.sqlite3",
    [int]$IntervalMinutes = 60,
    [ValidateSet("event", "artist")]
    [string]$Kind = "event",
    [switch]$AlertsJson,
    [int]$MaxRuns = 0,
    [int]$StopAfterErrors = 0
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Args = @(
    "lottery_monitor.py",
    "watch",
    "loop",
    "--db", $Db,
    "--interval-minutes", $IntervalMinutes,
    "--kind", $Kind
)

if ($AlertsJson) {
    $Args += "--alerts-json"
}
if ($MaxRuns -gt 0) {
    $Args += @("--max-runs", $MaxRuns)
}
if ($StopAfterErrors -gt 0) {
    $Args += @("--stop-after-errors", $StopAfterErrors)
}

Write-Host "Starting chusennote monitor loop..."
Write-Host "Kind: $Kind"
Write-Host "Interval: $IntervalMinutes minute(s)"
& python @Args
