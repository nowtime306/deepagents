#Requires -Version 7
param([Parameter(Mandatory)][string]$Executable, [Parameter(Mandatory)][string]$Extension,
    [Parameter(Mandatory)][string]$Chromium, [Parameter(Mandatory)][string]$Playwright)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'browser-skill-control.ps1')
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$fixtureRoot = Join-Path $repository '.local'
if ((Test-Path -LiteralPath $fixtureRoot) -and ((Get-Item -LiteralPath $fixtureRoot).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'QA fixture父目录不可为链接。' }
$data = Join-Path $repository ('.local\browser-skill-ops-qa-' + [guid]::NewGuid().ToString('N'))
if (![IO.Path]::GetFullPath($data).StartsWith($repository + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'QA fixture目录超出workspace。' }
$root = Join-Path $data 'browser-skill'
New-Item -ItemType Directory -Path $data | Out-Null
$checks = [Collections.Generic.List[string]]::new()
$started = $false
$originalState = $null
$listener = $null
$statePath = Join-Path $root 'state.json'
Start-Transcript -LiteralPath (Join-Path $data 'qa-transcript.log') | Out-Null
try {
    foreach ($port in @(52800,52982,52983)) {
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "QA需要空闲专用端口$port，未停止或接管现有实例。" }
    }
    & (Join-Path $PSScriptRoot 'start-browser-skill.ps1') -DataDir $data -Executable $Executable -Extension $Extension -Chromium $Chromium -Playwright $Playwright
    $started = $true
    $first = Read-BrowserSkillJson $statePath
    $connection = Read-BrowserSkillJson (Join-Path $root 'connection.json')
    & (Join-Path $PSScriptRoot 'check-browser-skill.ps1') -DataDir $data
    $checks.Add('real official CLI/extension/protocol/unique-peer and loopback checks passed')
    & (Join-Path $PSScriptRoot 'start-browser-skill.ps1') -DataDir $data
    $again = Read-BrowserSkillJson $statePath
    if ($again.helper_pid -ne $first.helper_pid -or $again.browser_pid -ne $first.browser_pid) { throw '重复启动创建了第二实例。' }
    $checks.Add('repeat start kept the same owned processes')
    $originalState = Get-Content -LiteralPath $statePath -Raw
    $modified = $originalState | ConvertFrom-Json
    $modified.browser_pid = $modified.helper_pid
    $modified | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
    try {
        & (Join-Path $PSScriptRoot 'stop-browser-skill.ps1') -DataDir $data
        throw 'QA_OWNERSHIP_UNEXPECTEDLY_ALLOWED'
    } catch {
        if ($_.Exception.Message -notmatch '进程归属检查失败') { throw }
    } finally { Set-Content -LiteralPath $statePath -Value $originalState -Encoding utf8; $originalState = $null }
    if (Test-Path -LiteralPath (Join-Path $root 'stop.request')) { throw '归属不匹配时错误地产生了停止请求。' }
    $checks.Add('tampered browser PID rejected without a stop request')
    & (Join-Path $PSScriptRoot 'stop-browser-skill.ps1') -DataDir $data
    $started = $false
    if ((Read-BrowserSkillJson $statePath).status -ne 'stopped' -or (Test-Path -LiteralPath (Join-Path $root 'connection.json'))) { throw '停止状态或连接文件未收敛。' }
    foreach ($processId in @($first.daemon_pid,$first.browser_pid)) {
        if (Get-CimInstance Win32_Process -Filter "ProcessId=$processId") { throw '停止后专用子进程仍然存活。' }
    }
    foreach ($port in @(52982,52983)) {
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw '停止后专用端口未释放。' }
    }
    $checks.Add('normal browser/session/daemon shutdown removed connection and released ports')
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,52983)
    $listener.Start()
    try {
        & (Join-Path $PSScriptRoot 'start-browser-skill.ps1') -DataDir $data
        throw 'QA_PORT_UNEXPECTEDLY_ADOPTED'
    } catch {
        if ($_.Exception.Message -notmatch '端口52983 已被占用') { throw }
    } finally { $listener.Stop(); $listener = $null }
    if (Test-Path -LiteralPath (Join-Path $root 'connection.json')) { throw '端口冲突时错误发布了ready连接。' }
    $checks.Add('foreign listener conflict refused; no adoption or ready connection')
    # Force failure after Chrome starts but before a CDP Browser object exists.
    $invalidModule = Join-Path $data 'invalid-playwright.cjs'
    'module.exports = {};' | Set-Content -LiteralPath $invalidModule -Encoding ascii
    $failureData = Join-Path $data 'startup-failure'
    try {
        & (Join-Path $PSScriptRoot 'start-browser-skill.ps1') -DataDir $failureData -Executable $Executable -Extension $Extension -Chromium $Chromium -Playwright $invalidModule
        throw 'QA_STARTUP_FAILURE_UNEXPECTEDLY_READY'
    } catch {
        if ($_.Exception.Message -notmatch '启动失败') { throw }
    }
    $failedRoot = Join-Path $failureData 'browser-skill'
    $failed = Read-BrowserSkillJson (Join-Path $failedRoot 'state.json')
    if ($failed.status -ne 'failed' -or $failed.error -match 'CLEANUP_REQUIRED' -or (Test-Path -LiteralPath (Join-Path $failedRoot 'connection.json'))) { throw '启动失败未正常关闭专用进程。' }
    foreach ($processId in @($failed.helper_pid,$failed.daemon_pid,$failed.browser_pid)) {
        if (Get-CimInstance Win32_Process -Filter "ProcessId=$processId") { throw '启动失败后专用进程仍然存活。' }
    }
    foreach ($port in @(52982,52983)) {
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw '启动失败后专用端口未释放。' }
    }
    $checks.Add('failure before CDP setup closed only owned child processes; no ready config or listeners')
    @{ passed=$true; instance_id=$connection.instance_id; checks=$checks; data_dir=$data } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $data 'qa-result.json') -Encoding utf8
    Write-Output "QA通过；证据保留：$data"
} finally {
    if ($originalState) { Set-Content -LiteralPath $statePath -Value $originalState -Encoding utf8 }
    if ($listener) { $listener.Stop() }
    if ($started) { & (Join-Path $PSScriptRoot 'stop-browser-skill.ps1') -DataDir $data }
    Stop-Transcript | Out-Null
}
