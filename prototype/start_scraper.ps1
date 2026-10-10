# Starts the official Track 2 scraper engine and reports readiness.
#
# Requires Docker Desktop to be installed and running. The engine is the
# gosom google-maps-scraper behind the Mahanaicoach/google-maps-scraper-kit
# REST wrapper, which modules/discovery.py::scraper_search already speaks.
#
#   .\start_scraper.ps1              # clone, build, start, probe
#   .\start_scraper.ps1 -Stop        # tear it down again
#   $env:DISCOVERY_BACKEND='scraper_docker'   # then point the app at it

param([switch]$Stop)

$ErrorActionPreference = 'Stop'
# The kit lives under workspace/repos/ alongside the other reference repos, but
# accept workspace/ too in case it was cloned flat.
$Kit = @(
    (Join-Path $PSScriptRoot '..\workspace\repos\google-maps-scraper-kit'),
    (Join-Path $PSScriptRoot '..\workspace\google-maps-scraper-kit')
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Kit) { $Kit = Join-Path $PSScriptRoot '..\workspace\repos\google-maps-scraper-kit' }
$Base = if ($env:SCRAPER_BASE_URL) { $env:SCRAPER_BASE_URL } else { 'http://127.0.0.1:8080' }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker is not installed. The default OSM backend needs nothing - run without it."
    exit 1
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker is installed but the daemon is not running. Start Docker Desktop."
    exit 1
}

if ($Stop) {
    if (Test-Path $Kit) { docker compose -f "$Kit\docker-compose.yml" down }
    Write-Host "scraper stopped"
    exit 0
}

if (-not (Test-Path $Kit)) {
    git clone https://github.com/Mahanaicoach/google-maps-scraper-kit $Kit
}

docker compose -f "$Kit\docker-compose.yml" up -d --build

Write-Host "waiting for the engine at $Base ..."
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest -Uri "$Base/api/v1/jobs" -TimeoutSec 3 -UseBasicParsing
        if ($r.StatusCode -lt 400) {
            Write-Host "scraper is up (HTTP $($r.StatusCode))"
            Write-Host "enable it with:  `$env:DISCOVERY_BACKEND='scraper_docker'"
            exit 0
        }
    } catch { }
}
Write-Host "engine did not answer in 60s. Check: docker compose -f $Kit\docker-compose.yml logs"
exit 1
