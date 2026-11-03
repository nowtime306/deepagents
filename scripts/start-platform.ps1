$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$apiPath = Join-Path $projectPath 'services\api'
$webPath = Join-Path $projectPath 'apps\web'
$composePath = Join-Path $projectPath 'infra\compose'
$logPath = Join-Path $projectPath '.local'
New-Item -ItemType Directory -Path $logPath -Force | Out-Null

function Assert-Success {
    if ($LASTEXITCODE -ne 0) { throw '上一步执行失败，请查看上方输出。' }
}

if (!(Test-Path (Join-Path $composePath '.env')) -and !(Test-Path (Join-Path $apiPath '.env'))) {
    $databasePassword = [Guid]::NewGuid().ToString('N')
    "POSTGRES_PASSWORD=$databasePassword" | Set-Content (Join-Path $composePath '.env') -Encoding utf8
    "DATABASE_URL=postgresql://agent:$databasePassword@127.0.0.1:55432/owner_agent?connect_timeout=10`nWEB_ORIGIN=http://localhost:3100" |
        Set-Content (Join-Path $apiPath '.env') -Encoding utf8
}
if (!(Test-Path (Join-Path $composePath '.env')) -or !(Test-Path (Join-Path $apiPath '.env'))) {
    throw '数据库和 API 环境文件不完整，请参照 docs/local-development.md 补齐。'
}
docker compose --env-file (Join-Path $composePath '.env') -f (Join-Path $composePath 'compose.yml') up -d --wait
Assert-Success
Push-Location $apiPath
try {
    uv sync --python 3.12 --group test
    Assert-Success
    uv run --no-sync python -m app.manage migrate
    Assert-Success
    uv run --no-sync python -m app.manage bootstrap-admin
    Assert-Success
} finally { Pop-Location }
Push-Location $webPath
try {
    npx --yes pnpm@10.5.1 install --frozen-lockfile
    Assert-Success
} finally { Pop-Location }

$apiPort = Get-NetTCPConnection -State Listen -LocalPort 8100 -ErrorAction SilentlyContinue
$webPort = Get-NetTCPConnection -State Listen -LocalPort 3100 -ErrorAction SilentlyContinue
if ($apiPort) {
    $health = Invoke-RestMethod 'http://127.0.0.1:8100/health/live'
    if ($health.service -ne 'owner-agent') { throw '8100 端口已被其他服务占用。' }
}
if ($webPort) {
    $webPage = Invoke-WebRequest 'http://localhost:3100'
    if ($webPage.Content -notmatch '知行') { throw '3100 端口已被其他服务占用。' }
}
if (!$apiPort) {
    & (Join-Path $PSScriptRoot 'start-managed-service.ps1') -Service api
}
if (!$webPort) {
    $nextPath = Join-Path $webPath 'node_modules\next\dist\bin\next'
    Start-Process -FilePath (Get-Command node).Source -ArgumentList "`"$nextPath`"", 'dev', '--webpack', '--hostname', '127.0.0.1', '--port', '3100' -WorkingDirectory $webPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logPath 'web.log') -RedirectStandardError (Join-Path $logPath 'web-error.log') -PassThru |
        Select-Object -ExpandProperty Id | Set-Content (Join-Path $logPath 'web.pid')
}
Write-Output '工作台：http://localhost:3100'
Write-Output '管理员账号文件：services/api/.env.admin（仅保存在本机）'
Write-Output '启动后可检查 API：http://127.0.0.1:8100/health/ready'
$apiReady = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:8100/health/ready' -TimeoutSec 2
        if ($health.status -eq 'ready') { $apiReady = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}
if ($apiReady) {
    & (Join-Path $PSScriptRoot 'start-ask-worker.ps1')
    & (Join-Path $PSScriptRoot 'start-work-worker.ps1')
    & (Join-Path $PSScriptRoot 'start-managed-service.ps1') -Service ops
}
else { throw 'API 尚未就绪，对话后台未启动，请查看 API 日志。' }
