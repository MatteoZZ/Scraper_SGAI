# 2 worker MEF in parallelo — un semestre ciascuno (gen-giu / lug-dic).
#
# PRIMA (init profili Edge — una tantum):
#   .\init_profili_mef.bat
#
# RETRY pagine vuote (terminale separato, porta 9224+):
#   python genera_pagine_retry.py
#   poi lancia i comandi in mef_pagine_retry_cmds.ps1
#
# FERMARE: Ctrl+C nei 2 terminali, chiudi Edge, poi:
#   Remove-Item -Force -ErrorAction SilentlyContinue mef_download_s1.lock, mef_download_s2.lock

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "Rigenero elenco pagine da ritentare..."
python genera_pagine_retry.py

$jobs = @(
    @{ s = 1; port = 9222; prof = ".edge_profile_mef_s1"; label = "gen-giu" },
    @{ s = 2; port = 9223; prof = ".edge_profile_mef_s2"; label = "lug-dic" }
)

foreach ($j in $jobs) {
    $cmd = "python download_mef_2025.py --year 2025 --semestre $($j.s) --profile-dir $($j.prof) --cdp-port $($j.port) --resume"
    Write-Host "Avvio S$($j.s) ($($j.label)): $cmd"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; $cmd"
    Start-Sleep -Seconds 8
}

Write-Host ""
Write-Host "2 worker avviati. Checkpoint: mef_download_checkpoint_s1.json / s2.json"
Write-Host "Retry pagine vuote: vedi mef_pagine_retry_cmds.ps1 (profilo q1 su porta 9224, ecc.)"
