@echo off
setlocal
if not defined PDF_PYTHON set "PDF_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PDF_PYTHON%" set "PDF_PYTHON=python"
if exist "%~dp0..\work\pdf-tools\pymupdf" set "PYTHONPATH=%~dp0..\work\pdf-tools;%PYTHONPATH%"
if "%~1"=="" (
    echo Drag one PDF onto this launcher to create a measured vector viewing copy.
    pause
    exit /b 1
)
"%PDF_PYTHON%" "%~dp0optimize_pdf.py" "%~1" "%~dpn1.viewing.pdf" --viewing-copy
set "PDF_EXIT=%ERRORLEVEL%"
pause
exit /b %PDF_EXIT%
