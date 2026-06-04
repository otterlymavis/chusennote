[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "chusennote-monitor",
    [string]$Db = "chusennote.sqlite3",
    [int]$IntervalMinutes = 60,
    [ValidateSet("event", "artist")]
    [string]$Kind = "event",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

if ($IntervalMinutes -lt 1) {
    throw "IntervalMinutes must be at least 1."
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$DbPath = $Db
if (-not [System.IO.Path]::IsPathRooted($DbPath)) {
    $DbPath = Join-Path $RepoRoot $DbPath
}

function Quote-TaskArgument {
    param([string]$Value)

    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

$Arguments = @(
    "lottery_monitor.py",
    $Kind,
    "run",
    "--db",
    $DbPath
) | ForEach-Object { Quote-TaskArgument $_ }

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument ($Arguments -join " ") `
    -WorkingDirectory $RepoRoot

$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

$Description = "Runs chusennote $Kind checks every $IntervalMinutes minute(s) from $RepoRoot."

if ($PSCmdlet.ShouldProcess($TaskName, "register recurring chusennote monitor task")) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description $Description `
        -Force | Out-Null

    Write-Host "Installed scheduled task '$TaskName'."
    Write-Host "Kind: $Kind"
    Write-Host "Interval: $IntervalMinutes minute(s)"
    Write-Host "Database: $DbPath"
}
