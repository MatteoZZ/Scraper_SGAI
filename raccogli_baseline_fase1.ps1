<#
.SYNOPSIS
  Raccoglie metriche baseline Fase 1 punto 8 in sola lettura.
  Non esegue requeue, SQL di scrittura, reset o deploy.

.EXAMPLE
  # Solo SSH host metrics
  .\raccogli_baseline_fase1.ps1 -KeyPath "$HOME\.ssh\sgai-collega" -SshUser "TUO_UTENTE"

  # API documenti (dopo env admin sul server / sessione valida)
  $env:SGAI_ADMIN_EMAIL = "..."
  $env:SGAI_ADMIN_PASSWORD = "..."
  .\raccogli_baseline_fase1.ps1 -ApiBase "https://sgailegal.com" -SkipSsh

  # Velocita doc/min (attende 15 minuti tra due campioni API)
  .\raccogli_baseline_fase1.ps1 -ApiBase "https://sgailegal.com" -SkipSsh -MeasureSpeedMinutes 15
#>

[CmdletBinding()]
param(
  [string] $KeyPath = "",
  [string] $SshUser = "ubuntu",
  [string] $HostIp = "13.49.16.179",
  [string] $ApiBase = "https://sgailegal.com",
  [string] $Dataset = "SENTENZE BANCA DATI MEF",
  [switch] $SkipSsh,
  [switch] $SkipApi,
  [int] $MeasureSpeedMinutes = 0,
  [switch] $Wake
)

$ErrorActionPreference = "Stop"
$outDir = Join-Path $PSScriptRoot "baseline_out"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$report = Join-Path $outDir "baseline_$stamp.txt"

function Write-Report([string] $line) {
  $line | Tee-Object -FilePath $report -Append
}

Write-Report "=== BASELINE FASE 1 ==="
Write-Report ("capturedLocal: {0}" -f (Get-Date).ToString("o"))
Write-Report ("dataset: {0}" -f $Dataset)
Write-Report ""

if ($Wake) {
  $body = @{ force_start = $true; target_instance = "SGAI-Production" } | ConvertTo-Json -Compress
  Invoke-RestMethod `
    -Uri "https://91k2hfw1n3.execute-api.eu-north-1.amazonaws.com/wake-up" `
    -Method Post -ContentType "application/json" -Body $body -TimeoutSec 90 | Out-Host
  Write-Report "Wake inviato: attendere 2-6 minuti prima di SSH/API."
}

if (-not $SkipSsh) {
  if (-not $KeyPath) { throw "Specificare -KeyPath per SSH oppure usare -SkipSsh" }
  $resolvedKey = (Resolve-Path -LiteralPath $KeyPath).Path
  $target = "${SshUser}@${HostIp}"
  Write-Report "--- SSH read-only: $target ---"
  $cmd = @'
hostname; date; uptime; echo '--- free ---'; free -h; echo '--- df ---'; df -h; echo '--- docker ---'; docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; echo '--- git ---'; cd /home/ubuntu/workspace/ragflow && git status --short --branch && git log -5 --oneline
'@
  $sshOut = & ssh -o BatchMode=yes -o ConnectTimeout=25 -i $resolvedKey $target $cmd 2>&1
  Write-Report ($sshOut | Out-String)
}

function Get-KnowledgeStatus {
  param($Base, $DatasetName, $Session)
  $enc = [uri]::EscapeDataString($DatasetName)
  Invoke-RestMethod -Uri "$Base/v1/admin/knowledge-status?dataset=$enc" -WebSession $Session
}

if (-not $SkipApi) {
  if (-not $env:SGAI_ADMIN_EMAIL -or -not $env:SGAI_ADMIN_PASSWORD) {
    Write-Report "API saltata: impostare env SGAI_ADMIN_EMAIL e SGAI_ADMIN_PASSWORD nella sessione PowerShell (non in Git)."
  }
  else {
    Write-Report "--- API knowledge-status ---"
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $loginBody = @{
      username = $env:SGAI_ADMIN_EMAIL
      password = $env:SGAI_ADMIN_PASSWORD
    } | ConvertTo-Json
    $login = Invoke-RestMethod -Uri "$ApiBase/v1/admin/login" -Method Post `
      -ContentType "application/json" -Body $loginBody -WebSession $session
    Write-Report ("login.code={0} role={1}" -f $login.code, $login.data.role)

    $t0 = Get-KnowledgeStatus -Base $ApiBase -DatasetName $Dataset -Session $session
    Write-Report ("T0: {0}" -f ($t0.data | ConvertTo-Json -Compress -Depth 6))

    if ($MeasureSpeedMinutes -gt 0) {
      Write-Report ("Attendo {0} minuti per velocita doc/min..." -f $MeasureSpeedMinutes)
      Start-Sleep -Seconds ($MeasureSpeedMinutes * 60)
      $t1 = Get-KnowledgeStatus -Base $ApiBase -DatasetName $Dataset -Session $session
      Write-Report ("T1: {0}" -f ($t1.data | ConvertTo-Json -Compress -Depth 6))
      $done0 = [double]($t0.data.statusCounts.done)
      $done1 = [double]($t1.data.statusCounts.done)
      $rate = ($done1 - $done0) / [double]$MeasureSpeedMinutes
      Write-Report ("doc_per_min_done≈ {0}" -f $rate)
    }
  }
}

Write-Report ""
Write-Report "FINE. Nessuna modifica dati eseguita da questo script."
Write-Host "Report: $report" -ForegroundColor Green
