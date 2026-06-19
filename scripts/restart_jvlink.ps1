#Requires -Version 5.1
<#
.SYNOPSIS
    Restarts the JVLinkAgent Windows service.
.DESCRIPTION
    Called by weekly sync scripts when a JVLink download stalls.
    Stops JVLinkAgent, waits for full stop, then starts it again.
    Outputs $true on success, $false on failure.
.PARAMETER StopTimeoutSec
    Seconds to wait for the service to reach Stopped state (default: 30).
.PARAMETER StartTimeoutSec
    Seconds to wait for the service to reach Running state (default: 30).
#>
param(
    [int]$StopTimeoutSec = 30,
    [int]$StartTimeoutSec = 30
)

$svcName = "JVLinkAgent"
$ts = { "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [JVLINK]" }

Write-Host "$(& $ts) Restarting service: $svcName"

# ── Stop ──────────────────────────────────────────────────────────────────────
try {
    $svc = Get-Service -Name $svcName -ErrorAction Stop
    if ($svc.Status -ne 'Stopped') {
        Stop-Service -Name $svcName -Force -ErrorAction Stop
        $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds($StopTimeoutSec))
    }
    Write-Host "$(& $ts) $svcName stopped."
} catch {
    Write-Host "$(& $ts) ERROR: failed to stop ${svcName}: $_"
    return $false
}

Start-Sleep -Seconds 3

# ── Start ─────────────────────────────────────────────────────────────────────
try {
    Start-Service -Name $svcName -ErrorAction Stop
    $svc = Get-Service -Name $svcName -ErrorAction Stop
    $svc.WaitForStatus('Running', [TimeSpan]::FromSeconds($StartTimeoutSec))
    Write-Host "$(& $ts) $svcName started successfully."
    return $true
} catch {
    Write-Host "$(& $ts) ERROR: failed to start ${svcName}: $_"
    return $false
}