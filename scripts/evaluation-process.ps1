# Shared only by the independent evaluation entry points.
$evaluationRepository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$evaluationProject = Join-Path $evaluationRepository 'services/evaluation'

function New-EvaluationOutput([string]$Path) {
    $base = Join-Path $evaluationRepository '.local/agent-evaluations'
    $target = if ($Path) { [IO.Path]::GetFullPath($Path) } else { Join-Path $base ([guid]::NewGuid().ToString('N')) }
    if (!$target.StartsWith($base + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw '评测输出必须位于本项目 .local/agent-evaluations 的独立子目录。'
    }
    $ancestor = $target
    while ($ancestor) {
        if ((Test-Path -LiteralPath $ancestor) -and ((Get-Item -LiteralPath $ancestor).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw '评测输出路径包含链接，未写入。'
        }
        $ancestor = Split-Path -Parent $ancestor
    }
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    if ($IsWindows) {
        $account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        & icacls $target /inheritance:r /grant:r "${account}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' *> $null
        if ($LASTEXITCODE -ne 0) { throw '评测目录私有权限设置失败。' }
    } else {
        & chmod 700 $target
        if ($LASTEXITCODE -ne 0) { throw '评测目录私有权限设置失败。' }
    }
    return $target
}

function Invoke-EvaluationProcess([string]$Executable, [string[]]$Arguments, [string]$Output, [string]$Label, [switch]$Authentication, [switch]$ParentDelegation) {
    $logId = $Label + '-' + [guid]::NewGuid().ToString('N')
    $info = [Diagnostics.ProcessStartInfo]::new($Executable)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WorkingDirectory = $Output
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.Environment.Clear()
    foreach ($name in @('PATH','SystemRoot','WINDIR','COMSPEC','PATHEXT','TEMP','TMP')) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) { $info.Environment[$name] = $value }
    }
    foreach ($name in @('HOME','USERPROFILE','APPDATA','LOCALAPPDATA','DEEPEVAL_HOME','ENV_DIR_PATH')) {
        $info.Environment[$name] = $Output
    }
    $temporary = Join-Path $Output 'tmp'
    foreach ($name in @('tmp','deepeval-results','deepeval-cache')) {
        $managed = Join-Path $Output $name
        if ((Test-Path -LiteralPath $managed) -and ((Get-Item -LiteralPath $managed).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw '评测缓存或临时路径包含链接，未启动。'
        }
    }
    New-Item -ItemType Directory -Path $temporary -Force | Out-Null
    $info.Environment['TEMP'] = $temporary
    $info.Environment['TMP'] = $temporary
    $info.Environment['DEEPEVAL_TELEMETRY_OPT_OUT'] = '1'
    $info.Environment['DEEPEVAL_DISABLE_DOTENV'] = '1'
    $info.Environment['DEEPEVAL_RESULTS_FOLDER'] = Join-Path $Output 'deepeval-results'
    $info.Environment['DEEPEVAL_CACHE_FOLDER'] = Join-Path $Output 'deepeval-cache'
    $info.Environment['PYTHONNOUSERSITE'] = '1'
    $info.Environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    $info.Environment['UV_PROJECT_ENVIRONMENT'] = Join-Path $evaluationProject '.venv'
    if ($Authentication) {
        foreach ($name in @('EVALUATION_API_TOKEN','EVALUATION_USERNAME','EVALUATION_PASSWORD')) {
            $value = [Environment]::GetEnvironmentVariable($name)
            if ($value) { $info.Environment[$name] = $value }
        }
    }
    if ($ParentDelegation) {
        foreach ($name in @('EVALUATION_PLATFORM_PERMIT_ORIGIN','EVALUATION_PLATFORM_PERMIT_SECRET')) {
            $value = [Environment]::GetEnvironmentVariable($name)
            if (!$value) { throw '父批次执行授权未完整转发，未启动目标。' }
            $info.Environment[$name] = $value
        }
    }
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    try { $process = [Diagnostics.Process]::Start($info) }
    catch {
        [IO.File]::WriteAllText((Join-Path $Output "$logId-error.log"), 'PROCESS_START_FAILED')
        return 3
    }
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        [IO.File]::WriteAllText((Join-Path $Output "$logId.log"), $stdout.GetAwaiter().GetResult())
        [IO.File]::WriteAllText((Join-Path $Output "$logId-error.log"), $stderr.GetAwaiter().GetResult())
        return $process.ExitCode
    } finally { $process.Dispose() }
}

function Get-EvaluationPython {
    $python = if ($IsWindows) { Join-Path $evaluationProject '.venv/Scripts/python.exe' } else { Join-Path $evaluationProject '.venv/bin/python' }
    if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw '请先安装独立评测环境，未使用生产API环境。' }
    return $python
}
