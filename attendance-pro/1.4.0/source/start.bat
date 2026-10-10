@echo off
rem Hader - one click: installs Python and all packages when needed, then starts.
chcp 65001 >nul
title Hader
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto packages

echo [1/3] Looking for Python 3.10+ ...
set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=python"
if not defined PY if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY="%LocalAppData%\Programs\Python\Python312\python.exe""
if defined PY goto venv

echo      Python is not installed - downloading it from python.org (about 25 MB) ...
set "PYSETUP=%TEMP%\python-3.12.7-amd64.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile '%PYSETUP%'"
if not exist "%PYSETUP%" goto nopython
echo      Installing Python (this window stays open) ...
"%PYSETUP%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1
set "PY="%LocalAppData%\Programs\Python\Python312\python.exe""
if not exist "%LocalAppData%\Programs\Python\Python312\python.exe" goto nopython

:venv
echo [2/3] Creating the program's Python environment ...
%PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" goto nopython

:packages
".venv\Scripts\python.exe" tools\bootstrap.py
if errorlevel 1 goto failed
if defined HADER_BOOTSTRAP_ONLY exit /b 0

rem The program opens its own ports in Windows Firewall when started as administrator,
rem and opens the browser on whatever port is set in System settings.
echo [3/3] Starting Hader ...
".venv\Scripts\python.exe" run.py --open %*
pause
exit /b 0

:nopython
echo.
echo Could not install Python automatically (no internet?).
echo Install Python 3.12 from https://www.python.org/downloads/ (tick "Add python.exe to PATH") and run start.bat again.
pause
exit /b 1

:failed
echo.
echo Package installation failed - see the messages above.
pause
exit /b 1
