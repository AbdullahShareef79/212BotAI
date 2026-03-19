@echo off
start "StockBot AI" powershell -NoExit -Command "cd 'C:\Users\EwaAbdullah\StockBot'; .\venv311\Scripts\Activate.ps1; python main.py"
start "TBot" powershell -NoExit -Command "cd 'C:\Users\shr\Documents\Python Personal\TBot'; .\.venv\Scripts\Activate.ps1; python main.py --dry-run"
start "Funding Rate Bot" powershell -NoExit -Command "cd 'C:\Users\shr\Documents\Python Personal\ArbBot'; .\venv\Scripts\Activate.ps1; python bot.py"
