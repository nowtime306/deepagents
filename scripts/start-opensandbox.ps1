$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$servicePath = Join-Path $projectPath 'services\sandbox'
$localPath = Join-Path $servicePath '.local'
New-Item -ItemType Directory -Path $localPath -Force | Out-Null
$acl = Get-Acl -LiteralPath $localPath
$allowedSids = @(
    [Security.Principal.WindowsIdentity]::GetCurrent().User.Value,
    'S-1-5-18', 'S-1-5-32-544'
)
$existingRules = @($acl.Access)
$alreadyRestricted = $acl.AreAccessRulesProtected -and $existingRules.Count -eq $allowedSids.Count
foreach ($existingRule in $existingRules) {
    $sid = $existingRule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    if ($sid -notin $allowedSids -or $existingRule.IsInherited -or
        $existingRule.AccessControlType -ne 'Allow' -or $existingRule.FileSystemRights -ne 'FullControl' -or
        $existingRule.InheritanceFlags -ne 'ContainerInherit, ObjectInherit' -or
        $existingRule.PropagationFlags -ne 'None') { $alreadyRestricted = $false }
}
$acl.SetAccessRuleProtection($true, $false)
foreach ($rule in @($acl.Access)) { [void]$acl.RemoveAccessRuleAll($rule) }
$identities = @(
    [Security.Principal.WindowsIdentity]::GetCurrent().User,
    [Security.Principal.SecurityIdentifier]::new('S-1-5-18'),
    [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
)
foreach ($identity in $identities) {
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
        $identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'
    )
    $acl.AddAccessRule($rule)
}
if (!$alreadyRestricted) { Set-Acl -LiteralPath $localPath -AclObject $acl }

if (Get-NetTCPConnection -State Listen -LocalPort 18180 -ErrorAction SilentlyContinue) {
    throw '18180 端口已被占用；请先确认该端口的服务。'
}
$osType = docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0 -or $osType -ne 'linux') { throw '请先启动 Docker Desktop 并切换至 Linux 容器模式。' }
foreach ($image in @('opensandbox/execd:v1.1.0', 'opensandbox/egress:v1.1.7', 'python:3.12-slim')) {
    docker image inspect $image *> $null
    if ($LASTEXITCODE -ne 0) {
        docker pull $image
        if ($LASTEXITCODE -ne 0) { throw "无法获取沙箱镜像：$image" }
    }
}
uv sync --project $servicePath --frozen --no-dev --python 3.12
if ($LASTEXITCODE -ne 0) { throw '安装沙箱管理服务依赖失败。' }
$keyPath = Join-Path $localPath 'manager.key'
if (!(Test-Path -LiteralPath $keyPath)) {
    $key = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
    Set-Content -LiteralPath $keyPath -Value $key -Encoding ascii -NoNewline
}
$env:OPENSANDBOX_SERVER_API_KEY = Get-Content -LiteralPath $keyPath -Raw
try {
    $config = Join-Path $servicePath 'windows.toml'
    $process = Start-Process -FilePath (Join-Path $servicePath '.venv\Scripts\opensandbox-server.exe') `
        -ArgumentList '--config', "`"$config`"" -WorkingDirectory $servicePath -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $localPath 'server.log') `
        -RedirectStandardError (Join-Path $localPath 'server-error.log') -PassThru
} finally { Remove-Item Env:OPENSANDBOX_SERVER_API_KEY -ErrorAction SilentlyContinue }
$ready = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) { break }
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:18180/v1/sandboxes' `
            -Headers @{ 'OPEN-SANDBOX-API-KEY' = (Get-Content -LiteralPath $keyPath -Raw) } -TimeoutSec 2
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}
if (!$ready) {
    $listener = Get-NetTCPConnection -LocalPort 18180 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if ($owner.CommandLine -like '*services\sandbox\windows.toml*') {
            Stop-Process -Id $owner.ProcessId -ErrorAction SilentlyContinue
        }
    }
    if (!$process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
    throw "沙箱服务未就绪，请查看 $localPath\server-error.log。"
}
$process.Id | Set-Content -LiteralPath (Join-Path $localPath 'server.pid') -Encoding ascii
Write-Output 'OpenSandbox 管理服务已启动（仅本机访问）。任务执行仍需完成安全验收后开放。'
