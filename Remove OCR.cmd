@echo off
setlocal
if not defined PDF_PYTHON set "PDF_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PDF_PYTHON%" set "PDF_PYTHON=python"
if "%~1"=="" (
    echo Drag one BOOX PDF onto this file. Invisible OCR text is removed; search and copy are lost.
    pause
    exit /b 1
)
"%PDF_PYTHON%" "%~dp0optimize_pdf.py" "%~1" "%~dpn1.no-ocr.pdf" --remove-invisible-text
set "PDF_EXIT=%ERRORLEVEL%"
pause
exit /b %PDF_EXIT%
