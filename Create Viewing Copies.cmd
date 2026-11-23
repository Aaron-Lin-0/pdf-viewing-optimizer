@echo off
setlocal
if not defined PDF_PYTHON set "PDF_PYTHON=%~dp0.venv\Scripts\python.exe"
"%PDF_PYTHON%" -c "import sys, tkinter, pypdf; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
if errorlevel 1 (
    echo Setup is required before you can create viewing copies.
    echo Extract the entire ZIP, install Python 3.10 or newer and Node.js 22.13 or newer,
    echo then run Setup Dependencies.cmd in this folder and reopen Create Viewing Copies.cmd.
    pause
    exit /b 1
)
"%PDF_PYTHON%" "%~dp0viewing_copy_gui.py"
if errorlevel 1 pause
