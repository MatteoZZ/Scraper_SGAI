# 4 worker MEF — SOLO se necessario. Preferisci avvia_workers_2.ps1
# Ora c'e' mutex globale search/submit + cooldown 403 (mef_search_global.lock).
# Anche cosi', 4 Edge sullo stesso IP restano rischiosi: avvio ogni 45s.
#
# PRIMA (una tantum): .\init_profili_mef_4.bat
# FERMARE: Ctrl+C, chiudi Edge, rimuovi *.lock e mef_akamai_cooldown.json

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# Copia profili se mancano (da s1/s2 gia inizializzati)
if (-not (Test-Path ".edge_profile_mef_s1b") -and (Test-Path ".edge_profile_mef_s1")) {
    Write-Host "Copia profilo S1 -> S1B..."
    Copy-Item -Recurse -Force ".edge_profile_mef_s1" ".edge_profile_mef_s1b"
}
if (-not (Test-Path ".edge_profile_mef_s2b") -and (Test-Path ".edge_profile_mef_s2")) {
    Write-Host "Copia profilo S2 -> S2B..."
    Copy-Item -Recurse -Force ".edge_profile_mef_s2" ".edge_profile_mef_s2b"
}

Remove-Item -Force -ErrorAction SilentlyContinue `
    mef_download_s1.lock, mef_download_s2.lock, `
    mef_download_s1a.lock, mef_download_s1b.lock, `
    mef_download_s2a.lock, mef_download_s2b.lock

$jobs = @(
    @{ tag = "S1A"; s = 1; w = "a"; port = 9222; prof = ".edge_profile_mef_s1" },
    @{ tag = "S2A"; s = 2; w = "a"; port = 9223; prof = ".edge_profile_mef_s2" },
    @{ tag = "S1B"; s = 1; w = "b"; port = 9224; prof = ".edge_profile_mef_s1b" },
    @{ tag = "S2B"; s = 2; w = "b"; port = 9225; prof = ".edge_profile_mef_s2b" }
)

foreach ($j in $jobs) {
    $cmd = "python download_mef_2025.py --year 2025 --semestre $($j.s) --worker $($j.w) --profile-dir $($j.prof) --cdp-port $($j.port) --resume"
    Write-Host "Avvio $($j.tag): $cmd"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; $cmd"
    Start-Sleep -Seconds 45
}

Write-Host ""
Write-Host "4 finestre avviate (pausa 45s). Mutex ricerca attivo."
Write-Host "Se vedi troppi 403, ferma 2 worker e usa solo avvia_workers_2.bat"
Write-Host "Monitor: python monitor_mef.py"
