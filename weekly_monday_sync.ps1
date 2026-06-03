#Requires -Version 5.1
<#
.SYNOPSIS
    JRA-VAN Weekly Monday Sync - TARGET CSV Export & DB Import
.DESCRIPTION
    毎週月曜 07:00 に実行。先週末の開催成績を TARGET から CSV 出力し DB に取り込む。

    Step 1: TARGET 起動 → seiseki CSV 出力（基本+単勝オッズ）
    Step 2: seiseki CSV を nl_se にインポート（kakuteijyuni / odds / harontimel3 等を更新）
    Step 3: TARGET → lap CSV 出力（成績画面・レースデータ(ユーザー設定)）
    Step 4: lap CSV を nl_target_race にインポート（ラップタイム・PCI 等を更新）

.NOTES
    Connection : localhost:5432/keiba (user: postgres)
    Python     : py -3.12-32
    Password   : HKCU\Environment\PGPASSWORD または $env:PGPASSWORD
#>

$ErrorActionPreference = "Stop"

$root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $root "logs"

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile   = Join-Path $logDir "weekly_monday_$timestamp.log"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
}

# Log rotation: keep latest 30 files
Get-ChildItem -Path $logDir -Filter "weekly_monday_*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 30 |
    ForEach-Object { Remove-Item $_.FullName -Force }

Write-Log "=== JRA-VAN weekly Monday sync START ==="

# ── Password ──────────────────────────────────────────────────────────────────
$pgPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'User')
if ([string]::IsNullOrEmpty($pgPassword)) { $pgPassword = $env:PGPASSWORD }
if ([string]::IsNullOrEmpty($pgPassword)) {
    Write-Log "PGPASSWORD is not set. Set it via: [Environment]::SetEnvironmentVariable('PGPASSWORD','<pw>','User')" "ERROR"
    exit 1
}

$env:PYTHONIOENCODING = "utf-8"

# ── Python check ──────────────────────────────────────────────────────────────
try {
    $pyVer = & py "-3.12-32" "--version" 2>&1
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Log "Python: $pyVer"
} catch {
    Write-Log "py -3.12-32 not found. JV-Link requires 32-bit Python 3.12." "ERROR"
    exit 1
}

# ── Config ────────────────────────────────────────────────────────────────────
$year          = (Get-Date).Year
$targetTxtDir  = "C:\TFJV\TXT"
$seisekiFile   = Join-Path $targetTxtDir "seiseki_$year.txt"
$lapFile       = Join-Path $targetTxtDir "lap_$year.txt"

$exportScript  = Join-Path $root "scripts\target_csv_export.py"
$seisekiScript = Join-Path $root "scripts\import_target_seiseki.py"
$lapScript     = Join-Path $root "scripts\import_target_csv.py"

Write-Log "Year: $year"
Write-Log "seiseki: $seisekiFile"
Write-Log "lap    : $lapFile"

Set-Location -Path $root

# ── Helper: run a command and stream output to log ────────────────────────────
function Invoke-Logged {
    param([string]$Label, [string[]]$CmdArgs)
    Write-Log "=== $Label START ==="
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & py "-3.12-32" @CmdArgs 2>&1 | ForEach-Object {
        $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Label, $_
        Write-Host $line
        [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
    }
    $ec = $LASTEXITCODE; $ErrorActionPreference = $prev
    if ($ec -eq 0) { Write-Log "=== $Label COMPLETE ===" }
    else           { Write-Log "=== $Label FAILED (exit: $ec) ===" "ERROR"; exit $ec }
}

# ── Step 1: TARGET → seiseki CSV ──────────────────────────────────────────────
Invoke-Logged "SEISEKI_EXPORT" @(
    $exportScript,
    "--restart",
    "--mode",     "seiseki",
    "--out-file", "seiseki_$year.txt",
    "--timeout",  "60"
)

# ── Step 2: seiseki CSV → nl_se ───────────────────────────────────────────────
Invoke-Logged "SEISEKI_IMPORT" @(
    $seisekiScript,
    "--file",        $seisekiFile,
    "--pg-password", $pgPassword
)

# ── Step 3: TARGET → lap CSV ──────────────────────────────────────────────────
Invoke-Logged "LAP_EXPORT" @(
    $exportScript,
    "--no-launch",
    "--mode",     "lap",
    "--out-file", "lap_$year.txt",
    "--timeout",  "60"
)

# ── Step 4: lap CSV → nl_target_race ─────────────────────────────────────────
Invoke-Logged "LAP_IMPORT" @(
    $lapScript,
    $lapFile,
    "--pg-password", $pgPassword
)

Write-Log "=== Monday sync ALL COMPLETE ==="
