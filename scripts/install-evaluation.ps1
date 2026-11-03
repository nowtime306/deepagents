#Requires -Version 7
param([string]$Output)
$ErrorActionPreference = 'Stop'
trap { [Console]::Error.WriteLine('独立评测安装配置或进程异常，请核对依赖及私有输出路径。'); exit 2 }
. (Join-Path $PSScriptRoot 'evaluation-process.ps1')
foreach ($file in @('pyproject.toml','uv.lock')) {
    if (!(Test-Path -LiteralPath (Join-Path $evaluationProject $file) -PathType Leaf)) { throw '独立评测依赖契约尚未就绪，未安装任何依赖。' }
}
$directory = New-EvaluationOutput $Output
$uv = (Get-Command uv -ErrorAction Stop).Source
$code = Invoke-EvaluationProcess $uv @('--no-config','sync','--frozen','--project',$evaluationProject,'--python','3.12','--no-python-downloads') $directory 'install'
Write-Output "独立评测依赖安装退出码：$code；私有日志：$directory"
exit $code
