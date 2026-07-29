# 2 worker MEF coordinati (mutex ricerca globale anti-Akamai).
# Consigliato: NON 4 insieme — Akamai banna lo stesso IP.
#
#   S1  gen-giu  porta 9222  .edge_profile_mef_s1
#   S1B gen-giu  porte 9224  .edge_profile_mef_s1b  (altre materie)
# oppure S1 + S2A su semestri diversi.

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Remove-Item -Force -ErrorAction SilentlyContinue `
    mef_search_global.lock, mef_akamai_cooldown.json, `
    mef_download_s1.lock, mef_download_s1b.lock, mef_download_s2.lock

if (-not (Test-Path ".edge_profile_mef_s1b") -and (Test-Path ".edge_profile_mef_s1")) {
    Copy-Item -Recurse -Force ".edge_profile_mef_s1" ".edge_profile_mef_s1b"
}

Write-Host "Avvio S1 (D040 / prime materie)..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", @"
cd '$root'
python download_mef_2025.py --year 2025 --semestre 1 --profile-dir .edge_profile_mef_s1 --cdp-port 9222 --resume
"@

Write-Host "Attendo 45s (Akamai + slot ricerca) prima del secondo worker..."
Start-Sleep -Seconds 45

Write-Host "Avvio S1B (altre materie stesso semestre)..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", @"
cd '$root'
python download_mef_2025.py --year 2025 --semestre 1 --worker b --profile-dir .edge_profile_mef_s1b --cdp-port 9224 --resume
"@

Write-Host ""
Write-Host "Nei log vedrai: 'Attendo slot ricerca' / 'Slot ricerca OK' / 'Cooldown Akamai globale'."
Write-Host "Max 2 worker. Per S2 aspetta che uno dei due sia stabile."
