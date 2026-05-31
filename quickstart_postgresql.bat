@echo off
setlocal enabledelayedexpansion
chcp 932 >nul 2>&1
set PYTHONIOENCODING=utf-8
title JLTSQL PostgreSQL Quickstart

REM Move to batch file directory
cd /d "%~dp0"

echo ============================================================
echo   JLTSQL PostgreSQL Quickstart
echo   Connecting: localhost:5432 / keiba / postgres
echo ============================================================
echo.

REM PostgreSQL
set POSTGRES_HOST=localhost
set POSTGRES_PORT=5432
set POSTGRES_DATABASE=keiba
set POSTGRES_USER=postgres

REM Password: use PGPASSWORD env var if set, otherwise prompt
if not defined PGPASSWORD (
    set /p "PGPASSWORD=PostgreSQL password (Enter for default 'postgres'): "
    if "!PGPASSWORD!"=="" set "PGPASSWORD=postgres"
)
set POSTGRES_PASSWORD=%PGPASSWORD%

REM Use 32-bit Python 3.12 (required for JV-Link 32-bit DLL)
py -3.12-32 --version >nul 2>&1
if !errorlevel! neq 0 (
    echo ERROR: py -3.12-32 not found.
    echo JV-Link requires 32-bit Python. Install Python 3.12 (32-bit) from:
    echo   https://www.python.org/downloads/
    echo Then select "Windows installer (32-bit)".
    pause
    exit /b 1
)

echo Using Python: py -3.12-32
echo.

REM Setup mode
echo Select setup mode:
echo   1. Simple   - RACE, DIFF (race results, odds, horse info)
echo   2. Standard - Simple + bloodline, training, schedule  [recommended]
echo   3. Full     - Standard + mining, training detail, commentary
echo.
set /p "MODE_CHOICE=Choice [1-3] (default: 2): "
if "!MODE_CHOICE!"=="1" (
    set "MODE=simple"
    set "MODE_NAME=Simple"
) else if "!MODE_CHOICE!"=="3" (
    set "MODE=full"
    set "MODE_NAME=Full"
) else (
    set "MODE=standard"
    set "MODE_NAME=Standard"
)

REM Date range
REM  YEARS_ARG = "--years N" for choices 1/2
REM  YEARS_ARG = ""          for choice 3 (all data; quickstart.py defaults to 1986-01-01)
echo.
echo Select date range:
echo   1. Last 1 year  [recommended]
echo   2. Last 5 years
echo   3. All data (since 1986, takes several hours)
echo.
set "YEARS_ARG=--years 1"
set /p "PERIOD_CHOICE=Choice [1-3] (default: 1): "
if "!PERIOD_CHOICE!"=="2" set "YEARS_ARG=--years 5"
if "!PERIOD_CHOICE!"=="3" set "YEARS_ARG="

echo.
echo ============================================================
echo   Confirm settings
echo   Mode    : !MODE_NAME!
echo   Database: PostgreSQL (postgres@localhost:5432/keiba)
echo ============================================================
echo.
set /p "CONFIRM=Proceed? [Y/n]: "
if /i "!CONFIRM!"=="n" (
    echo Cancelled.
    pause
    exit /b 0
)

echo.
echo ============================================================
echo   Running...
echo ============================================================
echo.

py -3.12-32 scripts/quickstart.py ^
    --db-type postgresql ^
    --pg-host localhost ^
    --pg-port 5432 ^
    --pg-database keiba ^
    --pg-user postgres ^
    --pg-password "!PGPASSWORD!" ^
    --mode !MODE! ^
    !YEARS_ARG! ^
    --yes

set SCRIPT_EXIT_CODE=!errorlevel!

echo.
if !SCRIPT_EXIT_CODE! neq 0 (
    echo ============================================================
    echo   Setup FAILED  (exit code: !SCRIPT_EXIT_CODE!)
    echo ============================================================
    echo.
    echo Common causes:
    echo   - JV-Link service key not configured
    echo     ^> Set it in JRA-VAN DataLab software
    echo   - PostgreSQL not running
    echo     ^> Start postgresql-x64-XX in services.msc
    echo   - Wrong password
    echo     ^> Re-run and enter the correct password
    echo   - pg8000 not installed
    echo     ^> Run: py -3.12-32 -m pip install pg8000
    echo.
    set /p dummy=Press Enter to close...
    endlocal & exit /b 1
)

echo ============================================================
echo   Setup Complete
echo ============================================================
echo.
echo   Database: PostgreSQL (postgres@localhost:5432/keiba)
echo.
echo   CLI commands:
echo     jltsql status                   - Check database status
echo     jltsql fetch                    - Fetch additional data
echo     daily_sync.bat --db postgresql  - Daily sync
echo.
set /p dummy=Press Enter to close...
endlocal
exit /b 0