@echo off
start "StockBot AI" powershell -NoExit -Command "cd 'C:\Users\EwaAbdullah\StockBot'; .\venv311\Scripts\Activate.ps1; python main.py"
start "TBot" powershell -NoExit -Command "cd 'C:\Users\EwaAbdullah\TBot'; .\.venv\Scripts\Activate.ps1; python main.py --dry-run"
start "Funding Rate Bot" powershell -NoExit -Command "cd 'C:\Users\EwaAbdullah\TBot\Arbot'; .\venv\Scripts\Activate.ps1; python bot.py"
