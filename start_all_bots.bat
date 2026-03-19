@echo off
REM ================================================================
REM  start_all_bots.bat  --  Launch StockBot, TBot & Funding Rate bot
REM  Each runs in its own PowerShell window with its own venv.
REM  Place a shortcut to this file in:
REM    shell:startup   (press Win+R, type shell:startup, Enter)
REM  to auto-run on Windows login.
REM ================================================================

title Bot Launcher
echo ================================================================
echo   Launching all trading bots...
echo ================================================================
echo.

REM -- 1. StockBot AI v3 (Trading 212 stock bot) --
echo [1/3] Starting StockBot AI...
start "StockBot AI" powershell -NoExit -Command ^
  "Set-Location 'C:\Users\EwaAbdullah\StockBot'; ^
   & '.\venv311\Scripts\Activate.ps1'; ^
   Write-Host '=== StockBot AI v3 ===' -ForegroundColor Cyan; ^
   python main.py"

REM -- small delay so windows don't fight for resources --
timeout /t 3 /nobreak >nul

REM -- 2. TBot (Telegram trading bot) --
echo [2/3] Starting TBot...
start "TBot" powershell -NoExit -Command ^
  "Set-Location 'C:\Users\EwaAbdullah\TBot'; ^
   & '.\.venv\Scripts\Activate.ps1'; ^
   Write-Host '=== TBot ===' -ForegroundColor Green; ^
   python main.py"

timeout /t 3 /nobreak >nul

REM -- 3. Arbot / Funding Rate bot --
echo [3/3] Starting Funding Rate Bot...
start "Funding Rate Bot" powershell -NoExit -Command ^
  "Set-Location 'C:\Users\EwaAbdullah\TBot\Arbot'; ^
   & '.\venv\Scripts\Activate.ps1'; ^
   Write-Host '=== Funding Rate Bot ===' -ForegroundColor Yellow; ^
   python bot.py"

echo.
echo ================================================================
echo   All 3 bots launched in separate windows.
echo   This window will close in 5 seconds.
echo ================================================================
timeout /t 5 /nobreak >nul
exit
