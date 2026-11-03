param([string]$Proxy = '')
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\.local\browser-skill-dependencies'))
New-Item -ItemType Directory -Force -Path $root | Out-Null
$assets = @(
    @{ Name='cli'; Tag='cli-v0.4.1'; File='bsk-v0.4.1-x86_64-pc-windows-msvc.zip'; Hash='f4a62af2dde31508188ea0c80feee11dbb62d9e7b7bf388f3f3a9856cfffb37d' },
    @{ Name='extension'; Tag='ext-v0.4.1'; File='browser-skill-extension-v0.4.1-chrome.zip'; Hash='75e38414e732679b7fe7616de9f3bb80dbb9cfcc55d9cab2566dbcebb4cd9b51' }
)
foreach ($asset in $assets) {
    $zip = Join-Path $root $asset.File
    $request = @{ Uri="https://github.com/Tencent/BrowserSkill/releases/download/$($asset.Tag)/$($asset.File)"; OutFile=$zip }
    if ($Proxy) { $request.Proxy = $Proxy }
    Invoke-WebRequest @request
    if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $asset.Hash) {
        throw 'BrowserSkill 官方发行包摘要不匹配，安装中止'
    }
    $target = Join-Path $root $asset.Name
    if (Test-Path -LiteralPath $target) { throw "目标已存在，请先检查：$target" }
    Expand-Archive -LiteralPath $zip -DestinationPath $target
}
$license = Join-Path $root 'LICENSE.BrowserSkill'
$request = @{ Uri='https://raw.githubusercontent.com/Tencent/BrowserSkill/b8f2d4f0fa35c8c7c866229e1cf3b3329ae39de5/LICENSE'; OutFile=$license }
if ($Proxy) { $request.Proxy = $Proxy }
Invoke-WebRequest @request
& (Join-Path $root 'cli\bsk.exe') --version
Write-Output "依赖准备完成：$root。尚未注册连接、启动服务或读取任何浏览器。"
