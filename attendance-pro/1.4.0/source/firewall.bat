@echo off
rem Run as administrator: opens the program's current ports (web, employee portal, terminals)
rem in Windows Firewall. The program also does this by itself when it runs as administrator.
cd /d "%~dp0"
".venv\Scripts\python.exe" -c "from hader import runtime; runtime.firewall(); from hader.config import settings as s; print('Opened TCP ports:', s.web_port, s.portal_port, *s.adms_ports)"
rem Direct link / discovery: answers from the terminals on port 4370 (TCP and UDP)
netsh advfirewall firewall delete rule name="Hader 4370" >nul 2>&1
netsh advfirewall firewall add rule name="Hader 4370" dir=in action=allow protocol=UDP remoteport=4370
pause
