[CmdletBinding()]
param(
  [string]$Repo = "sheryloe/Automethemoney",
  [string]$Workflow = "cloud-cycle.yml",
  [string]$Ref = "main",
  [int]$WatchTimeoutSeconds = 420,
  [switch]$ShowLog
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step {
  param([string]$Message)
  Write-Host "[run-once-hosted] $Message" -ForegroundColor Cyan
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
  throw "gh CLI is required"
}

$startUtc = (Get-Date).ToUniversalTime()
Write-Step "Dispatch workflow: $Workflow (ref=$Ref)"
gh workflow run $Workflow --repo $Repo --ref $Ref | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "gh workflow run failed"
}

$run = $null
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Seconds 2
  $runsRaw = gh run list --repo $Repo --workflow $Workflow --limit 20 --json databaseId,createdAt,event,status,conclusion,headBranch,url
  if ($LASTEXITCODE -ne 0) {
    throw "gh run list failed"
  }
  $runs = $runsRaw | ConvertFrom-Json
  $run = $runs |
    Where-Object {
      $_.event -eq "workflow_dispatch" -and
      $_.headBranch -eq $Ref -and
      ([DateTime]$_.createdAt).ToUniversalTime() -ge $startUtc.AddMinutes(-1)
    } |
    Sort-Object { [DateTime]$_.createdAt } -Descending |
    Select-Object -First 1

  if ($run) { break }
}

if (-not $run) {
  throw "Dispatched run was not found. Check Actions tab manually."
}

$runId = [string]$run.databaseId
Write-Step "Run id: $runId"
Write-Step "Run url: $($run.url)"

$watchArgs = @("run", "watch", $runId, "--repo", $Repo, "--interval", "5", "--exit-status")
if ($WatchTimeoutSeconds -gt 0) {
  $watchArgs += @("--timeout", [string]$WatchTimeoutSeconds)
}
& gh @watchArgs
$watchExit = $LASTEXITCODE

$detailRaw = gh run view $runId --repo $Repo --json status,conclusion,url,createdAt,updatedAt,jobs
if ($LASTEXITCODE -ne 0) {
  throw "gh run view failed"
}
$detail = $detailRaw | ConvertFrom-Json

Write-Host "status: $($detail.status)"
Write-Host "conclusion: $($detail.conclusion)"
Write-Host "url: $($detail.url)"

if ($ShowLog) {
  gh run view $runId --repo $Repo --log
}

if ($watchExit -ne 0) {
  throw "Workflow run failed or timed out (exit=$watchExit)"
}