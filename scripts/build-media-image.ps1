#Requires -Version 7
param([switch]$Preflight)
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ((git -C $projectPath branch --show-current).Trim() -ne 'onwer_agent') { throw '请在onwer_agent分支构建。' }
$kind = docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0 -or $kind -ne 'linux') { throw 'Docker Linux执行环境尚未就绪。' }
if ($Preflight) { Write-Output '媒体镜像前置检查通过，未构建或启停服务。'; return }
docker build --label owner-agent.component=media-decoder -t owner-agent/media:dev (Join-Path $projectPath 'infra/sandbox-images/media')
if ($LASTEXITCODE -ne 0) { throw '媒体解码镜像构建失败，未更新批准镜像。' }
$imageId = docker image inspect owner-agent/media:dev --format '{{.Id}}'
if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[0-9a-f]{64}$') { throw '无法确认镜像摘要。' }
$version = docker run --rm --network none --memory 128m --cpus 0.5 --pids-limit 32 --read-only --cap-drop ALL --security-opt no-new-privileges $imageId ffmpeg -version
if ($LASTEXITCODE -ne 0) { throw '受限镜像ffmpeg自检未通过，未更新批准镜像。' }
$localPath = Join-Path $projectPath '.local'
New-Item -ItemType Directory -Path $localPath -Force | Out-Null
@{image_id=$imageId;version=($version | Select-Object -First 1);created_at=(Get-Date).ToUniversalTime().ToString('o')} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $localPath 'media-decoder-image.json') -Encoding utf8
Write-Output '媒体解码镜像自检完成；批准的是固定镜像摘要，不依赖可变标签。未启动常驻服务。'
