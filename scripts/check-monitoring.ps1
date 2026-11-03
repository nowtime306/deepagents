#Requires -Version 7
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$composePath = Join-Path $projectPath 'infra\monitoring\compose.yml'
$privatePath = Join-Path $projectPath '.local\monitoring'
$apiPath = 'http://127.0.0.1:8100/api/v1/internal/observability/metrics'
$token = (Get-Content -LiteralPath (Join-Path $projectPath 'services\api\.local\monitoring\metrics.token') -Raw).Trim()
$metrics = Invoke-WebRequest $apiPath -Headers @{Authorization="Bearer $token"} -TimeoutSec 10
if ($metrics.StatusCode -ne 200 -or $metrics.Content -notmatch 'owner_agent_sample_timestamp_seconds') { throw '平台授权metrics未就绪。' }
$denied = Invoke-WebRequest $apiPath -SkipHttpErrorCheck -TimeoutSec 5
if ($denied.StatusCode -notin @(401,403)) { throw '指标接口没有拒绝匿名访问。' }
$raw = docker compose -p owner-agent-monitoring -f $composePath exec -T prometheus wget -q -O - 'http://127.0.0.1:9090/api/v1/targets'
if ($LASTEXITCODE -ne 0) { throw 'Prometheus未就绪。' }
$targets = $raw | ConvertFrom-Json
$target = @($targets.data.activeTargets | Where-Object { $_.labels.job -eq 'owner-agent' })
if ($target.Count -ne 1 -or $target[0].health -ne 'up') { throw '平台指标尚未被Prometheus成功采集。' }
$password = (Get-Content -LiteralPath (Join-Path $privatePath 'grafana.admin') -Raw).Trim()
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("admin:$password"))
$headers = @{Authorization="Basic $encoded"}
$dashboard = Invoke-RestMethod 'http://127.0.0.1:3300/api/dashboards/uid/owner-agent-platform' -Headers $headers -TimeoutSec 10
if ($dashboard.dashboard.uid -ne 'owner-agent-platform' -or $dashboard.dashboard.panels.Count -lt 10) { throw '中文监控看板尚未导入。' }
$anonymous = Invoke-WebRequest 'http://127.0.0.1:3300/api/dashboards/uid/owner-agent-platform' -SkipHttpErrorCheck -TimeoutSec 5
if ($anonymous.StatusCode -ne 401) { throw 'Grafana未拒绝匿名读取平台看板。' }
$rules = docker compose -p owner-agent-monitoring -f $composePath exec -T prometheus wget -q -O - 'http://127.0.0.1:9090/api/v1/rules'
if ($LASTEXITCODE -ne 0) { throw 'Prometheus告警规则未就绪。' }
$groups = ($rules | ConvertFrom-Json).data.groups
if (!@($groups | Where-Object { $_.name -eq 'owner-agent-platform' -and $_.rules.Count -ge 6 }).Count) { throw '平台告警规则尚未导入。' }
@{metrics='authorized';scrape='up';dashboard='provisioned';rules='loaded';grafana='login-required'} | ConvertTo-Json -Compress
