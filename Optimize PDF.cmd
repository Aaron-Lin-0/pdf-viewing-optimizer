@echo off
setlocal
if not defined PDF_PYTHON set "PDF_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PDF_PYTHON%" set "PDF_PYTHON=python"
if "%~1"=="" (
    echo Drag one PDF onto this file to create a verified optimized copy beside it.
    pause
    exit /b 1
)
"%PDF_PYTHON%" "%~dp0optimize_pdf.py" %*
set "PDF_EXIT=%ERRORLEVEL%"
pause
exit /b %PDF_EXIT%
