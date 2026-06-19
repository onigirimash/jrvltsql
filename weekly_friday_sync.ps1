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

# ── JVLink stall-retry wrapper ────────────────────────────────────────────────
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

                while ($true) {
                    $ln = $reader.ReadLine()
                    if ($null -eq $ln) { break }
                    $ll = "[{0}] [SYNC] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $ln
                    Write-Host $ll
                    [System.IO.File]::AppendAllText($logFile, "$ll`n", $utf8NoBom)
                }

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

            while ($true) {
                $ln = $reader.ReadLine()
                if ($null -eq $ln) { break }
                $ll = "[{0}] [SYNC] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $ln
                Write-Host $ll
                [System.IO.File]::AppendAllText($logFile, "$ll`n", $utf8NoBom)
            }

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

# ── SYNC (with stall-retry) ───────────────────────────────────────────────────
Write-Log "=== SYNC START ==="
$syncArgs = @(
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
if (-not (Invoke-SyncWithRetry -PyArgs $syncArgs)) {
    Write-Log "=== Friday sync ABORTED: SYNC step failed ===" "ERROR"
    exit 1
}

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
