@echo off
rem Run as administrator. Shows which program holds port 90 (usually the previous attendance server),
rem and after confirmation stops it and sets its services to Manual so it does not come back
rem after a restart. Hader then takes port 90 by itself within 10 seconds.
net session >nul 2>&1 || (echo Please right-click this file and choose "Run as administrator". & pause & exit /b 1)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$c = Get-NetTCPConnection -LocalPort 90 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1;" ^
  "if (-not $c) { Write-Host 'Port 90 is free.'; exit 0 }" ^
  "$p = Get-Process -Id $c.OwningProcess; Write-Host ('Port 90 is held by: ' + $p.ProcessName + ' (PID ' + $p.Id + ') ' + $p.Path);" ^
  "$svc = Get-CimInstance Win32_Service | Where-Object { $_.ProcessId -eq $p.Id -or ($_.PathName -and $p.Path -and $_.PathName -like ('*' + (Split-Path $p.Path -Parent) + '*')) };" ^
  "if ($svc) { Write-Host 'Services:'; $svc | ForEach-Object { Write-Host ('  ' + $_.Name + '  -  ' + $_.DisplayName) } }" ^
  "$a = Read-Host 'Stop them and keep them stopped after restart? (y/n)'; if ($a -ne 'y') { exit 0 }" ^
  "foreach ($s in $svc) { Stop-Service -Name $s.Name -Force -ErrorAction SilentlyContinue; Set-Service -Name $s.Name -StartupType Manual -ErrorAction SilentlyContinue }" ^
  "Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; Start-Sleep 2;" ^
  "if (Get-NetTCPConnection -LocalPort 90 -State Listen -ErrorAction SilentlyContinue) { Write-Host 'Port 90 is still in use.' } else { Write-Host 'Port 90 is free. Hader takes it within 10 seconds.' }"
pause
