# Profilo NUOVO (il vecchio .edge_profile_mef_s1 e' probabilmente bruciato da Akamai).
# Se anche QUI la ricerca manuale da 403 → non e' lo script: serve VPN / altra rete.
#
#   powershell -ExecutionPolicy Bypass -File .\RIPARTI_PROFILO_NUOVO.ps1

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$profName = ".edge_profile_mef_fresh"
$port = 9230

Write-Host ""
Write-Host "=== STOP python MEF + TUTTE le Edge del profilo vecchio/nuovo ===" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'download_mef' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

foreach ($p in @(".edge_profile_mef_s1", ".edge_profile_mef_s1b", $profName)) {
  if (-not (Test-Path $p)) { continue }
  $full = (Resolve-Path $p).Path
  Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$full*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

Remove-Item -Force -ErrorAction SilentlyContinue `
  mef_download_s1.lock, mef_search_global.lock, mef_akamai_cooldown.json

# Checkpoint: stesso file s1, riparti da 320
Write-Host "Checkpoint S1 resta su D040 pag.320 (non lo cancello)."
Start-Sleep -Seconds 3

Write-Host ""
Write-Host "=== INIT su profilo NUOVO: $profName  porta $port ===" -ForegroundColor Green
Write-Host "Nella Edge che si apre:"
Write-Host "  1) aspetta 30-60 secondi sulla pagina"
Write-Host "  2) ricerca 2025 / 01-01-06-30 / D040 Accertamento"
Write-Host "  3) se vedi TABELLA -> INVIO nel terminale"
Write-Host "  4) se 403 anche a mano QUI -> STOP, serve VPN"
Write-Host ""

python download_mef_2025.py --init-profilo --profile-dir $profName --cdp-port $port --warmup-seconds 20
if ($LASTEXITCODE -ne 0) {
  Write-Host ""
  Write-Host "INIT FALLITO. Se era 403 nella Edge dello script: cambia rete/VPN e ritenta questo file." -ForegroundColor Red
  exit $LASTEXITCODE
}

Write-Host ""
Write-Host "=== DOWNLOAD D040 da pag.320 sul profilo nuovo ===" -ForegroundColor Green
Write-Host "Se chiede INVIO: vai a pagina 320 in QUELL'Edge, poi INVIO."
Write-Host ""

# Copia checkpoint s1 verso un lock "s1" usando stesso --semestre 1 senza worker
python download_mef_2025.py `
  --year 2025 --semestre 1 --materia D040 `
  --start-pagina 320 `
  --profile-dir $profName --cdp-port $port `
  --resume --solo --no-auto-heal --page-delay 15
