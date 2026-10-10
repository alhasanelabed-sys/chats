@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Uses the same server and data as start.bat; opens a separate app window.
call start.bat --desktop %*
