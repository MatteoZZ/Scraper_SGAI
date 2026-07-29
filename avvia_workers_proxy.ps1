# Avvia N worker MEF con proxy diversi (da proxies.txt).
# Senza proxy residenziali NON usare questo script su stesso ADSL.
#
# Setup:
#   1. Copia proxies.example.txt -> proxies.txt e metti 1 proxy per riga
#   2. Opzionale: modifica $Jobs sotto (semestre/worker/materia)
#   3. .\avvia_workers_proxy.ps1
#
# Ogni worker: profilo Edge proprio, CDP port diversa, --proxy dedicato,
# ritmi 12-20s tra PDF, rotazione sessione ogni 25 DL.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$proxyFile = Join-Path $root "proxies.txt"
if (-not (Test-Path $proxyFile)) {
    Write-Host "Manca proxies.txt — copia da proxies.example.txt e compila."
    exit 1
}

$proxies = Get-Content $proxyFile |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith("#") }

if ($proxies.Count -lt 1) {
    Write-Host "proxies.txt vuoto."
    exit 1
}

# Piano default: fino a 4 worker (scala solo se i proxy sono residential e stabili)
$Jobs = @(
    @{ tag = "W01"; s = 1; w = "a"; materia = ""; startPag = 1 },
    @{ tag = "W02"; s = 1; w = "b"; materia = ""; startPag = 1 },
    @{ tag = "W03"; s = 2; w = "a"; materia = ""; startPag = 1 },
    @{ tag = "W04"; s = 2; w = "b"; materia = ""; startPag = 1 }
)

$n = [Math]::Min($Jobs.Count, $proxies.Count)
Write-Host "Avvio $n worker (proxy disponibili: $($proxies.Count))"
Write-Host "Sfalsamento 45s tra avvii. Ctrl+C ferma solo questa console, non i worker."
Write-Host ""

for ($i = 0; $i -lt $n; $i++) {
    $j = $Jobs[$i]
    $proxy = $proxies[$i]
    $port = 9230 + $i
    $prof = ".edge_profile_mef_$($j.tag.ToLower())"
    $proxyShown = if ($proxy -match "@") { ($proxy -split "@")[-1] } else { $proxy }

    $args = @(
        "download_mef_2025.py",
        "--year", "2025",
        "--semestre", "$($j.s)",
        "--worker", "$($j.w)",
        "--profile-dir", $prof,
        "--cdp-port", "$port",
        "--proxy", $proxy,
        "--page-delay", "15",
        "--download-delay-min", "12",
        "--download-delay-max", "20",
        "--session-rotate-every", "25",
        "--resume"
    )
    if ($j.materia) {
        $args += @("--materia", $j.materia)
    }
    if ($j.startPag -gt 1) {
        $args += @("--start-pagina", "$($j.startPag)")
    }

    # Proxy e path sempre quotati (user:pass@host)
    $quoted = for ($k = 0; $k -lt $args.Count; $k++) {
        $a = [string]$args[$k]
        if ($k -gt 0 -and $args[$k - 1] -in @("--proxy", "--profile-dir", "--materia")) {
            "`"$a`""
        } elseif ($a -match "[\s@:]") {
            "`"$a`""
        } else {
            $a
        }
    }
    $cmdLine = "cd `"$root`"; python " + ($quoted -join " ")

    Write-Host "[$($j.tag)] profilo=$prof CDP=$port proxy=$proxyShown"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmdLine
    Start-Sleep -Seconds 45
}

Write-Host ""
Write-Host "Worker avviati. Monitor: python monitor_mef.py"
Write-Host "Se 403 a raffica: chiudi le finestre, aspetta 10-30 min, riparti con meno worker."
