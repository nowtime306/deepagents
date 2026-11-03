#Requires -Version 7
param([string]$DataDir = '')
. (Join-Path $PSScriptRoot 'browser-skill-control.ps1')
. (Join-Path $PSScriptRoot 'service-control.ps1')
$root = Get-BrowserSkillRoot $DataDir
if (!(Test-Path -LiteralPath (Join-Path $root 'state.json'))) { Write-Output '专用实例尚未启动。'; return }
$lock = Open-PlatformControlLock (Join-Path $root 'control.lock')
try {
    $state = Read-BrowserSkillJson (Join-Path $root 'state.json')
    if ($state.status -eq 'stopped') { Write-Output '专用实例已停止。'; return }
    Assert-BrowserSkillProcess $state $root
    'stop' | Set-Content -LiteralPath (Join-Path $root 'stop.request') -Encoding ascii
    $deadline = (Get-Date).AddSeconds(45)
    do {
        $state = Read-BrowserSkillJson (Join-Path $root 'state.json')
        if ($state.status -eq 'stopped') { Write-Output '专用浏览器context和daemon已正常关闭。'; return }
        if ($state.status -eq 'failed') { throw "停止未完成：$($state.error)" }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    throw '正常退出超时，请检查专用运行记录；未强杀任何其它浏览器。'
} finally { $lock.Dispose() }
