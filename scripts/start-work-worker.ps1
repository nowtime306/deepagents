$ErrorActionPreference = 'Stop'
$health = Invoke-RestMethod 'http://127.0.0.1:8100/health/ready' -TimeoutSec 5
$live = Invoke-RestMethod 'http://127.0.0.1:8100/health/live' -TimeoutSec 5
if ($health.status -ne 'ready' -or $live.service -ne 'owner-agent') { throw '本平台 API 尚未就绪。' }
if (!(Get-NetTCPConnection -State Listen -LocalPort 18180 -ErrorAction SilentlyContinue)) {
    & (Join-Path $PSScriptRoot 'start-opensandbox.ps1')
}
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$managerKey = Join-Path $projectPath 'services\sandbox\.local\manager.key'
$null = Invoke-RestMethod 'http://127.0.0.1:18180/v1/sandboxes' -Headers @{
    'OPEN-SANDBOX-API-KEY' = (Get-Content -LiteralPath $managerKey -Raw).Trim()
} -TimeoutSec 5
$missingImage = $false
foreach ($image in @('owner-agent/coding:dev', 'owner-agent/browser:dev')) {
    docker image inspect $image *> $null
    if ($LASTEXITCODE -ne 0) { $missingImage = $true }
}
if ($missingImage) { & (Join-Path $PSScriptRoot 'build-sandbox-images.ps1') }
& (Join-Path $PSScriptRoot 'start-managed-service.ps1') -Service work
