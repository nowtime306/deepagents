#Requires -Version 7
param([ValidateSet('preflight','run','report','cleanup')][string]$Action='preflight',
    [string]$TargetManifest, [string]$Dataset, [string]$Output)
$ErrorActionPreference = 'Stop'
trap { [Console]::Error.WriteLine('评测启动配置或进程异常，未标记为通过。'); exit 2 }
. (Join-Path $PSScriptRoot 'evaluation-process.ps1')
if ($Action -in @('report','cleanup') -and !$Output) { throw '报告和清理必须明确指定原批次输出目录。' }
$python = Get-EvaluationPython
$arguments = [Collections.Generic.List[string]]::new()
foreach ($value in @('-I','-m','evaluation_runner',$Action)) { $arguments.Add($value) }
if ($Action -ne 'report') {
    if (!$TargetManifest -or !(Test-Path -LiteralPath $TargetManifest -PathType Leaf)) { throw '缺少隔离目标manifest。' }
    $arguments.Add('--target-manifest'); $arguments.Add((Resolve-Path -LiteralPath $TargetManifest).Path)
}
if ($Action -in @('preflight','run')) {
    if (!$Dataset -or !(Test-Path -LiteralPath $Dataset -PathType Leaf)) { throw '缺少版本化评测集。' }
    $arguments.Add('--dataset'); $arguments.Add((Resolve-Path -LiteralPath $Dataset).Path)
}
$directory = New-EvaluationOutput $Output
$arguments.Add('--output'); $arguments.Add($directory)
$code = Invoke-EvaluationProcess $python $arguments.ToArray() $directory $Action -Authentication:($Action -ne 'report')
Write-Output "评测 $Action 退出码：$code；私有结果：$directory"
exit $code
