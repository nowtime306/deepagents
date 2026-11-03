#Requires -Version 7
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectPath 'services/api/.venv/Scripts/python.exe'
if ((git -C $projectPath branch --show-current).Trim() -ne 'onwer_agent') { throw '请在onwer_agent工作区运行。' }
Push-Location (Join-Path $projectPath 'services/api')
try {
    & $pythonPath -u (Join-Path $PSScriptRoot 'verify_platform_topology.py')
    if ($LASTEXITCODE -ne 0) { throw '隔离主备或容量检查未通过，查看独立证据目录；生产未作为探针目标。' }
} finally { Pop-Location }
