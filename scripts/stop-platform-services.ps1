param([ValidateSet('api','ask','work','ops','all')][string]$Service='all',[string]$OwnerToken='')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'service-control.ps1')
if($OwnerToken -and $OwnerToken -notmatch '^[0-9a-f-]{36}$'){throw '备份停止标识无效。'}
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$apiPath = Join-Path $projectPath 'services\api'
Push-Location $apiPath
try {
    $platformData = & (Join-Path $apiPath '.venv\Scripts\python.exe') -c 'from app.config import Settings; print(Settings.from_env().data_dir)'
    if ($LASTEXITCODE -ne 0) {throw '无法读取本项目运行目录。'}
} finally {Pop-Location}
$services = if ($Service -eq 'all') {@('ops','api','ask','work')} else {@($Service)}
New-Item -ItemType Directory -Path (Join-Path $platformData 'services') -Force | Out-Null
foreach ($kind in $services) {
    $controlLock=Open-PlatformControlLock (Join-Path $platformData "services\$kind-control.lock")
    try {
        $marker=if($OwnerToken){"backup:$OwnerToken"}else{(Get-Date).ToUniversalTime().ToString('o')}
        $marker | Set-Content -LiteralPath (Join-Path $platformData "services\$kind.stop") -Encoding ascii
    } finally {$controlLock.Dispose()}
}
$deadline = (Get-Date).AddSeconds(40)
do {
    $remaining = @()
    foreach ($kind in $services) {
        $pidPath = if ($kind -eq 'api') {Join-Path $projectPath '.local\api.pid'} else {Join-Path $platformData "$kind-worker.pid"}
        $serviceProcessId = 0
        if ((Test-Path -LiteralPath $pidPath) -and ![int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$serviceProcessId)) {throw '服务编号记录无效。'}
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$serviceProcessId" -ErrorAction SilentlyContinue
        $module = @{api='server';ask='ask_server';work='work_server';ops='operations_worker'}[$kind]
        if ($process -and $process.Name -eq 'python.exe' -and
            $process.ExecutablePath -eq (Join-Path $apiPath '.venv\Scripts\python.exe') -and
            ($process.CommandLine -match " -m app\.service_host --service $kind(?:\s|$)" -or
             $process.CommandLine -match " -m app\.$module(?:\s|$)")) {$remaining += $kind}
        foreach ($file in Get-ChildItem -LiteralPath (Join-Path $platformData 'services') -Filter "$kind-*.json" -File) {
            $record = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
            foreach ($identifier in @($record.host_pid,$record.child_pid)) {
                if (!$identifier) {continue}
                $recordedId = [int]$identifier
                $recorded = Get-CimInstance Win32_Process -Filter "ProcessId=$recordedId" -ErrorAction SilentlyContinue
                if ($recorded -and $recorded.Name -eq 'python.exe' -and (
                    $recorded.CommandLine -match " -m app\.service_host --service $kind(?:\s|$)" -or
                    ($recorded.ExecutablePath -eq (Join-Path $apiPath '.venv\Scripts\python.exe') -and
                     $recorded.CommandLine -match " -m app\.$module(?:\s|$)"))) {$remaining += $kind}
            }
        }
    }
    if (!$remaining.Count) {break}
    if ((Get-Date) -ge $deadline) {throw '仍有本项目服务在退出，请查看日志；未停止其他应用。'}
    Start-Sleep -Milliseconds 500
} while ($true)
Write-Output '所选平台后台已停止。再次启动请使用本项目启动脚本。'
