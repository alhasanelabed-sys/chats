@echo off
rem Run as administrator (after start.bat has run once): starts the server
rem automatically when Windows boots, even before anyone logs in.
cd /d "%~dp0"
schtasks /Create /F /TN "Hader" /SC ONSTART /RU SYSTEM /RL HIGHEST ^
  /TR "\"%~dp0.venv\Scripts\pythonw.exe\" \"%~dp0run.py\""
echo Scheduled. To remove: schtasks /Delete /TN "Hader" /F
pause
