# Shared ownership checks; does not read user browser profiles or change environment files.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Get-BrowserSkillRoot([string]$DataDir) {
    if (!$DataDir) { $DataDir = $env:PLATFORM_DATA_DIR }
    if (!$DataDir) {
        $api = Join-Path $PSScriptRoot '..\services\api'
        Push-Location $api
        try {
            $DataDir = & (Join-Path $api '.venv\Scripts\python.exe') -c 'from app.config import Settings; print(Settings.from_env().data_dir)'
            if ($LASTEXITCODE -ne 0) { throw '无法读取项目数据目录，请指定 -DataDir。' }
        } finally { Pop-Location }
    }
    return [IO.Path]::GetFullPath((Join-Path $DataDir 'browser-skill'))
}
function Read-BrowserSkillJson([string]$Path) { Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json }
function Assert-BrowserSkillProcess($State, [string]$Root) {
    if ($State.root -ne $Root) { throw '运行记录不属于此目录。' }
    $helper = Get-CimInstance Win32_Process -Filter "ProcessId=$($State.helper_pid)"
    $launcher = Join-Path $PSScriptRoot 'browser-skill-launcher.mts'
    if (!$helper -or $helper.Name -ne 'node.exe' -or !$helper.CommandLine.Contains($launcher) -or
        !$helper.CommandLine.Contains((Join-Path $Root 'launch.json'))) { throw '监督进程归属检查失败；未终止任何进程。' }
    foreach ($kind in @('daemon','browser')) {
        $processId = if ($kind -eq 'daemon') { $State.daemon_pid } else { $State.browser_pid }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId"
        $executable = if ($kind -eq 'daemon') { $State.executable } else { $State.chromium }
        $token = if ($kind -eq 'daemon') { 'daemon start --foreground --port 52983' } else { '--user-data-dir=' + (Join-Path $Root 'profile') }
        if (!$process -or $process.ExecutablePath -ne $executable -or $process.ParentProcessId -ne $State.helper_pid -or
            !$process.CommandLine.Contains($token)) { throw "专用$kind 进程归属检查失败；未终止任何进程。" }
    }
    $manifest = Read-BrowserSkillJson (Join-Path $State.extension 'manifest.json')
    $daemon = Read-BrowserSkillJson (Join-Path $Root 'home\daemon.json')
    if ($manifest.version -ne '0.4.1' -or $daemon.pid -ne $State.daemon_pid -or $daemon.ws_port -ne 52983) { throw '扩展或daemon归属记录不符。' }
    foreach ($port in @(52983,52982)) {
        $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop)
        $expected = if ($port -eq 52983) { $State.daemon_pid } else { $State.browser_pid }
        if (!$listeners.Count -or @($listeners | Where-Object { $_.OwningProcess -ne $expected -or $_.LocalAddress -notin @('127.0.0.1','::1') }).Count) { throw "专用端口归属或回环限制失败：$port" }
    }
}
