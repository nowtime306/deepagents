#Requires -Version 7
param([Parameter(Mandatory)][ValidateSet('start','status','stop','cleanup')][string]$Action,
    [Parameter(Mandatory)][string]$StateDirectory, [string]$AdminConnectionFile, [string]$ManagerKeyFile, [guid]$ParentRunId = [guid]::Empty,
    [ValidateSet(1,2)][int]$ProfileVersion = 1,[ValidateSet('AET-A05','AET-W07','AET-W08','AET-F07')][string]$CaseId)
$ErrorActionPreference = 'Stop'
trap { [Console]::Error.WriteLine('隔离评测目标操作失败，未接管既有服务。'); exit 3 }
. (Join-Path $PSScriptRoot 'evaluation-process.ps1')
$python = Join-Path $evaluationRepository 'services/api/.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw '现有API依赖环境尚未安装。' }
if ($Action -in @('start','cleanup') -and (!$AdminConnectionFile -or !(Test-Path -LiteralPath $AdminConnectionFile -PathType Leaf))) {
    throw '创建/回收测试库需要明确的私有管理连接文件，未读取任何.env。'
}
$state = New-EvaluationOutput $StateDirectory
$arguments = @('-I',(Join-Path $PSScriptRoot 'evaluation-target.py'),$Action,'--state',$state)
if ($Action -eq 'start' -or $PSBoundParameters.ContainsKey('ProfileVersion')) { $arguments += @('--profile-version',[string]$ProfileVersion) }
if ($CaseId) { $arguments += @('--case-id',$CaseId) }
if ($ParentRunId -ne [guid]::Empty) { $arguments += @('--parent-run-id', $ParentRunId.ToString()) }
if ($AdminConnectionFile) { $arguments += @('--admin-connection-file',(Resolve-Path -LiteralPath $AdminConnectionFile).Path) }
if ($ManagerKeyFile) {
    if (!(Test-Path -LiteralPath $ManagerKeyFile -PathType Leaf)) { throw '明确指定的受管沙箱密钥文件不存在。' }
    $arguments += @('--manager-key-file',(Resolve-Path -LiteralPath $ManagerKeyFile).Path)
}
$parentMarker = Join-Path (Split-Path (Split-Path $state -Parent) -Parent) 'platform-parent.json'
$parentDelegation = $Action -eq 'start' -and $ParentRunId -ne [guid]::Empty
if ($parentDelegation -and !(Test-Path -LiteralPath $parentMarker -PathType Leaf)) { throw '父批次必需标记缺失，未启动目标。' }
$code = Invoke-EvaluationProcess $python $arguments $state ('target-' + $Action) -ParentDelegation:$parentDelegation
Write-Output "隔离目标 $Action 退出码：$code；私有状态：$state"
exit $code
