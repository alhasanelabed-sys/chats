@echo off
rem Update Hader from the command line (the Service Manager has the same thing on its Updates tab).
rem   update.bat            download and install the newest version
rem   update.bat file.zip   install a downloaded version (no internet needed)
cd /d "%~dp0"
if "%~1"=="" (".venv\Scripts\python.exe" -m hader.update --apply) else (".venv\Scripts\python.exe" -m hader.update --zip "%~1")
pause
