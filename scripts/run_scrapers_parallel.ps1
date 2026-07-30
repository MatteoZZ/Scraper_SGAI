# Avvia EUR-Lex + Curia + ADM in parallelo (cartelle separate).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if ((Split-Path -Leaf $Root) -eq "scripts") {
    $Root = Split-Path -Parent $Root
}
Set-Location $Root
Write-Host "Root: $Root"

$jobs = @(
    @{ Name = "eurlex"; Args = @("-m", "scripts.scraper_eurlex", "run") },
    @{ Name = "curia";  Args = @("-m", "scripts.scraper_curia", "run") },
    @{ Name = "adm";    Args = @("-m", "scripts.scraper_adm", "run", "--all-years") }
)

foreach ($j in $jobs) {
    $log = Join-Path $Root "scripts\_run_$($j.Name).log"
    Write-Host "START $($j.Name) -> $log"
    Start-Process -FilePath "python" -ArgumentList $j.Args `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $log `
        -RedirectStandardError "$log.err" `
        -WindowStyle Minimized
}

Write-Host ""
Write-Host "Italgiure (se non gia' attivo): python -m scripts.scraper_italgiure run"
Write-Host "Massimario dopo: python -m scripts.scraper_massimario run"
Write-Host "EBTI per ultima: python -m scripts.scraper_ebti run"
