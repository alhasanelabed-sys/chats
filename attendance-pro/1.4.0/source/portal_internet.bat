@echo off
rem Opens the EMPLOYEE PORTAL ONLY to the internet through a Cloudflare tunnel (HTTPS,
rem no router changes). Admin pages and terminals stay inside your network.
setlocal
cd /d "%~dp0"
rem Never publish an arbitrary port or fall back to the administration port.
if not "%~1"=="" (
  echo Custom port overrides are disabled. Configure the separate employee portal in Hader.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Start Hader with start.bat first, then run this file again.
  pause
  exit /b 1
)
set PORT=
set ADMIN_PORT=
for /f "tokens=1,2" %%p in ('".venv\Scripts\python.exe" -c "from hader.config import settings as s; print(s.portal_port, s.web_port)"') do (
  set PORT=%%p
  set ADMIN_PORT=%%q
)
if "%PORT%"=="" goto unsafe_port
if "%PORT%"=="0" goto unsafe_port
if "%PORT%"=="%ADMIN_PORT%" goto unsafe_port
set CF=%~dp0tools\cloudflared.exe

if not exist "%CF%" (
  echo Downloading cloudflared ^(Cloudflare tunnel client^)...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -UseBasicParsing 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile '%CF%'"
  if not exist "%CF%" ( echo Download failed. Check the internet connection. & pause & exit /b 1 )
)

echo.
echo   Employee portal on this PC: http://localhost:%PORT%
echo.
echo   1^) Temporary link now ^(for a test - changes every time, stops when this window closes^)
echo   2^) Permanent link on your domain ^(paste the tunnel token from Cloudflare; runs as a service^)
echo   3^) Remove the permanent link service
echo.
choice /c 123 /n /m "Choose 1, 2 or 3: "
if errorlevel 3 goto remove
if errorlevel 2 goto permanent

echo.
echo   Look for a line with https://....trycloudflare.com below - that is the link for employees.
echo   Keep this window open while it is in use.
echo.
"%CF%" tunnel --no-autoupdate --url http://localhost:%PORT%
goto end

:permanent
echo.
echo   In Cloudflare: Zero Trust ^> Networks ^> Tunnels ^> Create a tunnel ^(Cloudflared^),
echo   public hostname e.g. portal.yourdomain.com  -^>  service  http://localhost:%PORT%
echo   then copy the token shown in the install command.
echo.
set /p TOKEN=Paste the token here: 
if "%TOKEN%"=="" goto end
"%CF%" service install %TOKEN%
if errorlevel 1 ( echo Run this file as administrator for the permanent link. & pause & exit /b 1 )
echo.
echo   Done. Put https://portal.yourdomain.com in Hader: Personnel ^> Employee portal ^> "From outside".
pause
goto end

:remove
"%CF%" service uninstall
pause
goto end

:unsafe_port
echo The separate employee portal is disabled or shares the admin port.
echo Enable a separate employee portal port in System settings and restart Hader.
pause
exit /b 1

:end
endlocal
