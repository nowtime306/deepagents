#Requires -Version 7
param([switch]$Preflight)
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$composePath = Join-Path $projectPath 'infra\monitoring\compose.yml'
$privatePath = Join-Path $projectPath '.local\monitoring'
$metricsPath = Join-Path $projectPath 'services\api\.local\monitoring'
if ((git -C $projectPath branch --show-current).Trim() -ne 'onwer_agent') { throw '请在onwer_agent工作区运行。' }
if ((docker info --format '{{.OSType}}') -ne 'linux' -or $LASTEXITCODE -ne 0) { throw 'Docker Linux引擎未就绪。' }
$apiPath = Join-Path $projectPath 'services/api'
Push-Location $apiPath
try {
    $dataPath = & (Join-Path $apiPath '.venv/Scripts/python.exe') -c 'from app.config import Settings; print(Settings.from_env().data_dir.resolve())'
    if ($LASTEXITCODE -ne 0) { throw '无法核对平台实例数据目录，未生成监控凭证。' }
} finally { Pop-Location }
if ($dataPath.Trim() -ne (Join-Path $apiPath '.local')) { throw '当前平台使用自定义数据目录，请配置实例专属采集secret；默认监控脚本拒绝猜测路径。' }
foreach ($filename in @('prometheus.yml','rules.yml','dashboards\platform.json','provisioning\datasources\prometheus.yml','provisioning\dashboards\platform.yml')) {
    if (!(Test-Path -LiteralPath (Join-Path (Split-Path $composePath) $filename) -PathType Leaf)) { throw "监控配置缺少：$filename" }
}
if ($Preflight) { Write-Output '监控配置与Docker前置检查通过；未写入密钥或启动容器。'; return }
New-Item -ItemType Directory -Path $privatePath -Force | Out-Null
New-Item -ItemType Directory -Path $metricsPath -Force | Out-Null
. (Join-Path $PSScriptRoot 'service-control.ps1')
$monitorLock = Open-PlatformControlLock (Join-Path $privatePath 'control.lock')
try {
    $listener = Get-NetTCPConnection -LocalPort 3300 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        $owned = docker compose -p owner-agent-monitoring -f $composePath ps -q grafana
        if (!$owned) { throw '3300端口已被非本监控项目占用，未停止其他服务。' }
    }
    foreach ($filename in @('metrics.token','grafana.admin')) {
        $secretPath = if ($filename -eq 'metrics.token') { Join-Path $metricsPath $filename } else { Join-Path $privatePath $filename }
        $legacyPath = Join-Path $privatePath 'metrics.token'
        if ($filename -eq 'metrics.token' -and (Test-Path -LiteralPath $legacyPath)) {
            if (Test-Path -LiteralPath $secretPath) {
                if ((Get-FileHash -LiteralPath $legacyPath).Hash -ne (Get-FileHash -LiteralPath $secretPath).Hash) { throw '新旧监控token不一致，拒绝覆盖或猜测迁移。' }
            } else { throw '检测到旧根目录token，请先按文档核对并显式迁移到默认实例数据目录。' }
        }
        if (!(Test-Path -LiteralPath $secretPath)) {
            $bytes = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
            $secret = [Convert]::ToBase64String($bytes)
            $stream = [System.IO.File]::Open($secretPath,[System.IO.FileMode]::CreateNew,[System.IO.FileAccess]::Write)
            try { $raw = [System.Text.Encoding]::UTF8.GetBytes($secret); $stream.Write($raw) } finally { $stream.Dispose() }
            $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
            $acl = [System.Security.AccessControl.FileSecurity]::new()
            $acl.SetOwner($sid)
            $acl.SetAccessRuleProtection($true,$false)
            foreach ($identity in @($sid,[System.Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
                $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($identity,'FullControl','Allow'))
            }
            Set-Acl -LiteralPath $secretPath -AclObject $acl
        }
        if ((Get-Item -LiteralPath $secretPath).Length -lt 32) { throw "监控密钥文件不完整：$filename；未自动覆盖。" }
    }
    $memory = Get-CimInstance Win32_OperatingSystem
    if ($memory.FreePhysicalMemory * 1024 -lt 2GB) { throw '可用内存不足2GiB，未启动可选监控栈；内置大盘仍可使用。' }
    docker compose -p owner-agent-monitoring -f $composePath config --quiet
    if ($LASTEXITCODE -ne 0) { throw '监控compose校验失败。' }
    docker compose -p owner-agent-monitoring -f $composePath up -d
    if ($LASTEXITCODE -ne 0) { throw '监控容器未完整启动；保留数据卷和密钥，请检查状态。' }
    $healthy = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try { & (Join-Path $PSScriptRoot 'check-monitoring.ps1'); $healthy = $true; break }
        catch { if ($attempt -eq 59) { throw }; Start-Sleep -Seconds 2 }
    }
    if (!$healthy) { throw '监控采集或看板未通过检查。' }
    Write-Output '监控已启动：http://localhost:3300/d/owner-agent-platform；账号admin，密码仅保存在.local/monitoring/grafana.admin，未输出。'
} finally { $monitorLock.Dispose() }
