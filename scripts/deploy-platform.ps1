#Requires -Version 7
param([switch]$InstallDependencies,[switch]$Preflight,[switch]$BackendOnly)
$ErrorActionPreference = 'Stop'
if ($BackendOnly -and $InstallDependencies) { throw '后端补丁部署不能同时安装依赖，请使用完整维护部署。' }
$deploymentScope = if ($BackendOnly) { 'backend-only' } else { 'full' }
. (Join-Path $PSScriptRoot 'service-control.ps1')
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$apiPath = Join-Path $projectPath 'services\api'
$webPath = Join-Path $projectPath 'apps\web'
$composePath = Join-Path $projectPath 'infra\compose'
$pythonPath = Join-Path $apiPath '.venv\Scripts\python.exe'
$logPath = Join-Path $projectPath '.local'
foreach ($command in @('git','docker','node')) { $null = Get-Command $command -ErrorAction Stop }
if ((& git -C $projectPath branch --show-current).Trim() -ne 'onwer_agent' -or $LASTEXITCODE -ne 0) { throw '请在onwer_agent分支部署。' }
foreach ($file in @((Join-Path $apiPath '.env'),(Join-Path $composePath '.env'))) {
    if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw '基础设施环境文件不完整，未覆盖任何配置；见docs/system-deployment.md。' }
}
if ($InstallDependencies -and !$Preflight) {
    if (Get-NetTCPConnection -State Listen -LocalPort 8100,3100 -ErrorAction SilentlyContinue) { throw '依赖安装需要先进入维护窗口并停止平台API/Web。' }
    $activeBackends = @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -eq $pythonPath -and $_.CommandLine -match ' -m app\.(?:service_host|server|ask_server|work_server|operations_worker)(?:\s|$)'
    })
    if ($activeBackends.Count) { throw '依赖安装前需要停止本项目全部后台，不能在Worker运行时改写环境。' }
    $null = Get-Command uv -ErrorAction Stop
    Push-Location $apiPath
    try {
        uv sync --frozen --python 3.12 --group test
        if ($LASTEXITCODE -ne 0) { throw '锁定后端依赖安装失败。' }
    } finally { Pop-Location }
    Push-Location $webPath
    try {
        npx --yes pnpm@10.5.1 install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw '锁定前端依赖安装失败。' }
    } finally { Pop-Location }
}
foreach ($file in @($pythonPath,(Join-Path $webPath 'node_modules\next\dist\bin\next'))) {
    if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw '依赖尚未准备，请先执行维护窗口内的-InstallDependencies。' }
}
Push-Location $apiPath
try {
    & $pythonPath -c 'from app.config import Settings; s=Settings.from_env(); assert s.work_enabled and s.ask_worker_enabled, "完整部署需要WORK_ENABLED与ASK_WORKER_ENABLED"'
    if ($LASTEXITCODE -ne 0) { throw '完整后台开关检查失败，未停止现有服务。' }
} finally { Pop-Location }
$osType = docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0 -or $osType -ne 'linux') { throw 'Docker Desktop尚未就绪或未使用Linux容器。' }
if ($Preflight) { Write-Output '前置检查通过；未启停服务、安装依赖或写数据库。'; return }
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
$controlLock = Open-PlatformControlLock (Join-Path $logPath 'platform-deploy.lock')
$phase = 'infrastructure'
$backupPath = $null
$admissionProcess = $null
try {
    $databaseListener = Get-NetTCPConnection -State Listen -LocalPort 55432 -ErrorAction SilentlyContinue
    if ($databaseListener) {
        $projectLabel = docker inspect owner-agent-postgres-1 --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>$null
        if ($LASTEXITCODE -ne 0 -or $projectLabel -ne 'owner-agent') { throw '55432不属于本项目PostgreSQL，未覆盖其他服务。' }
    }
    docker compose --env-file (Join-Path $composePath '.env') -f (Join-Path $composePath 'compose.yml') up -d --wait postgres
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL未通过就绪检查。' }
    if (!(Get-NetTCPConnection -State Listen -LocalPort 18180 -ErrorAction SilentlyContinue)) {
        & (Join-Path $PSScriptRoot 'start-opensandbox.ps1')
    }
    $keyPath = Join-Path $projectPath 'services\sandbox\.local\manager.key'
    $null = Invoke-RestMethod 'http://127.0.0.1:18180/v1/sandboxes' -Headers @{'OPEN-SANDBOX-API-KEY'=(Get-Content -LiteralPath $keyPath -Raw).Trim()} -TimeoutSec 5
    $missingImage = $false
    foreach ($image in @('owner-agent/coding:dev','owner-agent/browser:dev')) {
        docker image inspect $image *> $null
        if ($LASTEXITCODE -ne 0) { $missingImage = $true }
    }
    if ($missingImage) { & (Join-Path $PSScriptRoot 'build-sandbox-images.ps1') }
    $mediaApproval = Join-Path $logPath 'media-decoder-image.json'
    $mediaReady = $false
    if (Test-Path -LiteralPath $mediaApproval -PathType Leaf) {
        $mediaId = (Get-Content -LiteralPath $mediaApproval -Raw | ConvertFrom-Json).image_id
        if ($mediaId -notmatch '^sha256:[a-f0-9]{64}$') { throw '媒体镜像批准记录无效，未覆盖原记录。' }
        docker image inspect $mediaId *> $null
        $mediaReady = $LASTEXITCODE -eq 0
    }
    if (!$mediaReady) { & (Join-Path $PSScriptRoot 'build-media-image.ps1') }
    $phase = 'admission'
    $admissionInfo = [System.Diagnostics.ProcessStartInfo]::new($pythonPath)
    $admissionInfo.ArgumentList.Add('-u')
    $admissionInfo.ArgumentList.Add((Join-Path $PSScriptRoot 'deployment_gate.py'))
    $admissionInfo.WorkingDirectory = $apiPath
    $admissionInfo.UseShellExecute = $false
    $admissionInfo.CreateNoWindow = $true
    $admissionInfo.RedirectStandardInput = $true
    $admissionInfo.RedirectStandardOutput = $true
    $admissionInfo.RedirectStandardError = $true
    $admissionProcess = [System.Diagnostics.Process]::Start($admissionInfo)
    $handshake = $admissionProcess.StandardOutput.ReadLineAsync()
    if (!$handshake.Wait(10000)) { throw '准入栅栏握手超时，未停止后台。' }
    $admissionState = $handshake.Result
    if ($admissionState -notin @('READY:existing','READY:fresh') -or $admissionProcess.HasExited) {
        throw "准入栅栏拒绝部署（$admissionState），未停止后台。"
    }
    $schema = $admissionState.Substring(6)
    $phase = 'stop-services'
    & (Join-Path $PSScriptRoot 'stop-platform-services.ps1') -Service all
    if ($admissionProcess.HasExited) { throw '停服期间准入锁连接丢失，停止发布；未迁移数据库。' }
    $admissionProcess.StandardInput.Close()
    if (!$admissionProcess.WaitForExit(7000) -or $admissionProcess.ExitCode -ne 0) {
        throw '准入栅栏未正常释放，停止发布；未迁移数据库。'
    }
    $admissionProcess.Dispose()
    $admissionProcess = $null
    Push-Location $apiPath
    try {
        if ($schema.Trim() -eq 'existing') {
            $phase = 'offline-backup'
            @'
import time,psycopg
from app.config import Settings
with psycopg.connect(Settings.from_env().database_url,autocommit=True) as c:
    deadline=time.monotonic()+50
    while True:
        busy=False
        for table in ('service_instances','work_worker_instances'):
            if c.execute('SELECT to_regclass(%s)',(table,)).fetchone()[0]:
                busy |= bool(c.execute('SELECT 1 FROM '+table+" WHERE heartbeat_at>now()-interval '45 seconds' LIMIT 1").fetchone())
        if not busy: break
        if time.monotonic()>=deadline: raise RuntimeError('真实后台心跳尚未过期，拒绝继续')
        time.sleep(1)
'@ | & $pythonPath -
            if ($LASTEXITCODE -ne 0) { throw '离线条件未满足，未创建备份或迁移。' }
            $backupPath = & $pythonPath -m app.backup create 2> (Join-Path $logPath 'platform-backup-error.log')
            if ($LASTEXITCODE -ne 0) { throw '迁移前备份失败，停止发布；查看.local/platform-backup-error.log。' }
            $backupPath = ($backupPath | Select-Object -Last 1).Trim()
            & $pythonPath -c 'from pathlib import Path; from app.backup import verify_files; import sys; verify_files(Path(sys.argv[1]))' $backupPath
            if ($LASTEXITCODE -ne 0) { throw '备份manifest摘要校验失败，未迁移。' }
        } else { Write-Output '确认是首次空数据库，没有已有数据可备份。' }
        $phase = 'migrate'
        & $pythonPath -m app.manage migrate *> (Join-Path $logPath 'platform-migration.log')
        if ($LASTEXITCODE -ne 0) { throw '迁移失败，后台保持停止；不要自动恢复生产数据库。' }
        & $pythonPath -m app.manage bootstrap-admin *> (Join-Path $logPath 'platform-bootstrap.log')
        if ($LASTEXITCODE -ne 0) { throw '首次管理员准备失败，未启动后台；已有账号不会重置。' }
    } finally { Pop-Location }
    $phase = 'start-backend'
    & (Join-Path $PSScriptRoot 'start-managed-service.ps1') -Service api
    $apiReady = $false
    for ($attempt=0; $attempt -lt 40; $attempt++) {
        try { if ((Invoke-RestMethod 'http://127.0.0.1:8100/health/ready' -TimeoutSec 2).status -eq 'ready') { $apiReady=$true; break } } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (!$apiReady) { throw 'API尚未就绪，未启动其他后台。' }
    & (Join-Path $PSScriptRoot 'start-ask-worker.ps1')
    & (Join-Path $PSScriptRoot 'start-work-worker.ps1')
    & (Join-Path $PSScriptRoot 'start-managed-service.ps1') -Service ops
    if (!$BackendOnly) {
        $phase = 'production-web'
        & (Join-Path $PSScriptRoot 'deploy-knowledge-ui.ps1')
    }
    $phase = 'health-check'
    $healthy = $false
    for ($attempt=0; $attempt -lt 20; $attempt++) {
        try {
            & (Join-Path $PSScriptRoot 'check-platform.ps1') | Set-Content -LiteralPath (Join-Path $logPath 'platform-health.json') -Encoding utf8
            $healthy = $true; break
        } catch { if ($attempt -eq 19) { throw }; Start-Sleep -Seconds 1 }
    }
    if (!$healthy) { throw '完整健康检查失败。' }
    @{status='deployed';phase='complete';scope=$deploymentScope;web=$(if ($BackendOnly) {'unchanged'} else {'rebuilt'});
      backup=$backupPath;time=(Get-Date).ToUniversalTime().ToString('o')} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logPath 'platform-deployment.json') -Encoding utf8
    if ($BackendOnly) { Write-Output '后端补丁发布完成；未重建或重启Web，四后台及整套健康检查通过。' }
    else { Write-Output '整套系统发布完成，生产Web/API/Ask/Work/Ops及执行环境检查通过。' }
} catch {
    @{status='failed';phase=$phase;scope=$deploymentScope;web=$(if ($BackendOnly) {'unchanged'} else {'not-confirmed'});
      backup=$backupPath;time=(Get-Date).ToUniversalTime().ToString('o')} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logPath 'platform-deployment.json') -Encoding utf8
    Write-Output "发布未完成，阶段：$phase；检查.local/platform-deployment.json及阶段日志。未自动恢复生产数据库或重放任务。"
    throw
} finally {
    if ($admissionProcess) {
        $admissionProcess.StandardInput.Close()
        $null = $admissionProcess.WaitForExit(7000)
        $admissionProcess.Dispose()
    }
    $controlLock.Dispose()
}
