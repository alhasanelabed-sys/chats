@echo off
rem Hader Service Manager: start/stop the server, set its address and ports, start-up with Windows, backups.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run start.bat once first - it installs what the program needs.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m hader.manager
