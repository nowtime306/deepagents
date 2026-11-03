#Requires -Version 7
param([string]$DataDir = '')
. (Join-Path $PSScriptRoot 'browser-skill-control.ps1')
$root = Get-BrowserSkillRoot $DataDir
if (!(Test-Path -LiteralPath (Join-Path $root 'connection.json'))) { throw 'BrowserSkill可选能力未配置或离线。聊天服务不依赖此检查。' }
$state = Read-BrowserSkillJson (Join-Path $root 'state.json')
if ($state.status -ne 'ready') { throw "BrowserSkill未就绪：$($state.status)" }
Assert-BrowserSkillProcess $state $root
$connection = Read-BrowserSkillJson (Join-Path $root 'connection.json')
if (@($connection.PSObject.Properties).Count -ne 6 -or $connection.schema_version -ne 1 -or
    $connection.executable -ne $state.executable -or $connection.instance_id -notmatch '^[0-9a-f]{8}$' -or
    $connection.cli_version -ne '0.4.1' -or $connection.extension_version -ne '0.4.1' -or $connection.protocol_version -ne '1.8') { throw '专用连接配置不符合固定schema。' }
$previous = @{}
try {
    foreach ($pair in @{BSK_HOME=(Join-Path $root 'home');BSK_AUTO_START='0';BSK_AUTO_UPDATE='off';BSK_BROWSER_WAIT_MS='0'}.GetEnumerator()) {
        $previous[$pair.Key] = [Environment]::GetEnvironmentVariable($pair.Key,'Process')
        [Environment]::SetEnvironmentVariable($pair.Key,$pair.Value,'Process')
    }
    if ((& $state.executable --version) -ne 'bsk 0.4.1') { throw 'CLI版本不匹配。' }
    $peers = @((& $state.executable browsers --json) | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or $peers.Count -ne 1 -or $peers[0].instance_id -ne $connection.instance_id -or
        $peers[0].extension_version -ne '0.4.1' -or $peers[0].extension_protocol_version -ne '1.8') { throw '专用连接不唯一、离线或版本不匹配。' }
    @{ready=$true;instance_id=$connection.instance_id;cli_version='0.4.1';protocol_version='1.8';sessions=$peers[0].session_count} | ConvertTo-Json
} finally {
    foreach ($key in $previous.Keys) { [Environment]::SetEnvironmentVariable($key,$previous[$key],'Process') }
}
