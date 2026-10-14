@echo off
setlocal
if not defined PDF_PYTHON set "PDF_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PDF_PYTHON%" set "PDF_PYTHON=python"
if "%~1"=="" (
    echo Drag one BOOX PDF onto this file. Vector coordinates are preserved; edge shading may change.
    pause
    exit /b 1
)
"%PDF_PYTHON%" "%~dp0optimize_pdf.py" "%~1" "%~dpn1.fast-vector.pdf" --fast-handwriting
set "PDF_EXIT=%ERRORLEVEL%"
pause
exit /b %PDF_EXIT%
