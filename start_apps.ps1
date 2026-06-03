#Requires -Version 5.1
<#
.SYNOPSIS
    review_app (8000) + prediction_app (8001) を起動し ngrok で外部公開する
.DESCRIPTION
    1. review_app    を uvicorn ポート 8000 で起動
    2. prediction_app を uvicorn ポート 8001 で起動
    3. ngrok で両ポートをトンネルし公開 URL をコンソールに表示
    Ctrl+C で両 uvicorn と ngrok を停止します。
.NOTES
    ngrok 認証済みであること: ngrok authtoken <TOKEN>
    トークン取得: https://dashboard.ngrok.com/
#>

$ErrorActionPreference = "Stop"
$root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$portRev = 8000
$portPred = 8001

Write-Host ""
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "  review_app + prediction_app + ngrok launcher" -ForegroundColor Cyan
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host ""

# ── PostgreSQL 接続情報 ──────────────────────────────────────────────────────
$pgPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'User')
if ([string]::IsNullOrEmpty($pgPassword)) { $pgPassword = $env:PGPASSWORD }
if ([string]::IsNullOrEmpty($pgPassword)) {
    Write-Host "[ERROR] PGPASSWORD が設定されていません。" -ForegroundColor Red
    Write-Host "        register_weekly_friday_task.ps1 を実行して設定してください。"
    exit 1
}

$env:POSTGRES_HOST     = "localhost"
$env:POSTGRES_PORT     = "5432"
$env:POSTGRES_DATABASE = "keiba"
$env:POSTGRES_USER     = "postgres"
$env:POSTGRES_PASSWORD = $pgPassword
$env:PYTHONIOENCODING  = "utf-8"

Set-Location $root

# ── ngrok 確認 ──────────────────────────────────────────────────────────────
$ngrokCmd = Get-Command ngrok -ErrorAction SilentlyContinue
if (-not $ngrokCmd) {
    Write-Host "[ERROR] ngrok が見つかりません。" -ForegroundColor Red
    Write-Host "        winget install ngrok  を実行してインストールしてください。"
    exit 1
}

$ngrokCfg = "$env:USERPROFILE\AppData\Local\ngrok\ngrok.yml"
if (-not (Test-Path $ngrokCfg) -or -not (Select-String "authtoken" $ngrokCfg -Quiet)) {
    Write-Host "[ERROR] ngrok の authtoken が設定されていません。" -ForegroundColor Red
    Write-Host "        ngrok authtoken <YOUR_TOKEN>  を実行してください。"
    Write-Host "        トークン: https://dashboard.ngrok.com/"
    exit 1
}

# ── ポート競合解消 ───────────────────────────────────────────────────────────
foreach ($port in @($portRev, $portPred)) {
    $occ = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($occ) {
        $pid_ = ($occ | Select-Object -First 1).OwningProcess
        $proc = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "[INFO] ポート $port を使用中 (PID $pid_ $($proc.ProcessName)) -- 停止します" -ForegroundColor Yellow
            Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
            Start-Sleep 1
        }
    }
}

# ── 既存の ngrok を停止 ─────────────────────────────────────────────────────
Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 1

# ── review_app 起動 ─────────────────────────────────────────────────────────
Write-Host "[1/4] review_app をポート $portRev で起動中 ..." -ForegroundColor Yellow
$revArgs = @(
    "-3.12-32", "-m", "uvicorn",
    "review_app.main:app",
    "--host", "127.0.0.1",
    "--port", "$portRev"
)
$revProc = Start-Process "py" -ArgumentList $revArgs -PassThru -WindowStyle Minimized

# ── prediction_app 起動 ─────────────────────────────────────────────────────
Write-Host "[2/4] prediction_app をポート $portPred で起動中 ..." -ForegroundColor Yellow
$predArgs = @(
    "-3.12-32", "-m", "uvicorn",
    "prediction_app.main:app",
    "--host", "127.0.0.1",
    "--port", "$portPred"
)
$predProc = Start-Process "py" -ArgumentList $predArgs -PassThru -WindowStyle Minimized

