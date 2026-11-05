#Requires -Version 7
param([Parameter(Mandatory)][string]$Dataset, [Parameter(Mandatory)][string]$Output,
    [Parameter(Mandatory)][string]$AdminConnectionFile, [string]$ManagerKeyFile,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{40}$')][string]$CandidateCommit, [Parameter(Mandatory)][string]$RulesId,
    [Parameter(Mandatory)][ValidateRange(1,86400)][int]$MaxSeconds,
    [Parameter(Mandatory)][ValidateRange(0,100000)][int]$MaxModelCalls,
    [Parameter(Mandatory)][ValidateRange(0,100000000)][int]$MaxTokens,
    [Parameter(Mandatory)][ValidateRange(0,1000)][int]$MaxSandboxes,
    [string[]]$CaseId, [ValidateRange(1,3)][int]$RepeatCount,
    [ValidateSet(1,2)][int]$ProfileVersion=1)
$ErrorActionPreference='Stop'
trap { [Console]::Error.WriteLine('Suite配置或进程异常，未放行。'); exit 2 }
. (Join-Path $PSScriptRoot 'evaluation-process.ps1')
$directory=New-EvaluationOutput $Output
$arguments=@('-I',(Join-Path $PSScriptRoot 'evaluation-suite.py'),'--dataset',(Resolve-Path -LiteralPath $Dataset).Path,
    '--profile-version',"$ProfileVersion",'--output',$directory,'--admin-connection-file',(Resolve-Path -LiteralPath $AdminConnectionFile).Path,
    '--candidate-commit',$CandidateCommit,'--rules-id',$RulesId,'--max-seconds',"$MaxSeconds",
    '--max-model-calls',"$MaxModelCalls",'--max-tokens',"$MaxTokens",'--max-sandboxes',"$MaxSandboxes")
if($ManagerKeyFile){$arguments+=@('--manager-key-file',(Resolve-Path -LiteralPath $ManagerKeyFile).Path)}
foreach($case in $CaseId){$arguments+=@('--case-id',$case)}
if($PSBoundParameters.ContainsKey('RepeatCount')){$arguments+=@('--repeat-count',"$RepeatCount")}
$code=Invoke-EvaluationProcess (Get-EvaluationPython) $arguments $directory 'suite'
Write-Output "隔离Suite退出码：$code；私有JSON/JUnit：$directory"
exit $code
