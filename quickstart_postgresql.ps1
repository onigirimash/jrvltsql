#Requires -Version 5.1
# quickstart_postgresql.ps1
# Setup JLTSQL with PostgreSQL backend using 32-bit Python (required for JV-Link).
#
# Run from PowerShell:
#   powershell -ExecutionPolicy Bypass -File quickstart_postgresql.ps1

Set-Location -Path $PSScriptRoot

Write-Host "============================================================"
Write-Host "  JLTSQL PostgreSQL Quickstart"
Write-Host "  Connecting: localhost:5432 / keiba / postgres"
Write-Host "============================================================"
Write-Host ""

# PostgreSQL connection settings (fixed)
$env:POSTGRES_HOST     = "localhost"
$env:POSTGRES_PORT     = "5432"
$env:POSTGRES_DATABASE = "keiba"
$env:POSTGRES_USER     = "postgres"

# Password: use existing PGPASSWORD env var or prompt
if ([string]::IsNullOrEmpty($env:PGPASSWORD)) {
    $pw = Read-Host "PostgreSQL password (Enter for default 'postgres')"
    if ([string]::IsNullOrEmpty($pw)) { $pw = "postgres" }
    $env:PGPASSWORD = $pw
}
$env:POSTGRES_PASSWORD = $env:PGPASSWORD

# Verify py -3.12-32 is available
try {
    $null = & py "-3.12-32" "--version" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "non-zero exit" }
} catch {
    Write-Host ""
    Write-Host "ERROR: py -3.12-32 not found."
    Write-Host "JV-Link requires 32-bit Python 3.12. Install from:"
    Write-Host "  https://www.python.org/downloads/"
    Write-Host "Select 'Windows installer (32-bit)'."
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "Using Python: py -3.12-32"
Write-Host ""

# --- Setup mode ---
Write-Host "Select setup mode:"
Write-Host "  1. Simple   - RACE, DIFF (race results, odds, horse info)"
Write-Host "  2. Standard - Simple + bloodline, training, schedule  [recommended]"
Write-Host "  3. Full     - Standard + mining, training detail, commentary"
Write-Host ""
$modeChoice = Read-Host "Choice [1-3] (default: 2)"
switch ($modeChoice) {
    "1"     { $mode = "simple";   $modeName = "Simple"   }
    "3"     { $mode = "full";     $modeName = "Full"     }
    default { $mode = "standard"; $modeName = "Standard" }
}

# --- Date range ---
Write-Host ""
Write-Host "Select date range:"
Write-Host "  1. Last 1 year  [recommended]"
Write-Host "  2. Last 5 years"
Write-Host "  3. All data (since 1986, takes several hours)"
Write-Host ""
$periodChoice = Read-Host "Choice [1-3] (default: 1)"

# Build period args as array.
# Choice 3: pass nothing -> quickstart.py defaults to 1986-01-01 internally.
$periodArgs = switch ($periodChoice) {
    "2"     { @("--years", "5") }
    "3"     { @()               }
    default { @("--years", "1") }
}

# --- Confirm ---
Write-Host ""
Write-Host "============================================================"
Write-Host "  Confirm settings"
Write-Host "  Mode    : $modeName"
Write-Host "  Database: PostgreSQL (postgres@localhost:5432/keiba)"
Write-Host "============================================================"
Write-Host ""
$confirm = Read-Host "Proceed? [Y/n]"
if ($confirm -ieq "n") {
    Write-Host "Cancelled."
    Read-Host "Press Enter to close"
    exit 0
}

Write-Host ""
Write-Host "============================================================"
Write-Host "  Running..."
Write-Host "============================================================"
Write-Host ""

# Build argument array. PowerShell passes each element as a separate argument,
# so no shell quoting or expansion issues occur.
$pyArgs = @(
    "scripts/quickstart.py",
    "--db-type",     "postgresql",
    "--pg-host",     "localhost",
    "--pg-port",     "5432",
    "--pg-database", "keiba",
    "--pg-user",     "postgres",
    "--pg-password", $env:PGPASSWORD,
    "--mode",        $mode
) + $periodArgs + @("--yes")

& py "-3.12-32" @pyArgs
$exitCode = $LASTEXITCODE

Write-Host ""
if ($exitCode -ne 0) {
    Write-Host "============================================================"
    Write-Host "  Setup FAILED  (exit code: $exitCode)"
    Write-Host "============================================================"
    Write-Host ""
    Write-Host "Common causes:"
    Write-Host "  - JV-Link service key not configured"
    Write-Host "    > Set it in JRA-VAN DataLab software"
    Write-Host "  - PostgreSQL not running"
    Write-Host "    > Start postgresql-x64-XX in services.msc"
    Write-Host "  - Wrong password"
    Write-Host "    > Re-run and enter the correct password"
    Write-Host "  - pg8000 not installed"
    Write-Host "    > Run: py -3.12-32 -m pip install pg8000"
    Write-Host ""
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "============================================================"
Write-Host "  Setup Complete"
Write-Host "============================================================"
Write-Host ""
Write-Host "  Database: PostgreSQL (postgres@localhost:5432/keiba)"
Write-Host ""
Write-Host "  CLI commands:"
Write-Host "    jltsql status                   - Check database status"
Write-Host "    jltsql fetch                    - Fetch additional data"
Write-Host "    daily_sync.bat --db postgresql  - Daily sync"
Write-Host ""
Read-Host "Press Enter to close"
