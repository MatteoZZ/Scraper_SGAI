# RIPARTENZA SICURA MEF D040 — un solo worker, niente auto-heal
# Uso: tasto destro → Esegui con PowerShell  OPPURE:
#   powershell -ExecutionPolicy Bypass -File .\RIPARTI_SICURO.ps1

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host ""
Write-Host "=== 1) Chiudo script MEF e Edge del profilo s1 ===" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -match 'download_mef' } |
  ForEach-Object { Write-Host "  stop python PID $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$prof = (Resolve-Path ".edge_profile_mef_s1").Path
Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -like "*$prof*" } |
  ForEach-Object { Write-Host "  stop Edge PID $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Remove-Item -Force -ErrorAction SilentlyContinue `
  mef_download_s1.lock, mef_search_global.lock, mef_akamai_cooldown.json

Start-Sleep -Seconds 2
Write-Host ""
Write-Host "=== 2) INIT profilo (OBBLIGATORIO se stamattina avevi 403 da terminale) ===" -ForegroundColor Cyan
Write-Host "Si apre Edge. Fai UNA ricerca a mano:"
Write-Host "  anno 2025 | date 01-01 / 06-30 | materia Accertamento imposte (D040) | Ricerca"
Write-Host "Quando vedi la TABELLA (no Access Denied), torna qui e premi INVIO."
Write-Host ""

python download_mef_2025.py --init-profilo --profile-dir .edge_profile_mef_s1 --cdp-port 9222 --warmup-seconds 10
if ($LASTEXITCODE -ne 0) {
  Write-Host "Init fallito o interrotto. Riprova." -ForegroundColor Red
  exit $LASTEXITCODE
}

Write-Host ""
Write-Host "=== 3) DOWNLOAD da pagina 320 ===" -ForegroundColor Cyan
Write-Host "Se chiede di nuovo INVIO: in QUELL'Edge vai a pagina 320, poi INVIO."
Write-Host ""

python download_mef_2025.py `
  --year 2025 --semestre 1 --materia D040 `
  --start-pagina 320 `
  --profile-dir .edge_profile_mef_s1 --cdp-port 9222 `
  --resume --solo --no-auto-heal --page-delay 15
