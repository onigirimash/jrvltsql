#Requires -Version 5.1
<#
.SYNOPSIS
    JRA-VAN Weekly Differential Data Auto-Sync
.DESCRIPTION
    Fetches differential data from JRA-VAN and stores it in PostgreSQL.
    Intended to run every Thursday at 23:00 via Task Scheduler.
.NOTES
    Connection : localhost:5432/keiba (user: postgres)
    Python     : py -3.12-32
    Password   : read from HKCU\Environment (User) via [Environment]::GetEnvironmentVariable,
                 with fallback to session $env:PGPASSWORD for manual runs
#>

$ErrorActionPreference = "Stop"

$root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $root "logs"

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile   = Join-Path $logDir "weekly_sync_$timestamp.log"

# UTF-8 without BOM encoder (PowerShell 5.1 Add-Content -Encoding UTF8 adds BOM)
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
}

# Log rotation: delete oldest files when count exceeds 30
Get-ChildItem -Path $logDir -Filter "weekly_sync_*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 30 |
    ForEach-Object { Remove-Item $_.FullName -Force }

Write-Log "=== JRA-VAN weekly diff sync START ==="

# Read PGPASSWORD from User environment (HKCU\Environment) directly so Task Scheduler
# picks it up regardless of when the variable was set relative to process launch.
# Fall back to the session variable for interactive/manual runs.
$pgPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'User')
if ([string]::IsNullOrEmpty($pgPassword)) {
    $pgPassword = $env:PGPASSWORD
}
if ([string]::IsNullOrEmpty($pgPassword)) {
    Write-Log "PGPASSWORD is not set in User environment (HKCU\Environment) or the current session. Run register_weekly_thursday_task.ps1 to configure it." "ERROR"
    exit 1
}

# Set PostgreSQL connection environment variables
$env:POSTGRES_HOST     = "localhost"
$env:POSTGRES_PORT     = "5432"
$env:POSTGRES_DATABASE = "keiba"
$env:POSTGRES_USER     = "postgres"
$env:POSTGRES_PASSWORD = $pgPassword
$env:PYTHONIOENCODING  = "utf-8"

Write-Log "Connection: $($env:POSTGRES_HOST):$($env:POSTGRES_PORT)/$($env:POSTGRES_DATABASE)  user: $($env:POSTGRES_USER)"

# Verify py -3.12-32 is available
try {
    $pyVer = & py "-3.12-32" "--version" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "non-zero exit" }
    Write-Log "Python: $pyVer"
} catch {
    Write-Log "py -3.12-32 not found. JV-Link requires 32-bit Python 3.12." "ERROR"
    exit 1
}

# Date range: 8 days back to tomorrow, to reliably cover since the last Thursday run
$fromDate = (Get-Date).AddDays(-8).ToString("yyyyMMdd")
$toDate   = (Get-Date).AddDays(1).ToString("yyyyMMdd")
Write-Log "Diff date range: $fromDate - $toDate"

$quickstart = Join-Path $root "scripts\quickstart.py"
if (-not (Test-Path $quickstart)) {
    Write-Log "scripts\quickstart.py not found: $quickstart" "ERROR"
    exit 1
}

$pyArgs = @(
    $quickstart,
    "--mode",        "update",
    "--db-type",     "postgresql",
    "--pg-host",     $env:POSTGRES_HOST,
    "--pg-port",     $env:POSTGRES_PORT,
    "--pg-database", $env:POSTGRES_DATABASE,
    "--pg-user",     $env:POSTGRES_USER,
    "--pg-password", $pgPassword,
    "--from-date",   $fromDate,
    "--to-date",     $toDate,
    "--yes"
)

Write-Log "Starting sync..."
Set-Location -Path $root

# Use ErrorActionPreference = Continue around the pipeline to prevent PowerShell 5.1
# from treating native-command stderr lines (wrapped as ErrorRecord) as terminating errors.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"

