$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start-managed-service.ps1') -Service ask
