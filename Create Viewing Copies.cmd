@echo off
setlocal
if not defined PDF_PYTHON if exist "%~dp0.venv\Scripts\python.exe" set "PDF_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined PDF_PYTHON set "PDF_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PDF_PYTHON%" set "PDF_PYTHON=python"
"%PDF_PYTHON%" "%~dp0viewing_copy_gui.py"
if errorlevel 1 pause
