@echo off
echo Starting StockBot AI v3...

:: 1 — Dashboard API (Flask)
start "StockBot Dashboard API" powershell -NoExit -Command "cd 'C:\Users\EwaAbdullah\StockBot'; .\venv311\Scripts\Activate.ps1; python api.py"

:: 2 — Bot (scheduled mode)
start "StockBot AI v3" powershell -NoExit -Command "cd 'C:\Users\EwaAbdullah\StockBot'; .\venv311\Scripts\Activate.ps1; python main.py"

:: 3 — Wait 3 seconds for Flask to start, then open dashboard in Chrome
timeout /t 3 /nobreak > nul
start chrome "http://localhost:5000"
