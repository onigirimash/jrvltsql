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

# ── SYNC ──────────────────────────────────────────────────────────────────────
Write-Log "=== SYNC START ==="
$pyArgs = @(
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
$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
& py "-3.12-32" @pyArgs 2>&1 | ForEach-Object {
    $line = "[{0}] [SYNC] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
    Write-Host $line
    [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
}
$ec = $LASTEXITCODE; $ErrorActionPreference = $prev
if ($ec -ne 0) { Write-Log "=== SYNC FAILED (exit: $ec) ===" "ERROR"; exit $ec }
Write-Log "=== SYNC COMPLETE ==="

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
Invoke-Step    "DISADV_DEV"  (Join-Path $root "scripts\calc_disadv_correction.py") $raceDates
Invoke-Step    "PACE_DEV"    (Join-Path $root "scripts\calc_pace_correction.py")   $raceDates
Invoke-Step    "BIAS_DEV"    (Join-Path $root "scripts\calc_bias_correction.py")   $raceDates
Invoke-Step    "TRACK_SPEED" (Join-Path $root "scripts\calc_track_speed.py")       $raceDates
Invoke-Step    "PERF_IDX"    (Join-Path $root "scripts\calc_performance_index.py") $raceDates

# ── Index pipeline (all data) ─────────────────────────────────────────────────
Invoke-StepAll "HORSE_IDX"   (Join-Path $root "scripts\calc_horse_index.py")
Invoke-StepAll "CURRENT_IDX" (Join-Path $root "scripts\calc_current_index.py") @("--recent-weight", "0.4", "--best-weight", "0.6")
Invoke-StepAll "RELIABILITY" (Join-Path $root "scripts\calc_reliability.py")

Write-Log "=== Wednesday sync ALL COMPLETE ==="
