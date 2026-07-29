@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo  INIT PROFILI EDGE MEF (anti-Akamai)
echo ============================================================
echo.
echo  Chiudi TUTTE le finestre Edge prima di iniziare.
echo.
echo  Per ogni profilo:
echo    1) Si apre Edge
echo    2) Nel browser: anno 2025, date qualsiasi, materia Irpef, Ricerca
echo    3) Se vedi la tabella (NO Access Denied), torna qui e premi INVIO
echo    4) Chiudi Edge prima del profilo successivo
echo.
pause

if exist ".edge_profile_mef" (
    echo.
    echo  Trovato profilo base: .edge_profile_mef
    echo.
    choice /C CI /N /M "  [C] Copia base in s1+s2+q1  oppure  [I] Init completo uno per uno? "
    if errorlevel 2 goto init_s1
    if errorlevel 1 goto copia_tutti
)

:copia_tutti
echo.
echo  Copia profilo base in s1, s2, q1...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Copy-Item -Recurse -Force '.edge_profile_mef' '.edge_profile_mef_s1'; ^
   Copy-Item -Recurse -Force '.edge_profile_mef' '.edge_profile_mef_s2'; ^
   Copy-Item -Recurse -Force '.edge_profile_mef' '.edge_profile_mef_q1'"
echo  Fatto. Profili pronti:
echo    .edge_profile_mef_s1  porta 9222  (S1 gen-giu)
echo    .edge_profile_mef_s2  porta 9223  (S2 lug-dic)
echo    .edge_profile_mef_q1  porta 9224  (retry Q1)
echo.
echo  Ora: avvia_workers_semestre.bat
pause
exit /b 0

:init_s1
echo.
echo ============================================================
echo  INIT 1/3 — S1 gen-giu  (.edge_profile_mef_s1  porta 9222)
echo ============================================================
python download_mef_2025.py --init-profilo --profile-dir .edge_profile_mef_s1 --cdp-port 9222
if errorlevel 1 goto errore

echo.
echo  Chiudi Edge, poi premi un tasto per S2...
pause >nul

:init_s2
echo.
echo ============================================================
echo  INIT 2/3 — S2 lug-dic  (.edge_profile_mef_s2  porta 9223)
echo ============================================================
python download_mef_2025.py --init-profilo --profile-dir .edge_profile_mef_s2 --cdp-port 9223
if errorlevel 1 goto errore

echo.
echo  Chiudi Edge, poi premi un tasto per Q1 retry...
pause >nul

:init_q1
echo.
echo ============================================================
echo  INIT 3/3 — Q1 retry  (.edge_profile_mef_q1  porta 9224)
echo ============================================================
python download_mef_2025.py --init-profilo --profile-dir .edge_profile_mef_q1 --cdp-port 9224
if errorlevel 1 goto errore

echo.
echo ============================================================
echo  TUTTI I PROFILI INIZIALIZZATI
echo ============================================================
echo    S1: .edge_profile_mef_s1  porta 9222
echo    S2: .edge_profile_mef_s2  porta 9223
echo    Q1: .edge_profile_mef_q1  porta 9224
echo.
echo  Ora: avvia_workers_semestre.bat
pause
exit /b 0

:errore
echo.
echo  Init fallito o interrotto. Chiudi Edge e riprova.
pause
exit /b 1
