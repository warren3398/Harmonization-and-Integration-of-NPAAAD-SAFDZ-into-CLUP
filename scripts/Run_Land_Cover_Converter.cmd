@echo off
setlocal
cd /d "%~dp0"
set "CONVERTER_ENV=%LOCALAPPDATA%\ALMED\LandCoverConverter"
if not exist "%CONVERTER_ENV%\Scripts\python.exe" (
    py -3 -m venv "%CONVERTER_ENV%"
    if errorlevel 1 goto error
)
"%CONVERTER_ENV%\Scripts\python.exe" -c "import geopandas,pyogrio,pyarrow" >nul 2>&1
if errorlevel 1 (
    "%CONVERTER_ENV%\Scripts\python.exe" -m pip install geopandas "pyogrio>=0.9" pyarrow
    if errorlevel 1 goto error
)
"%CONVERTER_ENV%\Scripts\python.exe" "%~dp0convert_namria_landcover.py" %*
if errorlevel 1 goto error
pause
exit /b 0
:error
echo Conversion did not complete. Read the error above.
pause
exit /b 1
