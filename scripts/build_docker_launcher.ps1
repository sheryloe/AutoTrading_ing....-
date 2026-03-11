Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

py -3 -m pip install --upgrade pip pyinstaller

py -3 -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --name AI_Auto_Docker_Start `
  .\scripts\docker_start.py

Write-Host ""
Write-Host "Built: $root\dist\AI_Auto_Docker_Start.exe"