& py "-3.12-32" @pyArgs 2>&1 | ForEach-Object {
    $line = "[{0}] [SYNC] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
    Write-Host $line
    [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
}
$exitCode = $LASTEXITCODE

$ErrorActionPreference = $prevEAP

if ($exitCode -eq 0) {
    Write-Log "=== Weekly diff sync COMPLETE ==="
} else {
    Write-Log "=== Weekly diff sync FAILED (exit code: $exitCode) ===" "ERROR"
    exit $exitCode
}

# ============================================================
# 気象データ同期（先週土日の全開催競馬場）
# ============================================================
Write-Log "=== 気象データ同期 START ==="

# beautifulsoup4 がなければインストール
$null = & py "-3.12-32" "-c" "import bs4" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log "beautifulsoup4 を自動インストール中..."
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & py "-3.12-32" "-m" "pip" "install" "beautifulsoup4" 2>&1 | ForEach-Object {
        $line = "[{0}] [PIP] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
        Write-Host $line
        [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
    }
    $ErrorActionPreference = $prevEAP
    if ($LASTEXITCODE -ne 0) {
        Write-Log "beautifulsoup4 インストール失敗 - 気象同期をスキップ" "WARN"
        Write-Log "手動インストール: py -3.12-32 -m pip install beautifulsoup4" "WARN"
        exit 0
    }
}

$weatherScript = Join-Path $root "scripts\sync_weather.py"
if (-not (Test-Path $weatherScript)) {
    Write-Log "scripts\sync_weather.py が見つかりません - スキップ" "WARN"
} else {
    $weatherArgs = @(
        $weatherScript,
        "--pg-host",     $env:POSTGRES_HOST,
        "--pg-port",     ([string]$env:POSTGRES_PORT),
        "--pg-database", $env:POSTGRES_DATABASE,
        "--pg-user",     $env:POSTGRES_USER,
        "--pg-password", $pgPassword
    )

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    & py "-3.12-32" @weatherArgs 2>&1 | ForEach-Object {
        $line = "[{0}] [WEATHER] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
        Write-Host $line
        [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
    }
    $weatherExitCode = $LASTEXITCODE

    $ErrorActionPreference = $prevEAP

    if ($weatherExitCode -eq 0) {
        Write-Log "=== 気象データ同期 COMPLETE ==="
    } else {
        # 気象同期の失敗はメイン同期に影響しない（WARN 扱い）
        Write-Log "=== 気象データ同期 FAILED (exit: $weatherExitCode) - メイン同期は正常完了 ===" "WARN"
    }
}

# ============================================================
# nl_ra.haron3l 補完（nl_se からの再集計・外れ値クリア）
# ============================================================
Write-Log "=== haron3l 補完 START ==="

$haronSql = Join-Path $root "scripts\fix_nl_ra_haron.sql"
if (-not (Test-Path $haronSql)) {
    Write-Log "[HARON_FIX] scripts\fix_nl_ra_haron.sql が見つかりません - スキップ" "WARN"
} else {
    # psql を検索（PATH 未登録環境に対応）
    $psqlCmd = Get-Command psql -ErrorAction SilentlyContinue |
               Select-Object -ExpandProperty Source
    if (-not $psqlCmd) {
        $psqlCmd = "C:\Program Files\PostgreSQL\17\bin\psql.exe"
    }

    if (-not (Test-Path $psqlCmd)) {
        Write-Log "[HARON_FIX] psql.exe が見つかりません - スキップ" "WARN"
    } else {
        $env:PGPASSWORD = $pgPassword

        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"

        & $psqlCmd `
            -U $env:POSTGRES_USER `
            -h $env:POSTGRES_HOST `
            -p $env:POSTGRES_PORT `
            -d $env:POSTGRES_DATABASE `
            -f $haronSql 2>&1 | ForEach-Object {
            $line = "[{0}] [HARON_FIX] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
            Write-Host $line
            [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
        }
        $haronExitCode = $LASTEXITCODE

        $ErrorActionPreference = $prevEAP

        if ($haronExitCode -eq 0) {
            Write-Log "=== haron3l 補完 COMPLETE ==="
        } else {
            Write-Log "=== haron3l 補完 FAILED (exit: $haronExitCode) - メイン同期は正常完了 ===" "WARN"
        }
    }
}

# ============================================================
# タイム偏差計算（先週土日）Step 2
# ============================================================
Write-Log "=== タイム偏差計算 START ==="

$timeDevScript = Join-Path $root "scripts\calc_time_deviation.py"
if (-not (Test-Path $timeDevScript)) {
    Write-Log "scripts\calc_time_deviation.py が見つかりません - スキップ" "WARN"
} else {
    $today   = Get-Date
    $dowInt  = [int]$today.DayOfWeek
    $daysToLastSat = if ($dowInt -ge 1) { $dowInt + 1 } else { 7 }
    $lastSat = $today.AddDays(-$daysToLastSat)
    $lastSun = $lastSat.AddDays(1)

    $raceDates = @(
        $lastSat.ToString("yyyyMMdd"),
        $lastSun.ToString("yyyyMMdd")
    )
    Write-Log "[TIME_DEV] 対象日: $($raceDates -join ', ')"

    $timeDevFailed = $false

    foreach ($raceDate in $raceDates) {
        Write-Log "[TIME_DEV] $raceDate 計算開始"

        $tdArgs = @(
            $timeDevScript,
            "--date",        $raceDate,
            "--pg-host",     $env:POSTGRES_HOST,
            "--pg-port",     ([string]$env:POSTGRES_PORT),
            "--pg-database", $env:POSTGRES_DATABASE,
            "--pg-user",     $env:POSTGRES_USER,
            "--pg-password", $pgPassword
        )

        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"

        & py "-3.12-32" @tdArgs 2>&1 | ForEach-Object {
            $line = "[{0}] [TIME_DEV] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
            Write-Host $line
            [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
        }
        $tdExitCode = $LASTEXITCODE

        $ErrorActionPreference = $prevEAP

        if ($tdExitCode -eq 0) {
            Write-Log "[TIME_DEV] $raceDate 計算完了"
        } else {
            Write-Log "[TIME_DEV] $raceDate 計算失敗 (exit: $tdExitCode)" "WARN"
            $timeDevFailed = $true
        }
    }

    if ($timeDevFailed) {
        Write-Log "=== タイム偏差計算 一部失敗 - メイン同期は正常完了 ===" "WARN"
    } else {
        Write-Log "=== タイム偏差計算 COMPLETE ==="
    }
}

# ============================================================
# 斤量補正計算（先週土日）Step 3①
# ============================================================
Write-Log "=== 斤量補正計算 START ==="

$futanDevScript = Join-Path $root "scripts\calc_futan_correction.py"
if (-not (Test-Path $futanDevScript)) {
    Write-Log "scripts\calc_futan_correction.py が見つかりません - スキップ" "WARN"
} else {
    $today   = Get-Date
    $dowInt  = [int]$today.DayOfWeek
    $daysToLastSat = if ($dowInt -ge 1) { $dowInt + 1 } else { 7 }
    $lastSat = $today.AddDays(-$daysToLastSat)
    $lastSun = $lastSat.AddDays(1)

    $raceDates = @(
        $lastSat.ToString("yyyyMMdd"),
        $lastSun.ToString("yyyyMMdd")
    )
    Write-Log "[FUTAN_DEV] 対象日: $($raceDates -join ', ')"

    $futanDevFailed = $false

    foreach ($raceDate in $raceDates) {
        Write-Log "[FUTAN_DEV] $raceDate 計算開始"

        $fdArgs = @(
            $futanDevScript,
            "--date",        $raceDate,
            "--pg-host",     $env:POSTGRES_HOST,
            "--pg-port",     ([string]$env:POSTGRES_PORT),
            "--pg-database", $env:POSTGRES_DATABASE,
            "--pg-user",     $env:POSTGRES_USER,
            "--pg-password", $pgPassword
        )

        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"

        & py "-3.12-32" @fdArgs 2>&1 | ForEach-Object {
            $line = "[{0}] [FUTAN_DEV] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
            Write-Host $line
            [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
        }
        $fdExitCode = $LASTEXITCODE

        $ErrorActionPreference = $prevEAP

        if ($fdExitCode -eq 0) {
            Write-Log "[FUTAN_DEV] $raceDate 計算完了"
        } else {
            Write-Log "[FUTAN_DEV] $raceDate 計算失敗 (exit: $fdExitCode)" "WARN"
            $futanDevFailed = $true
        }
    }

    if ($futanDevFailed) {
        Write-Log "=== 斤量補正計算 一部失敗 - メイン同期は正常完了 ===" "WARN"
    } else {
        Write-Log "=== 斤量補正計算 COMPLETE ==="
    }
}

# ============================================================
# 個馬補正計算（先週土日）Step 3②
# ============================================================
Write-Log "=== 個馬補正計算 START ==="

$disadvDevScript = Join-Path $root "scripts\calc_disadv_correction.py"
if (-not (Test-Path $disadvDevScript)) {
    Write-Log "scripts\calc_disadv_correction.py が見つかりません - スキップ" "WARN"
} else {
    $today   = Get-Date
    $dowInt  = [int]$today.DayOfWeek
    $daysToLastSat = if ($dowInt -ge 1) { $dowInt + 1 } else { 7 }
    $lastSat = $today.AddDays(-$daysToLastSat)
    $lastSun = $lastSat.AddDays(1)

    $raceDates = @(
        $lastSat.ToString("yyyyMMdd"),
        $lastSun.ToString("yyyyMMdd")
    )
    Write-Log "[DISADV_DEV] 対象日: $($raceDates -join ', ')"

    $disadvDevFailed = $false

    foreach ($raceDate in $raceDates) {
        Write-Log "[DISADV_DEV] $raceDate 計算開始"

        $ddArgs = @(
            $disadvDevScript,
            "--date",        $raceDate,
            "--pg-host",     $env:POSTGRES_HOST,
            "--pg-port",     ([string]$env:POSTGRES_PORT),
            "--pg-database", $env:POSTGRES_DATABASE,
            "--pg-user",     $env:POSTGRES_USER,
            "--pg-password", $pgPassword
        )

        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"

        & py "-3.12-32" @ddArgs 2>&1 | ForEach-Object {
            $line = "[{0}] [DISADV_DEV] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
            Write-Host $line
            [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
        }
        $ddExitCode = $LASTEXITCODE

        $ErrorActionPreference = $prevEAP

        if ($ddExitCode -eq 0) {
            Write-Log "[DISADV_DEV] $raceDate 計算完了"
        } else {
            Write-Log "[DISADV_DEV] $raceDate 計算失敗 (exit: $ddExitCode)" "WARN"
            $disadvDevFailed = $true
        }
    }

    if ($disadvDevFailed) {
        Write-Log "=== 個馬補正計算 一部失敗 - メイン同期は正常完了 ===" "WARN"
    } else {
        Write-Log "=== 個馬補正計算 COMPLETE ==="
    }
}

# ============================================================
# 展開補正計算（先週土日）Step 3③
# ============================================================
Write-Log "=== 展開補正計算 START ==="

$paceDevScript = Join-Path $root "scripts\calc_pace_correction.py"
if (-not (Test-Path $paceDevScript)) {
    Write-Log "scripts\calc_pace_correction.py が見つかりません - スキップ" "WARN"
} else {
    $today   = Get-Date
    $dowInt  = [int]$today.DayOfWeek
    $daysToLastSat = if ($dowInt -ge 1) { $dowInt + 1 } else { 7 }
    $lastSat = $today.AddDays(-$daysToLastSat)
    $lastSun = $lastSat.AddDays(1)

    $raceDates = @(
        $lastSat.ToString("yyyyMMdd"),
        $lastSun.ToString("yyyyMMdd")
    )
    Write-Log "[PACE_DEV] 対象日: $($raceDates -join ', ')"

    $paceDevFailed = $false

    foreach ($raceDate in $raceDates) {
        Write-Log "[PACE_DEV] $raceDate 計算開始"

        $pdArgs = @(
            $paceDevScript,
            "--date",        $raceDate,
            "--pg-host",     $env:POSTGRES_HOST,
            "--pg-port",     ([string]$env:POSTGRES_PORT),
            "--pg-database", $env:POSTGRES_DATABASE,
            "--pg-user",     $env:POSTGRES_USER,
            "--pg-password", $pgPassword
        )

        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"

        & py "-3.12-32" @pdArgs 2>&1 | ForEach-Object {
            $line = "[{0}] [PACE_DEV] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
            Write-Host $line
            [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
        }
        $pdExitCode = $LASTEXITCODE

        $ErrorActionPreference = $prevEAP

        if ($pdExitCode -eq 0) {
            Write-Log "[PACE_DEV] $raceDate 計算完了"
        } else {
            Write-Log "[PACE_DEV] $raceDate 計算失敗 (exit: $pdExitCode)" "WARN"
            $paceDevFailed = $true
        }
    }

    if ($paceDevFailed) {
        Write-Log "=== 展開補正計算 一部失敗 - メイン同期は正常完了 ===" "WARN"
    } else {
        Write-Log "=== 展開補正計算 COMPLETE ==="
    }
}

# ============================================================
# バイアス補正計算（先週土日）Step 3④
# ============================================================
Write-Log "=== バイアス補正計算 START ==="

$biasDevScript = Join-Path $root "scripts\calc_bias_correction.py"
if (-not (Test-Path $biasDevScript)) {
    Write-Log "scripts\calc_bias_correction.py が見つかりません - スキップ" "WARN"
} else {
    $today   = Get-Date
    $dowInt  = [int]$today.DayOfWeek
    $daysToLastSat = if ($dowInt -ge 1) { $dowInt + 1 } else { 7 }
    $lastSat = $today.AddDays(-$daysToLastSat)
    $lastSun = $lastSat.AddDays(1)

    $raceDates = @(
        $lastSat.ToString("yyyyMMdd"),
        $lastSun.ToString("yyyyMMdd")
    )
    Write-Log "[BIAS_DEV] 対象日: $($raceDates -join ', ')"

    $biasDevFailed = $false

    foreach ($raceDate in $raceDates) {
        Write-Log "[BIAS_DEV] $raceDate 計算開始"

        $bdArgs = @(
            $biasDevScript,
            "--date",        $raceDate,
            "--pg-host",     $env:POSTGRES_HOST,
            "--pg-port",     ([string]$env:POSTGRES_PORT),
            "--pg-database", $env:POSTGRES_DATABASE,
            "--pg-user",     $env:POSTGRES_USER,
            "--pg-password", $pgPassword
        )

        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"

        & py "-3.12-32" @bdArgs 2>&1 | ForEach-Object {
            $line = "[{0}] [BIAS_DEV] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
            Write-Host $line
            [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
        }
        $bdExitCode = $LASTEXITCODE

        $ErrorActionPreference = $prevEAP

        if ($bdExitCode -eq 0) {
            Write-Log "[BIAS_DEV] $raceDate 計算完了"
        } else {
            Write-Log "[BIAS_DEV] $raceDate 計算失敗 (exit: $bdExitCode)" "WARN"
            $biasDevFailed = $true
        }
    }

    if ($biasDevFailed) {
        Write-Log "=== バイアス補正計算 一部失敗 - メイン同期は正常完了 ===" "WARN"
    } else {
        Write-Log "=== バイアス補正計算 COMPLETE ==="
    }
}

# ============================================================
# 馬場指数計算（先週土日）Step 1
# ============================================================
Write-Log "=== 馬場指数計算 START ==="

$trackSpeedScript = Join-Path $root "scripts\calc_track_speed.py"
if (-not (Test-Path $trackSpeedScript)) {
    Write-Log "scripts\calc_track_speed.py が見つかりません - スキップ" "WARN"
} else {
    # 直近の土日を計算（木曜実行基準: 土=-5日, 日=-4日）
    $today   = Get-Date
    $dowInt  = [int]$today.DayOfWeek   # 0=Sun … 6=Sat
    # 直近の土曜日（当日が土曜なら先週土曜）
    $daysToLastSat = if ($dowInt -ge 1) { $dowInt + 1 } else { 7 }
    $lastSat = $today.AddDays(-$daysToLastSat)
    $lastSun = $lastSat.AddDays(1)

    $raceDates = @(
        $lastSat.ToString("yyyyMMdd"),
        $lastSun.ToString("yyyyMMdd")
    )
    Write-Log "[TRACK_SPEED] 対象日: $($raceDates -join ', ')"

    $trackSpeedFailed = $false

    foreach ($raceDate in $raceDates) {
        Write-Log "[TRACK_SPEED] $raceDate 計算開始"

        $tsArgs = @(
            $trackSpeedScript,
            "--date",        $raceDate,
            "--pg-host",     $env:POSTGRES_HOST,
            "--pg-port",     ([string]$env:POSTGRES_PORT),
            "--pg-database", $env:POSTGRES_DATABASE,
            "--pg-user",     $env:POSTGRES_USER,
            "--pg-password", $pgPassword
        )

        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"

        & py "-3.12-32" @tsArgs 2>&1 | ForEach-Object {
            $line = "[{0}] [TRACK_SPEED] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
            Write-Host $line
            [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
        }
        $tsExitCode = $LASTEXITCODE

        $ErrorActionPreference = $prevEAP

        if ($tsExitCode -eq 0) {
            Write-Log "[TRACK_SPEED] $raceDate 計算完了"
        } else {
            Write-Log "[TRACK_SPEED] $raceDate 計算失敗 (exit: $tsExitCode)" "WARN"
            $trackSpeedFailed = $true
        }
    }

    if ($trackSpeedFailed) {
        Write-Log "=== 馬場指数計算 一部失敗 - メイン同期は正常完了 ===" "WARN"
    } else {
        Write-Log "=== 馬場指数計算 COMPLETE ==="
    }
}

# ============================================================
# パフォーマンス指数計算（先週土日）Step 4
# 依存: time_dev / futan_dev / disadv_dev / pace_dev / bias_dev / track_index
# 必ず全補正計算・馬場指数計算の後に実行すること
# ============================================================
Write-Log "=== パフォーマンス指数計算 START ==="

$perfIdxScript = Join-Path $root "scripts\calc_performance_index.py"
if (-not (Test-Path $perfIdxScript)) {
    Write-Log "scripts\calc_performance_index.py が見つかりません - スキップ" "WARN"
} else {
    $today   = Get-Date
    $dowInt  = [int]$today.DayOfWeek
    $daysToLastSat = if ($dowInt -ge 1) { $dowInt + 1 } else { 7 }
    $lastSat = $today.AddDays(-$daysToLastSat)
    $lastSun = $lastSat.AddDays(1)

    $raceDates = @(
        $lastSat.ToString("yyyyMMdd"),
        $lastSun.ToString("yyyyMMdd")
    )
    Write-Log "[PERF_IDX] 対象日: $($raceDates -join ', ')"

    $perfIdxFailed = $false

    foreach ($raceDate in $raceDates) {
        Write-Log "[PERF_IDX] $raceDate 計算開始"

        $piArgs = @(
            $perfIdxScript,
            "--date",        $raceDate,
            "--pg-host",     $env:POSTGRES_HOST,
            "--pg-port",     ([string]$env:POSTGRES_PORT),
            "--pg-database", $env:POSTGRES_DATABASE,
            "--pg-user",     $env:POSTGRES_USER,
            "--pg-password", $pgPassword
        )

        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"

        & py "-3.12-32" @piArgs 2>&1 | ForEach-Object {
            $line = "[{0}] [PERF_IDX] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
            Write-Host $line
            [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
        }
        $piExitCode = $LASTEXITCODE

        $ErrorActionPreference = $prevEAP

        if ($piExitCode -eq 0) {
            Write-Log "[PERF_IDX] $raceDate 計算完了"
        } else {
            Write-Log "[PERF_IDX] $raceDate 計算失敗 (exit: $piExitCode)" "WARN"
            $perfIdxFailed = $true
        }
    }

    if ($perfIdxFailed) {
        Write-Log "=== パフォーマンス指数計算 一部失敗 - メイン同期は正常完了 ===" "WARN"
    } else {
        Write-Log "=== パフォーマンス指数計算 COMPLETE ==="
    }
}

# ============================================================
# 馬別実力指数計算（直近3年一括）Step 5
# 依存: nl_performance.perf_index（Step4完了後に実行すること）
# 日付指定なし: 常に直近3年全データを再計算して nl_horse_index を UPSERT
# ============================================================
Write-Log "=== 馬別実力指数計算 START ==="

$horseIdxScript = Join-Path $root "scripts\calc_horse_index.py"
if (-not (Test-Path $horseIdxScript)) {
    Write-Log "scripts\calc_horse_index.py が見つかりません - スキップ" "WARN"
} else {
    $hiArgs = @(
        $horseIdxScript,
        "--pg-host",     $env:POSTGRES_HOST,
        "--pg-port",     ([string]$env:POSTGRES_PORT),
        "--pg-database", $env:POSTGRES_DATABASE,
        "--pg-user",     $env:POSTGRES_USER,
        "--pg-password", $pgPassword
    )

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    & py "-3.12-32" @hiArgs 2>&1 | ForEach-Object {
        $line = "[{0}] [HORSE_IDX] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
        Write-Host $line
        [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
    }
    $hiExitCode = $LASTEXITCODE

    $ErrorActionPreference = $prevEAP

    if ($hiExitCode -eq 0) {
        Write-Log "=== 馬別実力指数計算 COMPLETE ==="
    } else {
        Write-Log "=== 馬別実力指数計算 FAILED (exit: $hiExitCode) - メイン同期は正常完了 ===" "WARN"
    }
}

# ============================================================
# 時系列補正計算（全データ一括）Step 6
# 依存: nl_horse_index（Step5完了後に実行すること）
# 日付指定なし: nl_horse_index の全エントリを対象に current_index を更新
# ============================================================
Write-Log "=== 時系列補正計算 START ==="

$currentIdxScript = Join-Path $root "scripts\calc_current_index.py"
if (-not (Test-Path $currentIdxScript)) {
    Write-Log "scripts\calc_current_index.py が見つかりません - スキップ" "WARN"
} else {
    $ciArgs = @(
        $currentIdxScript,
        "--pg-host",     $env:POSTGRES_HOST,
        "--pg-port",     ([string]$env:POSTGRES_PORT),
        "--pg-database", $env:POSTGRES_DATABASE,
        "--pg-user",     $env:POSTGRES_USER,
        "--pg-password", $pgPassword
    )

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    & py "-3.12-32" @ciArgs 2>&1 | ForEach-Object {
        $line = "[{0}] [CURRENT_IDX] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_
        Write-Host $line
        [System.IO.File]::AppendAllText($logFile, "$line`n", $utf8NoBom)
    }
    $ciExitCode = $LASTEXITCODE

    $ErrorActionPreference = $prevEAP

    if ($ciExitCode -eq 0) {
        Write-Log "=== 時系列補正計算 COMPLETE ==="
    } else {
        Write-Log "=== 時系列補正計算 FAILED (exit: $ciExitCode) - メイン同期は正常完了 ===" "WARN"
    }
}
