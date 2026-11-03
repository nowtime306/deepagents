$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
foreach ($kind in @('coding', 'browser')) {
    $image = "owner-agent/${kind}:dev"
    $context = Join-Path $projectPath "infra\sandbox-images\$kind"
    docker build -t $image $context
    if ($LASTEXITCODE -ne 0) { throw "构建 $image 失败。" }
}
Write-Output 'Coding 和 Browser 沙箱镜像构建完成。'
