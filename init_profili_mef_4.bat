@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo  INIT 4 PROFILI EDGE (S1A S1B S2A S2B)
echo ============================================================
echo  Chiudi TUTTE le finestre Edge prima.
echo.
echo  Opzione rapida: se hai gia .edge_profile_mef_s1 e _s2,
echo  copia in s1b/s2b e init solo se serve.
echo.
pause

if exist ".edge_profile_mef_s1" if not exist ".edge_profile_mef_s1b" (
    echo Copia s1 -^> s1b...
    powershell -NoProfile -Command "Copy-Item -Recurse -Force '.edge_profile_mef_s1' '.edge_profile_mef_s1b'"
)
if exist ".edge_profile_mef_s2" if not exist ".edge_profile_mef_s2b" (
    echo Copia s2 -^> s2b...
    powershell -NoProfile -Command "Copy-Item -Recurse -Force '.edge_profile_mef_s2' '.edge_profile_mef_s2b'"
)

echo.
echo Init S1A (9222)...
python download_mef_2025.py --init-profilo --profile-dir .edge_profile_mef_s1 --cdp-port 9222
if errorlevel 1 goto err
echo Chiudi Edge, poi tasto per S2A...
pause >nul

echo Init S2A (9223)...
python download_mef_2025.py --init-profilo --profile-dir .edge_profile_mef_s2 --cdp-port 9223
if errorlevel 1 goto err
echo Chiudi Edge, poi tasto per S1B...
pause >nul

echo Init S1B (9224)...
python download_mef_2025.py --init-profilo --profile-dir .edge_profile_mef_s1b --cdp-port 9224
if errorlevel 1 goto err
echo Chiudi Edge, poi tasto per S2B...
pause >nul

echo Init S2B (9225)...
python download_mef_2025.py --init-profilo --profile-dir .edge_profile_mef_s2b --cdp-port 9225
if errorlevel 1 goto err

echo.
echo OK. Avvia: avvia_workers_4.bat
pause
exit /b 0

:err
echo Init fallito. Chiudi Edge e riprova.
pause
exit /b 1
