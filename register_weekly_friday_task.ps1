#Requires -Version 5.1
<#
.SYNOPSIS
    Register the JRA-VAN weekly Friday sync job in Windows Task Scheduler.
.DESCRIPTION
    Registers a task that runs weekly_friday_sync.ps1 every Friday at 23:00.
    PGPASSWORD is persisted as a user environment variable so the task can read it at runtime.
.PARAMETER PgPassword
    PostgreSQL password. Defaults to the PGPASSWORD environment variable if omitted.
.PARAMETER TaskName
    Task name (default: JLTSQL_WeeklyFridaySync)
.PARAMETER Force
    Overwrite an existing task with the same name.
.EXAMPLE
    .\register_weekly_friday_task.ps1 -PgPassword "kousuke0809"
    .\register_weekly_friday_task.ps1 -Force
.NOTES
    - The task runs as the currently logged-in user.
    - The PC must be on and logged in on Friday at 23:00.
    - StartWhenAvailable is enabled, so missed runs are executed on next boot/login.
#>

param(
    [string]$PgPassword = "",
    [string]$TaskName   = "JLTSQL_WeeklyFridaySync",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$root       = Split-Path -Parent $MyInvocation.MyCommand.Path
$syncScript = Join-Path $root "weekly_friday_sync.ps1"

if (-not (Test-Path $syncScript)) {
    throw "weekly_friday_sync.ps1 not found: $syncScript"
}

# Resolve password
if ([string]::IsNullOrEmpty($PgPassword)) { $PgPassword = $env:PGPASSWORD }
if ([string]::IsNullOrEmpty($PgPassword)) {
    throw @"
PostgreSQL password is required. Specify it using one of:
  -PgPassword argument:
    .\register_weekly_friday_task.ps1 -PgPassword "yourpassword"
  PGPASSWORD environment variable:
    `$env:PGPASSWORD = "yourpassword"
    .\register_weekly_friday_task.ps1
"@
}

# Persist PGPASSWORD as a user environment variable (HKCU)
[Environment]::SetEnvironmentVariable("PGPASSWORD", $PgPassword, "User")
Write-Host "[OK] PGPASSWORD saved to user environment (HKCU)."

# Check for existing task
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    if ($Force) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[OK] Removed existing task '$TaskName'."
    } else {
        Write-Host ""
        Write-Host "Task '$TaskName' is already registered."
        Write-Host "To overwrite it, re-run with -Force:"
        Write-Host "  .\register_weekly_friday_task.ps1 -Force"
        Write-Host ""
        Write-Host "Current task info:"
        $existingTask | Format-List TaskName, State, LastRunTime, NextRunTime
        exit 0
    }
}

$action = New-ScheduledTaskAction `
    -Execute          "powershell.exe" `
    -Argument         "-NonInteractive -ExecutionPolicy Bypass -File `"$syncScript`"" `
    -WorkingDirectory $root

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At "23:00"

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName    $TaskName `
    -Action      $action `
    -Trigger     $trigger `
    -Settings    $settings `
    -RunLevel    Highest `
    -Description "JRA-VAN weekly Friday sync: next weekend entries + WIN_PROB + EV (localhost:5432/keiba)" | Out-Null

$task    = Get-ScheduledTask -TaskName $TaskName
$nextRun = ($task | Get-ScheduledTaskInfo).NextRunTime

Write-Host ""
Write-Host "============================================================"
Write-Host "  Task registered successfully"
Write-Host "============================================================"
Write-Host "  Task name  : $TaskName"
Write-Host "  Schedule   : Every Friday at 23:00"
Write-Host "  Next run   : $nextRun"
Write-Host "  Script     : $syncScript"
Write-Host "  Database   : localhost:5432/keiba (user: postgres)"
Write-Host "  Log output : $(Join-Path $root 'logs\weekly_friday_*.log')"
Write-Host ""
Write-Host "  Run manually:"
Write-Host "    Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "  Check status:"
Write-Host "    Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host ""
Write-Host "  Remove task:"
Write-Host "    Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host "============================================================"
