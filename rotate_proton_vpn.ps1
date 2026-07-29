# Rotazione Proton VPN Windows (MEF).
# Strategia che funziona anche SENZA Admin (visto in test):
#   stop client → tenta stop servizio ProtonVPN → IP cade / cambia → riavvia client
# Disable-NetAdapter ProTUN richiede Admin (opzionale).

param(
    [string]$OldIp = "",
    [int]$TimeoutSec = 90
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

function Get-PublicIp {
    foreach ($url in @("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com")) {
        try {
            $ip = (Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10).Content.Trim()
            if ($ip -and $ip.Length -lt 64 -and $ip -notmatch "\s") { return $ip }
        } catch {}
    }
    return ""
}

function Find-ClientExe {
    $p = "C:\Program Files\Proton\VPN\v5.1.5\ProtonVPN.Client.exe"
    if (Test-Path $p) { return $p }
    return (
        Get-ChildItem "C:\Program Files\Proton\VPN" -Recurse -Filter "ProtonVPN.Client.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty FullName
    )
}

function Wait-IpChange([string]$before, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    $last = $before
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        $cur = Get-PublicIp
        if ($cur) {
            $last = $cur
            Write-Host ("Proton: IP={0}" -f $cur)
            if ($before -and $cur -ne $before) { return $cur }
        }
    }
    return $last
}

if (-not $OldIp) { $OldIp = Get-PublicIp }
Write-Host ("Proton: IP prima: {0}" -f $OldIp)

# 1) Chiudi GUI (+ processi correlati)
Write-Host 'Proton: stop ProtonVPN.Client'
Get-Process -Name "ProtonVPN.Client","ProtonVPN.Service","ProtonVPN" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

# 2) Prova a stoppare il servizio (anche se "fallisce" spesso stacca il tunnel)
Write-Host 'Proton: stop servizio ProtonVPN Service (best-effort)'
try {
    Stop-Service -Name "ProtonVPN Service" -Force -ErrorAction Stop
    Write-Host 'Proton: servizio stoppato'
} catch {
    Write-Host ("Proton: Stop-Service messaggio: {0}" -f $_.Exception.Message)
}
# Resta disconnesso piu' a lungo: aumenta chance di uscire su un altro server
Write-Host 'Proton: attesa disconnesso (15s) prima di riconnettere...'
Start-Sleep -Seconds 15

# 3) Opzionale: Disable ProTUN se Admin
$adapters = Get-NetAdapter -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "ProTUN|Proton" -or $_.InterfaceDescription -match "Proton" }
foreach ($a in $adapters) {
    try {
        Disable-NetAdapter -Name $a.Name -Confirm:$false -ErrorAction Stop
        Write-Host ("Proton: adapter {0} DOWN" -f $a.Name)
    } catch {
        # ignore
    }
}

$mid = Get-PublicIp
Write-Host ("Proton: IP a meta rotazione: {0}" -f $mid)

# 4) Riavvia servizio + client
try {
    Start-Service -Name "ProtonVPN Service" -ErrorAction SilentlyContinue
} catch {}
foreach ($a in $adapters) {
    try { Enable-NetAdapter -Name $a.Name -Confirm:$false -ErrorAction SilentlyContinue } catch {}
}

$exe = Find-ClientExe
if ($exe) {
    Write-Host ("Proton: start {0}" -f $exe)
    Start-Process -FilePath $exe
} else {
    Write-Host 'Proton: apri il client a mano'
}
Write-Host 'Proton: attendo nuovo tunnel / Auto-connect...'
Start-Sleep -Seconds 18

$newIp = Wait-IpChange -before $OldIp -seconds ([Math]::Max(20, $TimeoutSec - 40))

if ($OldIp -and $newIp -and $newIp -ne $OldIp) {
    Write-Host ("Proton: OK IP cambiato: {0} -> {1}" -f $OldIp, $newIp)
    exit 0
}

Write-Host ("Proton: FAIL IP invariato ({0})" -f $newIp)
Write-Host 'Proton: fai a mano: Disconnect → 10s → Quick Connect (altro paese se possibile)'
Write-Host ("Proton: poi: python mef_vpn.py status  # diverso da {0}" -f $OldIp)
exit 3
