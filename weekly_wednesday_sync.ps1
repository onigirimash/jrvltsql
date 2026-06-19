#Requires -Version 5.1
<#
.SYNOPSIS
    JRA-VAN Weekly Wednesday Sync - Past Results + Index Pipeline
.DESCRIPTION
    1. Fetches past 8 days differential data from JRA-VAN (SE/RA/O1/etc.)
    2. Runs the full index pipeline for last weekend:
       HARON_FIX -> TIME_DEV -> FUTAN_DEV -> DISADV_DEV -> PACE_DEV -> BIAS_DEV
       -> TRACK_SPEED -> PERF_IDX -> HORSE_IDX -> CURRENT_IDX -> RELIABILITY
    WIN_PROB and EV are handled by weekly_friday_sync.ps1.
    Intended to run every Wednesday at 23:00 via Task Scheduler.
.NOTES
    Connection : localhost:5432/keiba (user: postgres)
    Python     : py -3.12-32
    Password   : read from HKCU\Environment via [Environment]::GetEnvironmentVariable
#>

$ErrorActionPreference = "Stop"

$root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $root "logs"

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile   = Join-Path $logDir "weekly_wednesday_$timestamp.log"

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
}

# Log rotation: keep latest 30 files
Get-ChildItem -Path $logDir -Filter "weekly_wednesday_*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 30 |
    ForEach-Object { Remove-Item $_.FullName -Force }

Write-Log "=== JRA-VAN weekly Wednesday sync START ==="

# ── Password ──────────────────────────────────────────────────────────────────
$pgPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'User')
if ([string]::IsNullOrEmpty($pgPassword)) { $pgPassword = $env:PGPASSWORD }
if ([string]::IsNullOrEmpty($pgPassword)) {
    Write-Log "PGPASSWORD is not set in User environment or session. Run register_weekly_wednesday_task.ps1 to configure it." "ERROR"
    exit 1
}

$env:POSTGRES_HOST     = "localhost"
$env:POSTGRES_PORT     = "5432"
$env:POSTGRES_DATABASE = "keiba"
$env:POSTGRES_USER     = "postgres"
$env:POSTGRES_PASSWORD = $pgPassword
$env:PYTHONIOENCODING  = "utf-8"

Write-Log "Connection: $($env:POSTGRES_HOST):$($env:POSTGRES_PORT)/$($env:POSTGRES_DATABASE)  user: $($env:POSTGRES_USER)"

try {
    $pyVer = & py "-3.12-32" "--version" 2>&1
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Log "Python: $pyVer"
} catch {
    Write-Log "py -3.12-32 not found. JV-Link requires 32-bit Python 3.12." "ERROR"
    exit 1
}

# ── Date calculations ─────────────────────────────────────────────────────────
$today   = Get-Date
$dowInt  = [int]$today.DayOfWeek              # 0=Sun … 6=Sat

# Sync range: past 8 days → tomorrow
$syncFrom = $today.AddDays(-8).ToString("yyyyMMdd")
$syncTo   = $today.AddDays(1).ToString("yyyyMMdd")
Write-Log "Sync range: $syncFrom - $syncTo"

# Last weekend dates for step calculations (most recent Sat/Sun before today)
$daysToLastSat = if ($dowInt -ge 1) { $dowInt + 1 } else { 7 }
$lastSat  = $today.AddDays(-$daysToLastSat)
$lastSun  = $lastSat.AddDays(1)
$raceDates = @($lastSat.ToString("yyyyMMdd"), $lastSun.ToString("yyyyMMdd"))
Write-Log "Race dates (last weekend): $($raceDates -join ', ')"

Set-Location -Path $root
$quickstart = Join-Path $root "scripts\quickstart.py"

