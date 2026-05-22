#Requires -Version 5.1
<#
.SYNOPSIS
    prediction_app を起動し ngrok でスマホからアクセス可能にする
.DESCRIPTION
    1. uvicorn で prediction_app を port 8001 で起動
    2. ngrok で HTTP トンネルを作成
    3. スマホアクセス用の公開 URL をコンソールに表示
    Ctrl+C でこのウィンドウを閉じると uvicorn と ngrok も停止します
.NOTES
    ngrok 認証済みであること（ngrok authtoken <TOKEN> で設定）
    https://dashboard.ngrok.com/ でトークンを取得できます
    ANTHROPIC_API_KEY が設定されていれば AI 分析も利用可能
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = 8001

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  競馬予想ツール  prediction_app + ngrok 起動" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# ── PostgreSQL パスワード ──────────────────────────────────────────────────────
$pgPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'User')
if ([string]::IsNullOrEmpty($pgPassword)) { $pgPassword = $env:PGPASSWORD }
if ([string]::IsNullOrEmpty($pgPassword)) {
    Write-Host "[ERROR] PGPASSWORD が設定されていません" -ForegroundColor Red
    Write-Host "        register_weekly_friday_task.ps1 を実行して設定してください"
    exit 1
}

$env:POSTGRES_HOST     = "localhost"
$env:POSTGRES_PORT     = "5432"
$env:POSTGRES_DATABASE = "keiba"
$env:POSTGRES_USER     = "postgres"
$env:POSTGRES_PASSWORD = $pgPassword
$env:PYTHONIOENCODING  = "utf-8"

Set-Location $root

# ── ngrok の認証確認 ───────────────────────────────────────────────────────────
$ngrokCmd = Get-Command ngrok -ErrorAction SilentlyContinue
if (-not $ngrokCmd) {
    Write-Host "[ERROR] ngrok が見つかりません。winget install ngrok でインストールしてください" -ForegroundColor Red
    exit 1
}

$ngrokCfg = "$env:USERPROFILE\AppData\Local\ngrok\ngrok.yml"
if (-not (Test-Path $ngrokCfg) -or -not (Select-String "authtoken" $ngrokCfg -Quiet)) {
    Write-Host "[ERROR] ngrok の認証トークンが設定されていません" -ForegroundColor Red
    Write-Host "        https://dashboard.ngrok.com/ でトークンを取得し"
    Write-Host "        「ngrok authtoken <YOUR_TOKEN>」を実行してください"
    exit 1
}

# ── ポートの使用確認（既存プロセスの停止） ────────────────────────────────────
$occupied = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($occupied) {
    Write-Host "[INFO] ポート $port は既に使用中です。既存プロセスを確認します..." -ForegroundColor Yellow
    $pid_ = ($occupied | Select-Object -First 1).OwningProcess
    $proc = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "       PID $pid_ ($($proc.ProcessName)) を停止します"
        Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
        Start-Sleep 1
    }
}

# ── uvicorn 起動 ───────────────────────────────────────────────────────────────
Write-Host "[1/3] uvicorn 起動中 (port $port)..." -ForegroundColor Yellow

$uvicornArgs = @(
    "-3.12-32", "-m", "uvicorn",
    "prediction_app.main:app",
    "--host", "127.0.0.1",
    "--port", "$port"
)
$uvicornProc = Start-Process "py" -ArgumentList $uvicornArgs -PassThru -WindowStyle Minimized

# uvicorn の起動を待つ（最大20秒）
$uvicornReady = $false
for ($i = 1; $i -le 20; $i++) {
    Start-Sleep 1
    try {
        $null = Invoke-WebRequest "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
        $uvicornReady = $true
        break
    } catch { }
}

if ($uvicornReady) {
    Write-Host "  uvicorn 起動完了 (PID: $($uvicornProc.Id))" -ForegroundColor Green
} else {
    Write-Host "  [WARN] uvicorn の応答確認がタイムアウト — 起動を続行します" -ForegroundColor Yellow
}

# ── ngrok トンネル作成 ────────────────────────────────────────────────────────
Write-Host "[2/3] ngrok トンネル作成中..." -ForegroundColor Yellow

# 既存 ngrok プロセスを停止してクリーンな状態で起動
Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 1

$ngrokProc = Start-Process "ngrok" -ArgumentList @("http", "$port") -PassThru -WindowStyle Minimized

# ngrok Web API からトンネル URL を取得（最大20秒）
$ngrokUrl = $null
for ($i = 1; $i -le 20; $i++) {
    Start-Sleep 1
    try {
        $tunnels = Invoke-RestMethod "http://127.0.0.1:4040/api/tunnels" -ErrorAction Stop
        $https = $tunnels.tunnels | Where-Object { $_.proto -eq "https" } | Select-Object -First 1
        if ($https) { $ngrokUrl = $https.public_url; break }
    } catch { }
}

# ── 公開 URL 表示 ─────────────────────────────────────────────────────────────
Write-Host ""
if ($ngrokUrl) {
    Write-Host "[3/3] 公開 URL 取得完了" -ForegroundColor Green
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "  ║  スマホからアクセス（外部公開 URL）:                ║" -ForegroundColor Cyan
    Write-Host "  ║" -ForegroundColor Cyan -NoNewline
    Write-Host ("  " + $ngrokUrl.PadRight(51)) -ForegroundColor Green -NoNewline
    Write-Host "║" -ForegroundColor Cyan
    Write-Host "  ║                                                      ║" -ForegroundColor Cyan
    Write-Host "  ║  ローカル: http://127.0.0.1:$port                     ║" -ForegroundColor Cyan
    Write-Host "  ╚══════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  QRコード・詳細: http://127.0.0.1:4040 (ngrok 管理画面)" -ForegroundColor Gray
} else {
    Write-Host "[WARN] ngrok URL の自動取得に失敗しました" -ForegroundColor Yellow
    Write-Host "       ブラウザで http://127.0.0.1:4040 を開いて URL を確認してください"
}

Write-Host ""
Write-Host "  実行中... Ctrl+C でサーバーを停止します" -ForegroundColor Gray
Write-Host ""

# ── 実行中 / 終了待ち ─────────────────────────────────────────────────────────
try {
    while ($true) {
        Start-Sleep 10
        if ($uvicornProc.HasExited) {
            Write-Host "[WARN] uvicorn が予期せず終了しました (exit: $($uvicornProc.ExitCode))" -ForegroundColor Yellow
            break
        }
        if ($ngrokProc.HasExited) {
            Write-Host "[WARN] ngrok が予期せず終了しました (exit: $($ngrokProc.ExitCode))" -ForegroundColor Yellow
            break
        }
    }
} finally {
    Write-Host ""
    Write-Host "シャットダウン中..." -ForegroundColor Yellow
    if (-not $uvicornProc.HasExited) {
        Stop-Process -Id $uvicornProc.Id -Force -ErrorAction SilentlyContinue
        Write-Host "  uvicorn 停止 (PID: $($uvicornProc.Id))" -ForegroundColor Gray
    }
    if (-not $ngrokProc.HasExited) {
        Stop-Process -Id $ngrokProc.Id  -Force -ErrorAction SilentlyContinue
        Write-Host "  ngrok 停止  (PID: $($ngrokProc.Id))" -ForegroundColor Gray
    }
    Write-Host "停止完了" -ForegroundColor Green
    Write-Host ""
}
