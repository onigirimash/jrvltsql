#Requires -Version 5.1
<#
.SYNOPSIS
    JRA-VAN Weekly Friday Sync - Next Weekend Entries + Prediction
.DESCRIPTION
    1. Fetches today to +7 days differential data (SE/RA for next weekend entries)
    2. Runs WIN_PROB -> EV for next Saturday and Sunday
    Past results and index calculation are handled by weekly_wednesday_sync.ps1.
    Intended to run every Friday at 23:00 via Task Scheduler.
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
$logFile   = Join-Path $logDir "weekly_friday_$timestamp.log"

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
}

# Log rotation: keep latest 30 files
Get-ChildItem -Path $logDir -Filter "weekly_friday_*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 30 |
    ForEach-Object { Remove-Item $_.FullName -Force }

Write-Log "=== JRA-VAN weekly Friday sync START ==="

# ── Password ──────────────────────────────────────────────────────────────────
$pgPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'User')
if ([string]::IsNullOrEmpty($pgPassword)) { $pgPassword = $env:PGPASSWORD }
if ([string]::IsNullOrEmpty($pgPassword)) {
    Write-Log "PGPASSWORD is not set in User environment or session. Run register_weekly_friday_task.ps1 to configure it." "ERROR"
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
$today  = Get-Date
$dowInt = [int]$today.DayOfWeek   # 0=Sun … 6=Sat

# Sync range: today to +7 days (captures next weekend SE/RA data)
$syncFrom = $today.ToString("yyyyMMdd")
$syncTo   = $today.AddDays(7).ToString("yyyyMMdd")
Write-Log "Sync range: $syncFrom - $syncTo"

# Next weekend dates: next Saturday and Sunday
# (6 - dowInt + 7) % 7 gives days until next Saturday; 0 → use 7 (already Saturday → next week)
$daysToNextSat = (6 - $dowInt + 7) % 7
if ($daysToNextSat -eq 0) { $daysToNextSat = 7 }
$nextSat   = $today.AddDays($daysToNextSat)
$nextSun   = $nextSat.AddDays(1)
$raceDates = @($nextSat.ToString("yyyyMMdd"), $nextSun.ToString("yyyyMMdd"))
Write-Log "Race dates (next weekend): $($raceDates -join ', ')"

Set-Location -Path $root
$quickstart = Join-Path $root "scripts\quickstart.py"

# ── Helper: run py script per race date ───────────────────────────────────────
function Invoke-Step {
    param([string]$Label, [string]$Script, [string[]]$Dates, [string[]]$ExtraArgs = @())
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
            "--pg-password", $pgPassword) + $ExtraArgs
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

# ── SYNC ──────────────────────────────────────────────────────────────────────
Write-Log "=== SYNC START ==="
$pyArgs = @(
    $quickstart,
    "--mode",        "update",
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

# ── WIN_PROB ──────────────────────────────────────────────────────────────────
Invoke-Step "WIN_PROB" (Join-Path $root "scripts\calc_win_prob.py")       $raceDates @("--max-debut", "999")

# ── EV ────────────────────────────────────────────────────────────────────────
Invoke-Step "EV"       (Join-Path $root "scripts\calc_expected_value.py") $raceDates

# ── TARGET CSV IMPORT ─────────────────────────────────────────────────────────
Write-Log "=== TARGET CSV IMPORT START ==="

$targetTxtDir   = "C:\TFJV\TXT"
$lastImportFile = Join-Path $logDir "target_last_import.txt"
$importScript   = Join-Path $root "scripts\import_target_csv.py"

if (-not (Test-Path $importScript)) {
    Write-Log "import_target_csv.py not found - skip" "WARN"
} elseif (-not (Test-Path $targetTxtDir)) {
    Write-Log "TARGET TXT dir not found: $targetTxtDir - skip" "WARN"
} else {
    # 前回インポート日時を読み込む（なければ最古扱い）
    $lastImport = [DateTime]::MinValue
    if (Test-Path $lastImportFile) {
        $raw = (Get-Content $lastImportFile -TotalCount 1 -Encoding UTF8).Trim()
        if ($raw -and [DateTime]::TryParse($raw, [ref]$lastImport)) {
            Write-Log "前回インポート日時: $($lastImport.ToString('yyyy-MM-dd HH:mm:ss'))"
        } else {
            Write-Log "target_last_import.txt のパース失敗 - 全ファイルを対象とします" "WARN"
            $lastImport = [DateTime]::MinValue
        }
    } else {
        Write-Log "target_last_import.txt なし - 全ファイルを初回インポートとして対象とします"
    }

    # 前回より新しい .txt ファイルを検索
    $newFiles = Get-ChildItem -Path $targetTxtDir -Filter "*.txt" -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTime -gt $lastImport } |
                Sort-Object LastWriteTime

    if ($newFiles.Count -eq 0) {
        Write-Log "新規TARGETファイルなし（スキップ）"
    } else {
        Write-Log "新規ファイル $($newFiles.Count) 件を検出:"
        foreach ($f in $newFiles) {
            Write-Log "  $($f.Name)  ($($f.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')))"
        }

        $filePaths = $newFiles | ForEach-Object { $_.FullName }
        $pyArgs = @($importScript) + $filePaths + @(
            "--pg-host",     $env:POSTGRES_HOST,
            "--pg-port",     ([string]$env:POSTGRES_PORT),
            "--pg-database", $env:POSTGRES_DATABASE,
            "--pg-user",     $env:POSTGRES_USER,
            "--pg-password", $pgPassword
        )

        $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        & py "-3.12-32" @pyArgs 2>&1 | ForEach-Object {
            $line = "[{0}] [TARGET_CSV] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
            Write-Host $line
            [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
        }
        $ec = $LASTEXITCODE; $ErrorActionPreference = $prev

        if ($ec -eq 0) {
            # インポート成功 → 最終インポート日時を更新
            $nowStr = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
            [System.IO.File]::WriteAllText($lastImportFile, $nowStr, $utf8NoBom)
            Write-Log "TARGET CSV インポート完了。最終日時を更新: $nowStr"
        } else {
            Write-Log "TARGET CSV インポート失敗 (exit: $ec) - 最終日時は更新しません" "WARN"
        }
    }
}

Write-Log "=== TARGET CSV IMPORT COMPLETE ==="

Write-Log "=== Friday sync ALL COMPLETE ==="