# ── 両アプリの起動待ち ───────────────────────────────────────────────────────
Write-Host "  アプリの起動を待機中..." -ForegroundColor Gray
$ready = @{ $portRev = $false; $portPred = $false }
for ($i = 1; $i -le 25; $i++) {
    Start-Sleep 1
    foreach ($p in @($portRev, $portPred)) {
        if (-not $ready[$p]) {
            try {
                $null = Invoke-WebRequest "http://127.0.0.1:$p/" -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
                $ready[$p] = $true
                $label = if ($p -eq $portRev) { "review_app" } else { "prediction_app" }
                Write-Host "  $label ポート $p 起動完了" -ForegroundColor Green
            } catch { }
        }
    }
    if ($ready[$portRev] -and $ready[$portPred]) { break }
}
if (-not $ready[$portRev])  { Write-Host "  [WARN] review_app の起動確認タイムアウト -- 続行します"  -ForegroundColor Yellow }
if (-not $ready[$portPred]) { Write-Host "  [WARN] prediction_app の起動確認タイムアウト -- 続行します" -ForegroundColor Yellow }

# ── ngrok マルチトンネル設定 ─────────────────────────────────────────────────
Write-Host "[3/4] ngrok トンネルを作成中 ..." -ForegroundColor Yellow

$ngrokTmpCfg = "$env:TEMP\ngrok_keiba_tunnels.yml"
@"
tunnels:
  review:
    proto: http
    addr: $portRev
  prediction:
    proto: http
    addr: $portPred
"@ | Set-Content $ngrokTmpCfg -Encoding utf8

$ngrokArgs = @(
    "start", "review", "prediction",
    "--config", $ngrokCfg,
    "--config", $ngrokTmpCfg
)
$ngrokProc = Start-Process "ngrok" -ArgumentList $ngrokArgs -PassThru -WindowStyle Minimized

# ── トンネル URL 取得 ────────────────────────────────────────────────────────
$urlRev  = $null
$urlPred = $null
for ($i = 1; $i -le 25; $i++) {
    Start-Sleep 1
    try {
        $tunnels = Invoke-RestMethod "http://127.0.0.1:4040/api/tunnels" -ErrorAction Stop
        foreach ($t in $tunnels.tunnels) {
            if ($t.proto -ne "https") { continue }
            if ($t.config.addr -match ":$portRev$")  { $urlRev  = $t.public_url }
            if ($t.config.addr -match ":$portPred$") { $urlPred = $t.public_url }
        }
        if ($urlRev -and $urlPred) { break }
    } catch { }
}

# ── 公開 URL 表示 ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[4/4] トンネル準備完了" -ForegroundColor Green
Write-Host ""
Write-Host "  +------------------------------------------------------------+" -ForegroundColor Cyan
Write-Host "  |  回顧アプリ (review_app):                                  |" -ForegroundColor Cyan
$u1 = if ($urlRev)  { $urlRev  } else { "取得失敗 -- http://127.0.0.1:4040 を確認" }
Write-Host "  |    $u1" -ForegroundColor Green
Write-Host "  |                                                            |" -ForegroundColor Cyan
Write-Host "  |  予想アプリ (prediction_app):                              |" -ForegroundColor Cyan
$u2 = if ($urlPred) { $urlPred } else { "取得失敗 -- http://127.0.0.1:4040 を確認" }
Write-Host "  |    $u2" -ForegroundColor Green
Write-Host "  +------------------------------------------------------------+" -ForegroundColor Cyan
Write-Host ""
Write-Host "  ngrok ダッシュボード (QR コードあり): http://127.0.0.1:4040" -ForegroundColor Gray
Write-Host ""
Write-Host "  Running -- Ctrl+C で停止" -ForegroundColor Gray
Write-Host ""

# ── 監視ループ / 終了時クリーンアップ ───────────────────────────────────────
try {
    while ($true) {
        Start-Sleep 10
        if ($revProc.HasExited) {
            Write-Host "[WARN] review_app が予期せず終了しました (code: $($revProc.ExitCode))" -ForegroundColor Yellow
            break
        }
        if ($predProc.HasExited) {
            Write-Host "[WARN] prediction_app が予期せず終了しました (code: $($predProc.ExitCode))" -ForegroundColor Yellow
            break
        }
        if ($ngrokProc.HasExited) {
            Write-Host "[WARN] ngrok が予期せず終了しました (code: $($ngrokProc.ExitCode))" -ForegroundColor Yellow
            break
        }
    }
} finally {
    Write-Host ""
    Write-Host "シャットダウン中 ..." -ForegroundColor Yellow
    foreach ($p in @($revProc, $predProc, $ngrokProc)) {
        if ($p -and -not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
    if (Test-Path $ngrokTmpCfg) { Remove-Item $ngrokTmpCfg -ErrorAction SilentlyContinue }
    Write-Host "Done." -ForegroundColor Green
    Write-Host ""
}
