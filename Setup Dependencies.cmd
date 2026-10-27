@echo off
setlocal
cd /d "%~dp0"
if not exist ".setup-temp" mkdir ".setup-temp"
set "TEMP=%~dp0.setup-temp"
set "TMP=%~dp0.setup-temp"

echo Setting up PDF Viewing Optimizer...
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 set "PDF_BOOTSTRAP=py -3"
if not defined PDF_BOOTSTRAP (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 set "PDF_BOOTSTRAP=python"
)
if not defined PDF_BOOTSTRAP goto :python_missing

if not exist "vendor\qpdf\bin\qpdf.exe" goto :qpdf_missing
"vendor\qpdf\bin\qpdf.exe" --version | findstr /c:"qpdf version 12.4.2" >nul
if errorlevel 1 goto :qpdf_missing

if not exist ".venv\Scripts\python.exe" (
    echo Creating the private Python environment...
    %PDF_BOOTSTRAP% -m venv .venv
    if errorlevel 1 goto :failed
)

echo Installing pinned Python packages...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

where node >nul 2>nul
if errorlevel 1 goto :node_missing
node -e "const [a,b]=process.versions.node.split('.').map(Number);process.exit(a>22||(a===22&&b>=13)?0:1)"
if errorlevel 1 (
    echo Node.js 22.13 or newer is required. Install it from https://nodejs.org/ and run this setup again.
    goto :failed
)
where npm >nul 2>nul
if errorlevel 1 goto :node_missing

echo Installing pinned PDF.js packages...
call npm ci
if errorlevel 1 goto :failed

echo.
echo Setup complete. Open Create Viewing Copies.cmd to begin.
if not exist "C:\Program Files\Zen Browser\zen.exe" echo Note: Install Zen or Firefox and set PDF_BROWSER if the browser check cannot find it.
pause
exit /b 0

:python_missing
echo Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ and run this setup again.
goto :failed

:node_missing
echo Node.js 22.13 or newer was not found. Install it from https://nodejs.org/ and run this setup again.
goto :failed

:qpdf_missing
echo Bundled qpdf 12.4.2 is missing or damaged. Extract a fresh release and run setup again.

:failed
echo.
echo Setup did not finish. Nothing in your PDFs was changed.
pause
exit /b 1
