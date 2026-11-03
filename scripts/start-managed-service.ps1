param([Parameter(Mandatory=$true)][ValidateSet('api','ask','work','ops')][string]$Service,[ValidateRange(0,3)][int]$Slot=0,[string]$ExpectedStopMarker='')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'service-control.ps1')
if($ExpectedStopMarker -and $ExpectedStopMarker -notmatch '^backup:[0-9a-f-]{36}$'){throw '备份恢复标识无效。'}
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$apiPath = Join-Path $projectPath 'services\api'
$pythonPath = Join-Path $apiPath '.venv\Scripts\python.exe'
if($Service -in @('api','ops') -and $Slot -ne 0){throw '此服务只支持一个实例。'}
Push-Location $apiPath
try {
    $platformData = & $pythonPath -c 'from app.config import Settings; print(Settings.from_env().data_dir)'
    if ($LASTEXITCODE -ne 0) { throw '无法读取本项目运行目录。' }
} finally { Pop-Location }
$stopPath = Join-Path $platformData "services\$Service.stop"
if($ExpectedStopMarker){
    New-Item -ItemType Directory -Path (Join-Path $platformData 'services') -Force | Out-Null
    $controlLock=Open-PlatformControlLock (Join-Path $platformData "services\$Service-control.lock")
    try {
        if((Test-Path -LiteralPath $stopPath) -and (Get-Content -LiteralPath $stopPath -Raw).Trim() -ne $ExpectedStopMarker){
            Write-Output '人工停止标记已更新，保留停止状态。'; return
        }
    } finally {$controlLock.Dispose()}
}
$logPath = if ($Service -eq 'api') { Join-Path $projectPath '.local' } else { $platformData }
$pidName = if ($Service -eq 'api') {'api.pid'} else {"$Service-worker.pid"}
if($Slot -gt 0){$pidName="$Service-worker-$Slot.pid"}
$pidPath = Join-Path $logPath $pidName
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
if (Test-Path -LiteralPath $pidPath) {
    $serviceProcessId = 0
    if (![int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$serviceProcessId)) {
        throw '本项目服务编号记录无效，请先检查运行目录。'
    }
    $deadline = (Get-Date).AddSeconds(35)
    do {
        $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$serviceProcessId" -ErrorAction SilentlyContinue
        $module = @{api='server';ask='ask_server';work='work_server';ops='operations_worker'}[$Service]
        $owned = $existing -and $existing.Name -eq 'python.exe' -and $existing.ExecutablePath -eq $pythonPath -and (
            $existing.CommandLine -match " -m app\.$module(?:\s|$)" -or
            ($existing.CommandLine -match " -m app\.service_host --service $Service(?:\s|$)" -and
             ($Slot -eq 0 -or $existing.CommandLine -match " --slot $Slot(?:\s|$)")))
        if (!$owned) {break}
        if (!(Test-Path -LiteralPath $stopPath)) {Write-Output '本项目服务已运行。'; return}
        if ((Get-Date) -ge $deadline) {throw '服务仍在安全退出，请查看运行日志后再启动。'}
        Start-Sleep -Milliseconds 500
    } while ($true)
}
if ($Service -eq 'api') {
    if (Get-NetTCPConnection -State Listen -LocalPort 8100 -ErrorAction SilentlyContinue) {
        $live = Invoke-RestMethod 'http://127.0.0.1:8100/health/live' -TimeoutSec 5
        if ($live.service -ne 'owner-agent') {throw '8100端口已被其他服务占用。'}
        Write-Output '本项目API已运行。'; return
    }
} else {
    $health = Invoke-RestMethod 'http://127.0.0.1:8100/health/ready' -TimeoutSec 5
    if ($health.status -ne 'ready') {throw '请先启动本项目API。'}
}
New-Item -ItemType Directory -Path (Join-Path $platformData 'services') -Force | Out-Null
$controlLock=Open-PlatformControlLock (Join-Path $platformData "services\$Service-control.lock")
try {
    if($ExpectedStopMarker -and (Test-Path -LiteralPath $stopPath) -and (Get-Content -LiteralPath $stopPath -Raw).Trim() -ne $ExpectedStopMarker){
        Write-Output '人工停止标记已更新，保留停止状态。'; return
    }
    if(Test-Path -LiteralPath $stopPath){Remove-Item -LiteralPath $stopPath}
    $process = Start-Process -FilePath $pythonPath -ArgumentList '-m','app.service_host','--service',$Service,'--slot',"$Slot" `
        -WorkingDirectory $apiPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logPath "$Service-$Slot-host.log") `
        -RedirectStandardError (Join-Path $logPath "$Service-$Slot-host-error.log") -PassThru
    $process.Id | Set-Content -LiteralPath $pidPath -Encoding ascii
} finally {$controlLock.Dispose()}
Write-Output '服务守护已启动。后台异常退出会安全恢复，结果未知的旧任务不会自动重放。'
