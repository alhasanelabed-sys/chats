@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  set HADER_BOOTSTRAP_ONLY=1
  call start.bat
  set HADER_BOOTSTRAP_ONLY=
)
".venv\Scripts\python.exe" tools\bootstrap.py || (pause & exit /b 1)
set /p IP=Device IP (e.g. 10.28.65.253): 
".venv\Scripts\python.exe" tools\device_check.py %IP% %*
pause
