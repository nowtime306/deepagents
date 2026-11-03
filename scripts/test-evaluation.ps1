#Requires -Version 7
param([string]$Output)
$ErrorActionPreference = 'Stop'
trap { [Console]::Error.WriteLine('执行器工程测试配置或进程异常，未标记为通过。'); exit 2 }
. (Join-Path $PSScriptRoot 'evaluation-process.ps1')
$python = Get-EvaluationPython
$directory = New-EvaluationOutput $Output
$tests = Join-Path $evaluationProject 'tests'
if (!(Test-Path -LiteralPath $tests -PathType Container)) { throw '评测执行器工程测试尚未就绪。' }
$code = Invoke-EvaluationProcess $python @('-I','-m','pytest',$tests,'-q',('--junitxml=' + (Join-Path $directory 'results.xml'))) $directory 'tests'
@{scope='runner-engineering-tests';exit_code=$code;status=$(if ($code -eq 0) {'passed'} else {'failed'});
    business_dataset_acceptance=$false;external_models=$false} | ConvertTo-Json |
    Set-Content -LiteralPath (Join-Path $directory 'ci-result.json') -Encoding utf8
Write-Output "执行器工程测试退出码：$code；私有JSON/JUnit：$directory"
exit $code
