@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call INSTALL_AND_RUN.bat
  exit /b
)
.venv\Scripts\python.exe launcher.py
