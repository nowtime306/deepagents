#Requires -Version 7
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$privatePath = Join-Path $projectPath '.local\monitoring'
if (!(Test-Path -LiteralPath $privatePath)) { Write-Output '监控尚未初始化。'; return }
. (Join-Path $PSScriptRoot 'service-control.ps1')
$monitorLock = Open-PlatformControlLock (Join-Path $privatePath 'control.lock')
try {
    docker compose -p owner-agent-monitoring -f (Join-Path $projectPath 'infra\monitoring\compose.yml') stop
    if ($LASTEXITCODE -ne 0) { throw '监控未完整停止，请检查本项目容器状态。' }
    Write-Output '仅监控栈已停止；平台后台、数据卷和密钥保留。'
} finally { $monitorLock.Dispose() }
