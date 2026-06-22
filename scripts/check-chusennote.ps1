[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8877",
    [int]$TimeoutSec = 5
)

$ErrorActionPreference = "Stop"
$RootUrl = $BaseUrl.TrimEnd('/')
$Checks = @()

function Add-Check {
    param(
        [string]$Name,
        [string]$Path,
        [scriptblock]$Request,
        [scriptblock]$Validate
    )

    $Url = "$RootUrl$Path"
    try {
        $Value = & $Request $Url
        $Valid = & $Validate $Value
        if (-not $Valid) {
            throw "Unexpected response from $Url"
        }
        $script:Checks += [pscustomobject]@{
            name = $Name
            path = $Path
            ok = $true
        }
    } catch {
        $script:Checks += [pscustomobject]@{
            name = $Name
            path = $Path
            ok = $false
            error = $_.Exception.Message
        }
    }
}

function Get-Json {
    param([string]$Url)
    Invoke-RestMethod -Uri $Url -TimeoutSec $TimeoutSec
}

function Get-Text {
    param([string]$Url)
    (Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSec -UseBasicParsing).Content
}

Add-Check "home" "/" ${function:Get-Text} { param($Value) $Value -like "*chusennote*" }
Add-Check "health" "/api/health" ${function:Get-Json} { param($Value) $Value.status -eq "ok" -and $Value.app -eq "chusennote" }
Add-Check "watchlist" "/api/watchlist?include_muted=1" ${function:Get-Json} { param($Value) $null -ne $Value }
Add-Check "events" "/api/events?include_muted=1" ${function:Get-Json} { param($Value) $null -ne $Value }
Add-Check "upcoming" "/api/upcoming?include_muted=1" ${function:Get-Json} { param($Value) $null -ne $Value }
Add-Check "alerts" "/api/alerts" ${function:Get-Json} { param($Value) $null -ne $Value }
Add-Check "sources" "/api/sources?include_muted=1" ${function:Get-Json} { param($Value) $null -ne $Value }
Add-Check "calendar" "/calendar.ics?include_muted=1" ${function:Get-Text} { param($Value) $Value -like "*BEGIN:VCALENDAR*" }

$Health = $null
try {
    $Health = Invoke-RestMethod -Uri "$RootUrl/api/health" -TimeoutSec $TimeoutSec
} catch {
    $Health = $null
}

$Failures = @($Checks | Where-Object { -not $_.ok })
$Summary = [pscustomobject]@{
    base_url = $RootUrl
    ok = $Failures.Count -eq 0
    health = $Health
    checks = $Checks
}

$Summary | ConvertTo-Json -Depth 8

if ($Failures.Count -eq 0) {
    exit 0
}

Write-Error "chusennote smoke check failed for $($Failures.Count) endpoint(s)."
exit 1
