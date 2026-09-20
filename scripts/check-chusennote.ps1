[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8877",
    [int]$TimeoutSec = 5,
    [string]$Python = "python",
    [switch]$RequirePostgres
)

$ErrorActionPreference = "Stop"
if ($TimeoutSec -le 0) {
    throw "TimeoutSec must be greater than zero."
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$SmokeExitCode = 1
Push-Location $RepoRoot
try {
    $SmokeArgs = @("lottery_monitor.py", "smoke", "--base-url", $BaseUrl, "--timeout", $TimeoutSec, "--json")
    if ($RequirePostgres) {
        $SmokeArgs += "--require-postgres"
    }
    & $Python @SmokeArgs
    $SmokeExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

exit $SmokeExitCode
