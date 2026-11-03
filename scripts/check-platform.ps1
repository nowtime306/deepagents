#Requires -Version 7
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$apiPath = Join-Path $projectPath 'services\api'
$pythonPath = Join-Path $apiPath '.venv\Scripts\python.exe'
$webPath = Join-Path $projectPath 'apps\web'
$results = [ordered]@{}
$live = Invoke-RestMethod 'http://127.0.0.1:8100/health/live' -TimeoutSec 5
$ready = Invoke-RestMethod 'http://127.0.0.1:8100/health/ready' -TimeoutSec 5
if ($live.service -ne 'owner-agent' -or $ready.status -ne 'ready') { throw '本项目API未就绪。' }
$results.api = 'ready'
$page = Invoke-WebRequest 'http://127.0.0.1:3100/' -TimeoutSec 5
if ($page.StatusCode -ne 200 -or $page.Content -notmatch '知行') { throw '本项目Web未就绪。' }
$webId = [int](Get-Content -LiteralPath (Join-Path $projectPath '.local\web.pid') -Raw)
$web = Get-CimInstance Win32_Process -Filter "ProcessId=$webId"
if ($web.Name -ne 'node.exe' -or $web.CommandLine -notmatch [regex]::Escape((Join-Path $webPath 'node_modules\next\dist\bin\next')) -or
    $web.CommandLine -notmatch '\bstart\b' -or $web.CommandLine -notmatch '(?:--port|-p)\s+3100(?:\s|$)') { throw '3100不是已记录的生产Web。' }
$listeners = @(Get-NetTCPConnection -LocalPort 3100 -State Listen -ErrorAction Stop)
if (@($listeners | Where-Object OwningProcess -ne $webId).Count) { throw '3100监听进程与本项目记录不一致。' }
$results.web = 'production-ready'
$keyPath = Join-Path $projectPath 'services\sandbox\.local\manager.key'
$null = Invoke-RestMethod 'http://127.0.0.1:18180/v1/sandboxes' -Headers @{'OPEN-SANDBOX-API-KEY'=(Get-Content -LiteralPath $keyPath -Raw).Trim()} -TimeoutSec 5
$results.sandbox = 'authenticated'
foreach ($image in @('owner-agent/coding:dev','owner-agent/browser:dev')) {
    docker image inspect $image *> $null
    if ($LASTEXITCODE -ne 0) { throw "执行镜像缺失：$image" }
}
$results.images = 'present'
Push-Location $apiPath
try {
    $raw = @'
import json
import psycopg
from app.config import Settings
s=Settings.from_env()
with psycopg.connect(s.database_url) as c:
    live=[r[0] for r in c.execute("SELECT DISTINCT kind FROM service_instances WHERE heartbeat_at>now()-interval '45 seconds'")]
    work=c.execute("SELECT count(*) FROM work_worker_instances WHERE heartbeat_at>now()-interval '45 seconds'").fetchone()[0]
    applied={r[0] for r in c.execute('SELECT version FROM schema_migrations')}
    from pathlib import Path
    pending=[p.name for p in Path('migrations').glob('*.sql') if p.name not in applied]
print(json.dumps({'data_dir':str(s.data_dir),'services':live,'work_instances':work,'pending_migrations':pending,'work_enabled':s.work_enabled,'ask_enabled':s.ask_worker_enabled}))
'@ | & $pythonPath -
    if ($LASTEXITCODE -ne 0) { throw '后台心跳/迁移检查失败。' }
    $state = $raw | ConvertFrom-Json
    if (!$state.work_enabled -or !$state.ask_enabled -or $state.work_instances -lt 1 -or @($state.pending_migrations).Count -or
        @('api','ask','work','ops' | Where-Object { $_ -notin $state.services }).Count) { throw '后台开关、真实心跳或迁移状态未通过。' }
    foreach ($kind in @('api','ask','work','ops')) {
        $pidFile = if ($kind -eq 'api') {Join-Path $projectPath '.local\api.pid'} else {Join-Path $state.data_dir "$kind-worker.pid"}
        $instanceId = [int](Get-Content -LiteralPath $pidFile -Raw)
        $instance = Get-CimInstance Win32_Process -Filter "ProcessId=$instanceId"
        $module = @{api='server';ask='ask_server';work='work_server';ops='operations_worker'}[$kind]
        if ($instance.Name -ne 'python.exe' -or $instance.ExecutablePath -ne $pythonPath -or
            ($instance.CommandLine -notmatch " -m app\.service_host --service $kind(?:\s|$)" -and
             $instance.CommandLine -notmatch " -m app\.$module(?:\s|$)")) { throw "后台进程归属或存活未通过：$kind" }
        $results[$kind + '_process'] = 'owned-alive'
    }
} finally { Pop-Location }
$results.migrations = 'current'
$results.browser_skill = 'not-configured (optional)'
if (Test-Path -LiteralPath (Join-Path $state.data_dir 'browser-skill\connection.json')) {
    try {
        $browserSkill = & (Join-Path $PSScriptRoot 'check-browser-skill.ps1') -DataDir $state.data_dir | ConvertFrom-Json
        $results.browser_skill = if ($browserSkill.ready) { 'ready (optional)' } else { 'unavailable (optional)' }
    } catch { $results.browser_skill = 'unavailable (optional)' }
}
$results | ConvertTo-Json
