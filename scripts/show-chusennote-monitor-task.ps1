[CmdletBinding()]
param(
    [string]$TaskName = "chusennote-monitor"
)

$ErrorActionPreference = "Stop"

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $Task) {
    Write-Host "Scheduled task '$TaskName' is not installed."
    exit 1
}

$Info = Get-ScheduledTaskInfo -TaskName $TaskName

[PSCustomObject]@{
    TaskName = $Task.TaskName
    State = $Task.State
    LastRunTime = $Info.LastRunTime
    LastTaskResult = $Info.LastTaskResult
    NextRunTime = $Info.NextRunTime
    Action = ($Task.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join "; "
    Description = $Task.Description
} | Format-List