# ── JVLink stall-retry wrapper ────────────────────────────────────────────────
# Runs quickstart.py and monitors stdout for stalls (no new output for $StallSec).
# On stall: kills the process, restarts JVLinkAgent, retries up to $MaxRetry times.
# Returns $true on success, $false on total failure.
function Invoke-SyncWithRetry {
    param(
        [string[]]$PyArgs,
        [int]$MaxRetry = 3,
        [int]$StallSec = 60
    )

    $restartScript = Join-Path $root "scripts\restart_jvlink.ps1"
    $totalAttempts = $MaxRetry + 1

    for ($attempt = 1; $attempt -le $totalAttempts; $attempt++) {
        Write-Log "=== SYNC attempt $attempt/$totalAttempts START ==="

        $tmpOut = [System.IO.Path]::GetTempFileName()
        $tmpErr = [System.IO.Path]::GetTempFileName()

        $proc = Start-Process -FilePath "py" `
            -ArgumentList (@("-3.12-32") + $PyArgs) `
            -RedirectStandardOutput $tmpOut `
            -RedirectStandardError  $tmpErr `
            -PassThru -NoNewWindow

        Start-Sleep -Milliseconds 500

        $reader = [System.IO.StreamReader]::new(
            [System.IO.File]::Open($tmpOut,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::ReadWrite),
            [System.Text.Encoding]::UTF8
        )

        $stalled    = $false
        $lastSize   = 0
        $lastMoveAt = Get-Date

        try {
            while (-not $proc.HasExited) {
                Start-Sleep -Seconds 5

                # Stream new output lines to log
                while ($true) {
                    $ln = $reader.ReadLine()
                    if ($null -eq $ln) { break }
                    $ll = "[{0}] [SYNC] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $ln
                    Write-Host $ll
                    [System.IO.File]::AppendAllText($logFile, "$ll`n", $utf8NoBom)
                }

                # Stall check
                $fi = Get-Item $tmpOut -ErrorAction SilentlyContinue
                $curSize = if ($fi) { $fi.Length } else { 0 }
                if ($curSize -gt $lastSize) {
                    $lastSize   = $curSize
                    $lastMoveAt = Get-Date
                } elseif (((Get-Date) - $lastMoveAt).TotalSeconds -ge $StallSec) {
                    Write-Log "SYNC: no output for ${StallSec}s - stall detected, killing process" "WARN"
                    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                    $stalled = $true
                    break
                }
            }

            # Drain remaining stdout
            while ($true) {
                $ln = $reader.ReadLine()
                if ($null -eq $ln) { break }
                $ll = "[{0}] [SYNC] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $ln
                Write-Host $ll
                [System.IO.File]::AppendAllText($logFile, "$ll`n", $utf8NoBom)
            }

            # Drain stderr
            if (Test-Path $tmpErr) {
                foreach ($ln in (Get-Content $tmpErr -Encoding UTF8 -ErrorAction SilentlyContinue)) {
                    if ($ln) {
                        $ll = "[{0}] [SYNC:ERR] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $ln
                        Write-Host $ll
                        [System.IO.File]::AppendAllText($logFile, "$ll`n", $utf8NoBom)
                    }
                }
            }
        } finally {
            $reader.Dispose()
            Remove-Item $tmpOut -Force -ErrorAction SilentlyContinue
            Remove-Item $tmpErr -Force -ErrorAction SilentlyContinue
        }

        if ($stalled) {
            Write-Log "SYNC attempt $attempt/$totalAttempts stalled." "WARN"
            if ($attempt -lt $totalAttempts) {
                Write-Log "Restarting JVLinkAgent before retry (attempt $($attempt+1)/$totalAttempts)..."
                if (Test-Path $restartScript) {
                    $ok = & $restartScript
                    if ($ok) {
                        Write-Log "JVLinkAgent restarted. Waiting 10s..."
                        Start-Sleep -Seconds 10
                    } else {
                        Write-Log "JVLinkAgent restart failed - will retry anyway." "WARN"
                        Start-Sleep -Seconds 5
                    }
                } else {
                    Write-Log "restart_jvlink.ps1 not found - skipping restart." "WARN"
                }
                continue
            }
            Write-Log "=== SYNC FAILED: stalled on all $totalAttempts attempts ===" "ERROR"
            return $false
        }

        $proc.WaitForExit()
        $ec = $proc.ExitCode
        if ($ec -eq 0) {
            Write-Log "=== SYNC attempt $attempt COMPLETE ==="
            return $true
        }
        Write-Log "=== SYNC attempt $attempt FAILED (exit: $ec) ===" "ERROR"
        return $false
    }

    return $false
}

