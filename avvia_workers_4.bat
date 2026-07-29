@echo off
cd /d "%~dp0"
echo ============================================================
echo  4 WORKER MEF — 2 per semestre (materie a meta')
echo ============================================================
echo  Chiudi Edge e i vecchi worker prima di continuare.
echo  Profili: se mancano s1b/s2b verranno copiati da s1/s2.
echo.
pause
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0avvia_workers_4.ps1"
pause
