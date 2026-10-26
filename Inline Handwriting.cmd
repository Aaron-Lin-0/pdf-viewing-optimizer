@echo off
setlocal
if not defined PDF_PYTHON if exist "%~dp0.venv\Scripts\python.exe" set "PDF_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined PDF_PYTHON set "PDF_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PDF_PYTHON%" set "PDF_PYTHON=python"
if "%~1"=="" (
    echo Drag a supported BOOX vector PDF onto this file. Drawing geometry and clipping are preserved.
    pause
    exit /b 1
)
"%PDF_PYTHON%" "%~dp0optimize_pdf.py" "%~1" "%~dpn1.inlined.pdf" --inline-handwriting
set "PDF_EXIT=%ERRORLEVEL%"
pause
exit /b %PDF_EXIT%
