@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0CREATE_DESKTOP_SHORTCUT.ps1"
if errorlevel 1 (
  echo.
  echo Failed to create the desktop shortcut.
  pause
  exit /b 1
)
timeout /t 2 /nobreak >nul
exit /b 0
