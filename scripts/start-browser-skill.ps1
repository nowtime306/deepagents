#Requires -Version 7
param([string]$DataDir = '', [string]$Executable = '', [string]$Extension = '', [string]$Chromium = '', [string]$Playwright = '')
. (Join-Path $PSScriptRoot 'browser-skill-control.ps1')
. (Join-Path $PSScriptRoot 'service-control.ps1')
$root = Get-BrowserSkillRoot $DataDir
New-Item -ItemType Directory -Force -Path $root | Out-Null
$lock = Open-PlatformControlLock (Join-Path $root 'control.lock')
try {
    $statePath = Join-Path $root 'state.json'
    if (Test-Path -LiteralPath $statePath) {
        $old = Read-BrowserSkillJson $statePath
        if (Get-CimInstance Win32_Process -Filter "ProcessId=$($old.helper_pid)") {
            & (Join-Path $PSScriptRoot 'check-browser-skill.ps1') -DataDir (Split-Path $root)
            return
        }
        if ($old.status -notin @('stopped','failed')) { throw '先检查上次异常退出的专用实例；禁止自动接管。' }
        foreach ($processId in @($old.daemon_pid,$old.browser_pid)) {
            if ($processId -gt 0 -and (Get-CimInstance Win32_Process -Filter "ProcessId=$processId")) { throw '旧实例仍有存活进程，禁止覆盖记录。' }
        }
    }
    foreach ($port in @(52983,52982)) {
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "专用端口$port 已被占用；不会连接或终止其它实例。" }
    }
    # A fresh official extension initially tries its default before local preferences load.
    if (Get-NetTCPConnection -LocalPort 52800 -State Listen -ErrorAction SilentlyContinue) { throw '官方扩展默认端口52800已有其它实例；拒绝启动空profile以防短暂误连。' }
    $dependencies = Join-Path $PSScriptRoot '..\.local\browser-skill-dependencies'
    if (!$Executable) { $Executable = Join-Path $dependencies 'cli\bsk.exe' }
    if (!$Extension) { $Extension = Join-Path $dependencies 'extension' }
    if (!$Playwright) { $Playwright = Join-Path $PSScriptRoot '..\apps\web\node_modules\@playwright\test\index.js' }
    if (!$Chromium) { throw '请指定已安装的专用Chromium可执行文件 -Chromium；不会安装或使用个人Chrome。' }
    $options = @{root=$root;executable=(Resolve-Path -LiteralPath $Executable).Path;extension=(Resolve-Path -LiteralPath $Extension).Path;
        chromium=(Resolve-Path -LiteralPath $Chromium).Path;playwright=(Resolve-Path -LiteralPath $Playwright).Path}
    $profile = Join-Path $root 'profile'
    $marker = Join-Path $root 'profile.owner'
    if ((Test-Path -LiteralPath $profile) -and (!(Test-Path -LiteralPath $marker) -or (Get-Content -LiteralPath $marker -Raw).Trim() -ne $root)) { throw '拒绝使用已有的非本服务浏览器profile。' }
    if ((Get-Item -LiteralPath $root).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '数据目录不可为链接。' }
    New-Item -ItemType Directory -Force -Path $profile | Out-Null
    if ((Get-Item -LiteralPath $profile).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'profile不可为链接。' }
    $root | Set-Content -LiteralPath $marker -Encoding utf8
    $options | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'launch.json') -Encoding utf8
    foreach ($name in @('stop.request','connection.json')) {
        $path = Join-Path $root $name
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
    }
    $node = (Get-Command node.exe).Source
    $arguments = '"' + (Join-Path $PSScriptRoot 'browser-skill-launcher.mts') + '" "' + (Join-Path $root 'launch.json') + '"'
    $helper = Start-Process -FilePath $node -ArgumentList $arguments -WindowStyle Hidden -WorkingDirectory $PSScriptRoot -PassThru `
        -RedirectStandardOutput (Join-Path $root 'launcher.log') -RedirectStandardError (Join-Path $root 'launcher-error.log')
    $deadline = (Get-Date).AddSeconds(80)
    do {
        if (Test-Path -LiteralPath $statePath) {
            $state = Read-BrowserSkillJson $statePath
            if ($state.helper_pid -eq $helper.Id -and $state.status -eq 'ready') {
                & (Join-Path $PSScriptRoot 'check-browser-skill.ps1') -DataDir (Split-Path $root)
                return
            }
            if ($state.helper_pid -eq $helper.Id -and $state.status -eq 'failed') { throw "启动失败：$($state.error)" }
        }
        if ($helper.HasExited) { throw '专用监督进程退出，请检查launcher-error.log。' }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $deadline)
    throw '启动未在时限内就绪；不会标记ready，请检查state.json。'
} finally { $lock.Dispose() }
