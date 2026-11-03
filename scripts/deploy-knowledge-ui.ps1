$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'service-control.ps1')
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$webPath = Join-Path $projectPath 'apps\web'
$logPath = Join-Path $projectPath '.local'
$nextPath = Join-Path $webPath 'node_modules\next\dist\bin\next'
$buildPath = Join-Path $webPath '.next'
$pidPath = Join-Path $logPath 'web.pid'
$nodePath = (Get-Command node -ErrorAction Stop).Source
if ((& git -C $projectPath branch --show-current).Trim() -ne 'onwer_agent') { throw '请在 onwer_agent 开发分支部署。' }
if (!(Test-Path -LiteralPath $nextPath)) { throw '请先安装本项目 Web 依赖。' }
$health = Invoke-RestMethod 'http://127.0.0.1:8100/health/ready' -TimeoutSec 10
if ($health.status -ne 'ready') { throw 'API尚未就绪，未停止网页服务。' }
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
$controlLock = Open-PlatformControlLock (Join-Path $logPath 'web-deploy.lock')
$releaseId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$previousBuild = Join-Path $logPath "knowledge-web-previous-$releaseId"
$failedBuild = Join-Path $logPath "knowledge-web-failed-$releaseId"
$oldNodeOptions = $env:NODE_OPTIONS

function Move-ProjectBuild([string]$Source, [string]$Target) {
    $resolvedSource = (Resolve-Path -LiteralPath $Source).Path
    $targetParent = (Resolve-Path -LiteralPath (Split-Path -Parent $Target)).Path
    $resolvedTarget = Join-Path $targetParent ([IO.Path]::GetFileName($Target))
    $workspacePrefix = $projectPath.TrimEnd('\') + '\'
    if (!$resolvedSource.StartsWith($workspacePrefix,[StringComparison]::OrdinalIgnoreCase) -or
        !$resolvedTarget.StartsWith($workspacePrefix,[StringComparison]::OrdinalIgnoreCase)) {
        throw '构建移动目标不在本项目内，已停止。'
    }
    foreach ($path in @($resolvedSource,$targetParent)) {
        $directory = Get-Item -LiteralPath $path
        while ($directory) {
            if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '构建路径包含链接或联接，未移动。' }
            if ($directory.FullName -eq $projectPath) { break }
            $directory = $directory.Parent
        }
    }
    if (Test-Path -LiteralPath $resolvedTarget) { throw '目标目录已存在，未覆盖任何构建。' }
    Move-Item -LiteralPath $resolvedSource -Destination $resolvedTarget
}

function Stop-ProjectWeb {
    $listener = @(Get-NetTCPConnection -LocalPort 3100 -State Listen -ErrorAction SilentlyContinue)
    $recordedId = 0
    if (Test-Path -LiteralPath $pidPath) {
        if (![int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(),[ref]$recordedId)) { throw '网页服务编号记录无效。' }
    }
    $process = if ($recordedId) { Get-CimInstance Win32_Process -Filter "ProcessId=$recordedId" -ErrorAction SilentlyContinue }
    if ($process) {
        $owned = $process.Name -eq 'node.exe' -and $process.CommandLine -match [regex]::Escape($nextPath) -and
            $process.CommandLine -match '\b(?:start|dev)\b' -and $process.CommandLine -match '(?:--port|-p)\s+3100(?:\s|$)'
        if (!$owned) { throw '服务编号指向其他进程，未停止任何应用。' }
    }
    if ($listener.Count -and (!$process -or @($listener | Where-Object { $_.OwningProcess -ne $recordedId }).Count)) {
        throw '3100端口不属于已记录的项目网页，未停止任何应用。'
    }
    if ($process) { Stop-Process -Id $recordedId; Wait-Process -Id $recordedId -Timeout 15 -ErrorAction SilentlyContinue }
}

function Start-ProjectWeb {
    if (Get-NetTCPConnection -LocalPort 3100 -State Listen -ErrorAction SilentlyContinue) { throw '3100端口已占用，未覆盖运行进程。' }
    $started = Start-Process -FilePath $nodePath -ArgumentList "`"$nextPath`"",'start','--hostname','127.0.0.1','--port','3100' `
        -WorkingDirectory $webPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logPath 'web.log') `
        -RedirectStandardError (Join-Path $logPath 'web-error.log') -PassThru
    $started.Id | Set-Content -LiteralPath $pidPath -Encoding ascii
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $page = Invoke-WebRequest 'http://127.0.0.1:3100/' -TimeoutSec 2
            if ($page.StatusCode -eq 200 -and $page.Content -match '知行') { return }
        } catch { }
        if ($started.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    throw '网页未通过200和页面标题检查，请查看.local/web-error.log。'
}

try {
    Push-Location $webPath
    try {
        $env:NODE_OPTIONS = '--require ./scripts/use-typescript6.cjs'
        & $nodePath (Join-Path $webPath 'node_modules\typescript\bin\tsc') --noEmit *> (Join-Path $logPath 'knowledge-ui-types.log')
        if ($LASTEXITCODE -ne 0) { throw '类型检查失败，网页服务未停止；查看.local/knowledge-ui-types.log。' }
        Stop-ProjectWeb
        try {
            if (Test-Path -LiteralPath $buildPath) { Move-ProjectBuild $buildPath $previousBuild }
            & $nodePath $nextPath build --webpack *> (Join-Path $logPath 'knowledge-ui-build.log')
            if ($LASTEXITCODE -ne 0) { throw '生产构建失败。' }
            Start-ProjectWeb
            @{ release=$releaseId; previous_build=$previousBuild; deployed_at=(Get-Date).ToUniversalTime().ToString('o') } |
                ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logPath 'knowledge-ui-release.json') -Encoding utf8
            Write-Output '知识库网页已部署并通过就绪检查：http://localhost:3100'
            Write-Output "旧构建保留在：$previousBuild"
        } catch {
            $deploymentError = $_
            if (Test-Path -LiteralPath $previousBuild) {
                Stop-ProjectWeb
                if (Test-Path -LiteralPath $buildPath) { Move-ProjectBuild $buildPath $failedBuild }
                Move-ProjectBuild $previousBuild $buildPath
                Start-ProjectWeb
                Write-Output '本次部署失败，已恢复旧网页构建；新构建（若有）保留在.local供排查。'
            }
            throw $deploymentError
        }
    } finally { Pop-Location }
} finally {
    $env:NODE_OPTIONS = $oldNodeOptions
    $controlLock.Dispose()
}
