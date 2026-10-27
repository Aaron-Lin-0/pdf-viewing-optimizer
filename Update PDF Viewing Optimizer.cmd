@echo off
setlocal
cd /d "%~dp0"

if not exist ".git" goto :not_checkout
where git >nul 2>nul
if errorlevel 1 goto :git_missing

git diff --quiet
if errorlevel 1 goto :dirty
git diff --cached --quiet
if errorlevel 1 goto :dirty
for /f "delims=" %%I in ('git ls-files --others --exclude-standard') do goto :dirty

echo Checking for PDF Viewing Optimizer updates...
git pull --ff-only
if errorlevel 1 goto :failed

call "Setup Dependencies.cmd"
exit /b %ERRORLEVEL%

:dirty
echo Update stopped because this checkout has local changes.
echo Commit, stash, or otherwise review them before updating.
goto :failed

:not_checkout
echo This copy is not a Git checkout and cannot update itself.
echo Install a newer release over a separate folder instead.
goto :failed

:git_missing
echo Git is required for updates. Install Git for Windows, then try again.

:failed
echo.
echo Nothing was updated.
pause
exit /b 1
