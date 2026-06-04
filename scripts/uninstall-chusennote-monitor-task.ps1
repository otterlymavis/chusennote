[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "chusennote-monitor"
)

$ErrorActionPreference = "Stop"

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $Task) {
    Write-Host "Scheduled task '$TaskName' is not installed."
    return
}

if ($PSCmdlet.ShouldProcess($TaskName, "unregister chusennote monitor task")) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Uninstalled scheduled task '$TaskName'."
}
