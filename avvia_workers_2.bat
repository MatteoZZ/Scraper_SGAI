@echo off
cd /d "%~dp0"
echo 2 worker coordinati (mutex ricerca). Chiudi Edge/worker vecchi prima.
pause
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0avvia_workers_2.ps1"
pause
