[CmdletBinding()]
param(
  [string]$Repo = "sheryloe/Automethemoney",
  [string]$RunnerRoot = "",
  [string]$RunnerName = "",
  [string]$Labels = "automethemoney,windows,x64",
  [switch]$ReplaceExisting
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step {
  param([string]$Message)
  Write-Host "[setup-runner] $Message" -ForegroundColor Cyan
}

function Assert-Command {
  param([string]$Name)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Required command not found: $Name"
  }
}

function Test-IsAdmin {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
  throw "Run this script in an elevated PowerShell session (Run as Administrator)."
}

Assert-Command -Name "gh"

$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($RunnerRoot)) {
  $RunnerRoot = Join-Path $repoRoot ".runner\actions-runner"
}
if ([string]::IsNullOrWhiteSpace($RunnerName)) {
  $RunnerName = "$env:COMPUTERNAME-automethemoney"
}

New-Item -ItemType Directory -Path $RunnerRoot -Force | Out-Null

Write-Step "Target repo: $Repo"
Write-Step "Runner root: $RunnerRoot"
Write-Step "Runner name: $RunnerName"
Write-Step "Runner labels: $Labels"

Push-Location $RunnerRoot
try {
  $configCmd = Join-Path $RunnerRoot "config.cmd"
  if (-not (Test-Path $configCmd)) {
    Write-Step "Downloading latest GitHub Actions runner"
    $releaseRaw = gh api "repos/actions/runner/releases/latest"
    $release = $releaseRaw | ConvertFrom-Json
    $tag = [string]$release.tag_name
    if ([string]::IsNullOrWhiteSpace($tag)) {
      throw "Unable to resolve latest actions runner release tag"
    }
    $version = $tag.TrimStart("v")
    $assetName = "actions-runner-win-x64-$version.zip"
    $asset = $release.assets | Where-Object { $_.name -eq $assetName } | Select-Object -First 1
    if (-not $asset) {
      throw "Runner asset not found: $assetName"
    }

    $zipPath = Join-Path $RunnerRoot $assetName
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $RunnerRoot -Force
    Remove-Item -Path $zipPath -Force
  }

  if ($ReplaceExisting -and (Test-Path (Join-Path $RunnerRoot ".runner"))) {
    Write-Step "Removing existing runner registration"
    $removeToken = gh api -X POST "repos/$Repo/actions/runners/remove-token" --jq ".token"
    if ([string]::IsNullOrWhiteSpace($removeToken)) {
      throw "Failed to get remove token from GitHub API"
    }
    & .\config.cmd remove --token $removeToken
    if ($LASTEXITCODE -ne 0) {
      throw "config.cmd remove failed with exit code $LASTEXITCODE"
    }
  }

  if (-not (Test-Path (Join-Path $RunnerRoot ".runner"))) {
    Write-Step "Registering runner"
    $regToken = gh api -X POST "repos/$Repo/actions/runners/registration-token" --jq ".token"
    if ([string]::IsNullOrWhiteSpace($regToken)) {
      throw "Failed to get registration token from GitHub API"
    }

    $args = @(
      "--url", "https://github.com/$Repo",
      "--token", $regToken,
      "--name", $RunnerName,
      "--labels", $Labels,
      "--work", "_work",
      "--unattended"
    )
    if ($ReplaceExisting) {
      $args += "--replace"
    }

    & .\config.cmd @args
    if ($LASTEXITCODE -ne 0) {
      throw "config.cmd registration failed with exit code $LASTEXITCODE"
    }
  }
  else {
    Write-Step "Runner already registered; skipping config"
  }

  Write-Step "Installing and starting runner service"
  & .\svc.cmd install
  if ($LASTEXITCODE -ne 0) {
    Write-Step "svc.cmd install returned exit code $LASTEXITCODE (service may already exist)"
  }

  & .\svc.cmd start
  if ($LASTEXITCODE -ne 0) {
    throw "svc.cmd start failed with exit code $LASTEXITCODE"
  }

  Write-Step "Done. Verify from GitHub: Settings > Actions > Runners"
}
finally {
  Pop-Location
}