# ── Helper: run py script per race date ───────────────────────────────────────
function Invoke-Step {
    param([string]$Label, [string]$Script, [string[]]$Dates)
    Write-Log "=== $Label START ==="
    if (-not (Test-Path $Script)) { Write-Log "${Label}: $Script not found - skip" "WARN"; return }
    $anyFail = $false
    foreach ($d in $Dates) {
        Write-Log "[$Label] $d start"
        $pyArgs = @($Script, "--date", $d,
            "--pg-host",     $env:POSTGRES_HOST,
            "--pg-port",     ([string]$env:POSTGRES_PORT),
            "--pg-database", $env:POSTGRES_DATABASE,
            "--pg-user",     $env:POSTGRES_USER,
            "--pg-password", $pgPassword)
        $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        & py "-3.12-32" @pyArgs 2>&1 | ForEach-Object {
            $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Label, $_
            Write-Host $line
            [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
        }
        $ec = $LASTEXITCODE; $ErrorActionPreference = $prev
        if ($ec -eq 0) { Write-Log "[$Label] $d done" }
        else            { Write-Log "[$Label] $d FAILED (exit: $ec)" "WARN"; $anyFail = $true }
    }
    if ($anyFail) { Write-Log "=== $Label PARTIAL FAILURE ===" "WARN" }
    else          { Write-Log "=== $Label COMPLETE ===" }
}

# Helper: run py script with --date-from / --date-to (range steps)
# Used for review-based corrections that may be entered retrospectively.
function Invoke-StepRange {
    param([string]$Label, [string]$Script, [string]$DateFrom, [string]$DateTo)
    Write-Log "=== $Label START (${DateFrom} - ${DateTo}) ==="
    if (-not (Test-Path $Script)) { Write-Log "${Label}: $Script not found - skip" "WARN"; return }
    $pyArgs = @($Script,
        "--date-from", $DateFrom,
        "--date-to",   $DateTo,
        "--pg-host",     $env:POSTGRES_HOST,
        "--pg-port",     ([string]$env:POSTGRES_PORT),
        "--pg-database", $env:POSTGRES_DATABASE,
        "--pg-user",     $env:POSTGRES_USER,
        "--pg-password", $pgPassword)
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & py "-3.12-32" @pyArgs 2>&1 | ForEach-Object {
        $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Label, $_
        Write-Host $line
        [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
    }
    $ec = $LASTEXITCODE; $ErrorActionPreference = $prev
    if ($ec -eq 0) { Write-Log "=== $Label COMPLETE ===" }
    else           { Write-Log "=== $Label FAILED (exit: $ec) ===" "WARN" }
}

# Helper: run py script with no date arg (whole-dataset steps)
function Invoke-StepAll {
    param([string]$Label, [string]$Script, [string[]]$ExtraArgs = @())
    Write-Log "=== $Label START ==="
    if (-not (Test-Path $Script)) { Write-Log "${Label}: $Script not found - skip" "WARN"; return }
    $pyArgs = @($Script,
        "--pg-host",     $env:POSTGRES_HOST,
        "--pg-port",     ([string]$env:POSTGRES_PORT),
        "--pg-database", $env:POSTGRES_DATABASE,
        "--pg-user",     $env:POSTGRES_USER,
        "--pg-password", $pgPassword) + $ExtraArgs
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & py "-3.12-32" @pyArgs 2>&1 | ForEach-Object {
        $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Label, $_
        Write-Host $line
        [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
    }
    $ec = $LASTEXITCODE; $ErrorActionPreference = $prev
    if ($ec -eq 0) { Write-Log "=== $Label COMPLETE ===" }
    else           { Write-Log "=== $Label FAILED (exit: $ec) ===" "WARN" }
}

# ── SYNC (with stall-retry) ───────────────────────────────────────────────────
Write-Log "=== SYNC START ==="
$syncArgs = @(
    $quickstart,
    "--mode",        "update",
    "--no-toku",
    "--no-tcvn",
    "--no-rcvn",
    "--db-type",     "postgresql",
    "--pg-host",     $env:POSTGRES_HOST,
    "--pg-port",     $env:POSTGRES_PORT,
    "--pg-database", $env:POSTGRES_DATABASE,
    "--pg-user",     $env:POSTGRES_USER,
    "--pg-password", $pgPassword,
    "--from-date",   $syncFrom,
    "--to-date",     $syncTo,
    "--yes"
)
if (-not (Invoke-SyncWithRetry -PyArgs $syncArgs)) {
    Write-Log "=== Wednesday sync ABORTED: SYNC step failed ===" "ERROR"
    exit 1
}

# ── 気象データ同期 ─────────────────────────────────────────────────────────────
Write-Log "=== WEATHER START ==="
$null = & py "-3.12-32" "-c" "import bs4" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log "Installing beautifulsoup4..."
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & py "-3.12-32" "-m" "pip" "install" "beautifulsoup4" 2>&1 | ForEach-Object { Write-Host $_ }
    $ErrorActionPreference = $prev
}
$weatherScript = Join-Path $root "scripts\sync_weather.py"
if (Test-Path $weatherScript) {
    $pyArgs = @($weatherScript,
        "--pg-host",     $env:POSTGRES_HOST,
        "--pg-port",     ([string]$env:POSTGRES_PORT),
        "--pg-database", $env:POSTGRES_DATABASE,
        "--pg-user",     $env:POSTGRES_USER,
        "--pg-password", $pgPassword)
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & py "-3.12-32" @pyArgs 2>&1 | ForEach-Object {
        $line = "[{0}] [WEATHER] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
        Write-Host $line
        [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
    }
    $ec = $LASTEXITCODE; $ErrorActionPreference = $prev
    if ($ec -eq 0) { Write-Log "=== WEATHER COMPLETE ===" }
    else           { Write-Log "=== WEATHER FAILED (exit: $ec) - continuing ===" "WARN" }
} else { Write-Log "sync_weather.py not found - skip" "WARN" }

# ── HARON_FIX ─────────────────────────────────────────────────────────────────
Write-Log "=== HARON_FIX START ==="
$haronSql = Join-Path $root "scripts\fix_nl_ra_haron.sql"
if (Test-Path $haronSql) {
    $_psqlGet = Get-Command psql -ErrorAction SilentlyContinue
    $psqlCmd  = if ($_psqlGet) { $_psqlGet.Source } else { $null }
    if (-not $psqlCmd) { $psqlCmd = "C:\Program Files\PostgreSQL\17\bin\psql.exe" }
    if (Test-Path $psqlCmd) {
        $env:PGPASSWORD = $pgPassword
        $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        & $psqlCmd -U $env:POSTGRES_USER -h $env:POSTGRES_HOST -p $env:POSTGRES_PORT `
                   -d $env:POSTGRES_DATABASE -f $haronSql 2>&1 | ForEach-Object {
            $line = "[{0}] [HARON_FIX] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
            Write-Host $line
            [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
        }
        $ec = $LASTEXITCODE; $ErrorActionPreference = $prev
        if ($ec -eq 0) { Write-Log "=== HARON_FIX COMPLETE ===" }
        else           { Write-Log "=== HARON_FIX FAILED (exit: $ec) ===" "WARN" }
    } else { Write-Log "psql.exe not found - skip" "WARN" }
} else { Write-Log "fix_nl_ra_haron.sql not found - skip" "WARN" }

# ── Index pipeline (last weekend) ─────────────────────────────────────────────
Invoke-Step    "TIME_DEV"    (Join-Path $root "scripts\calc_time_deviation.py")    $raceDates
Invoke-Step    "FUTAN_DEV"   (Join-Path $root "scripts\calc_futan_correction.py")  $raceDates
Invoke-Step    "PACE_DEV"    (Join-Path $root "scripts\calc_pace_correction.py")   $raceDates
Invoke-Step    "TRACK_SPEED" (Join-Path $root "scripts\calc_track_speed.py")       $raceDates
Invoke-Step    "PERF_IDX"    (Join-Path $root "scripts\calc_performance_index.py") $raceDates

# ── Review-based corrections: 4-week window (captures retrospective entries) ───
# review_disadvantage / review_track_bias は後から入力されることがあるため
# 直近4週間を毎週再処理して取りこぼしを防ぐ
$reviewFrom = $today.AddDays(-28).ToString("yyyyMMdd")
$reviewTo   = $lastSun.ToString("yyyyMMdd")
Write-Log "Review correction window: $reviewFrom - $reviewTo"
Invoke-StepRange "DISADV_DEV" (Join-Path $root "scripts\calc_disadv_correction.py") $reviewFrom $reviewTo
Invoke-StepRange "BIAS_DEV"   (Join-Path $root "scripts\calc_bias_correction.py")   $reviewFrom $reviewTo

# ── Index pipeline (all data) ─────────────────────────────────────────────────
Invoke-StepAll "HORSE_IDX"   (Join-Path $root "scripts\calc_horse_index.py")
Invoke-StepAll "CURRENT_IDX" (Join-Path $root "scripts\calc_current_index.py")
Invoke-StepAll "RELIABILITY" (Join-Path $root "scripts\calc_reliability.py")

Write-Log "=== Wednesday sync ALL COMPLETE ==="
