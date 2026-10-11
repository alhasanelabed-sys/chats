@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Forgotten password: resets the admin password to admin (or: reset_password.bat USER NEWPASSWORD)
if not exist ".venv\Scripts\python.exe" (echo Run start.bat once first. & pause & exit /b 1)
".venv\Scripts\python.exe" tools\reset_password.py %*
pause
