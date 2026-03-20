"""Add / remove StockBot from Windows Startup (Task Scheduler or shell:startup).

Usage:
    python setup_autostart.py install     Add to Windows startup
    python setup_autostart.py uninstall   Remove from Windows startup
"""

from __future__ import annotations

import os
import sys
import pathlib
import argparse


_TASK_NAME   = "StockBotAI"
_PROJECT_DIR = pathlib.Path(__file__).resolve().parent
_PYTHON      = sys.executable
_BAT         = str(_PROJECT_DIR / "start_all_bots.bat")


def _get_startup_folder() -> pathlib.Path:
    """Return the shell:startup folder path."""
    import winreg
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
    )
    startup_path, _ = winreg.QueryValueEx(key, "Startup")
    winreg.CloseKey(key)
    return pathlib.Path(startup_path)


def _vbs_content() -> str:
    """VBScript that runs start_all_bots.bat silently (no cmd window flash)."""
    return (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.Run "cmd /c ""{_BAT}""", 0, False\n'
    )


def install_startup() -> None:
    """Place a .vbs launcher in the Windows Startup folder."""
    startup  = _get_startup_folder()
    vbs_path = startup / f"{_TASK_NAME}.vbs"
    vbs_path.write_text(_vbs_content(), encoding="utf-8")
    print(f"✅ Installed: {vbs_path}")
    print(f"   StockBot will auto-start on login.")
    print(f"   Launches: {_BAT}")


def uninstall_startup() -> None:
    """Remove the .vbs launcher from the Startup folder."""
    startup  = _get_startup_folder()
    vbs_path = startup / f"{_TASK_NAME}.vbs"
    if vbs_path.exists():
        vbs_path.unlink()
        print(f"✅ Removed: {vbs_path}")
    else:
        print("ℹ  No startup entry found.")


def install_task_scheduler() -> None:
    """Alternative: use Windows Task Scheduler (runs even without login)."""
    import subprocess
    cmd = [
        "schtasks", "/create",
        "/tn", _TASK_NAME,
        "/tr", f'cmd /c "{_BAT}"',
        "/sc", "onlogon",
        "/rl", "limited",
        "/f",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"✅ Task Scheduler entry created: {_TASK_NAME}")
    else:
        print(f"❌ Failed: {result.stderr}")
        print("   Try running as Administrator.")


def uninstall_task_scheduler() -> None:
    import subprocess
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", _TASK_NAME, "/f"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"✅ Task Scheduler entry removed: {_TASK_NAME}")
    else:
        print(f"ℹ  {result.stderr.strip()}")


def main() -> None:
    p = argparse.ArgumentParser(description="Manage StockBot Windows autostart")
    p.add_argument("action", choices=["install", "uninstall"], help="install or uninstall autostart")
    p.add_argument("--method", choices=["startup", "taskscheduler"], default="startup",
                   help="Use Startup folder (default) or Task Scheduler")
    args = p.parse_args()

    if sys.platform != "win32":
        print("⚠  This script is for Windows only.")
        print("   On Linux/macOS, use a systemd service or crontab instead.")
        sys.exit(1)

    if args.action == "install":
        if args.method == "taskscheduler":
            install_task_scheduler()
        else:
            install_startup()
    else:
        if args.method == "taskscheduler":
            uninstall_task_scheduler()
        else:
            uninstall_startup()


if __name__ == "__main__":
    main()
