@echo off
setlocal
cd /d "%~dp0"
title NPAAAD-SAFDZ CLUP Harmonizer - Installer

echo ================================================
echo  NPAAAD-SAFDZ CLUP HARMONIZER - FIRST SETUP
echo ================================================
echo.

where py >nul 2>nul
if %errorlevel% neq 0 (
  echo ERROR: Python Launcher was not found.
  echo Install 64-bit Python 3.11, 3.12, 3.13, or 3.14 and enable the Python launcher.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  py -m venv .venv
  if %errorlevel% neq 0 goto :fail
)

call .venv\Scripts\activate.bat

echo Updating pip...
python -m pip install --upgrade pip
if %errorlevel% neq 0 goto :fail

echo.
echo Installing required GIS packages. This can take a few minutes...
python -m pip install -r requirements.txt
if %errorlevel% neq 0 goto :fail

echo.
echo Checking the application...
python -c "import flask,pandas,geopandas,shapely,pyogrio,openpyxl; import app; print('Application check: OK')"
if %errorlevel% neq 0 goto :fail

echo.
echo Creating desktop shortcut...
call CREATE_DESKTOP_SHORTCUT.bat

echo.
echo Setup complete. Starting website...
python launcher.py
exit /b %errorlevel%

:fail
echo.
echo ================================================
echo  SETUP FAILED
 echo ================================================
echo Copy or screenshot the error shown above and send it to ChatGPT.
pause
exit /b 1
