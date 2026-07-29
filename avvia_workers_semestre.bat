@echo off
cd /d "%~dp0"
echo Se non hai ancora inizializzato i profili Edge: init_profili_mef.bat
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0avvia_workers_semestre.ps1"
pause
