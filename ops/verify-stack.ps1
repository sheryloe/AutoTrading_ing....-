[CmdletBinding()]
param(
  [string]$EnvFile = ".\.env",
  [int]$LookbackHours = 1,
  [switch]$RequireBybitSync
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function To-Int {
  param($Value)
  try { return [int]$Value } catch { return 0 }
}

function Write-Kv {
  param([string]$K, [string]$V)
  Write-Host ("{0,-32} {1}" -f $K, $V)
}

function Get-Prop {
  param(
    [Parameter(Mandatory=$true)]$Object,
    [Parameter(Mandatory=$true)][string]$Name,
    $Default = $null
  )
  if ($null -eq $Object) { return $Default }
  $p = $Object.PSObject.Properties[$Name]
  if ($null -eq $p) { return $Default }
  return $p.Value
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "python command not found"
}

$jsonRaw = python .\scripts\supabase_usage_audit.py --env-file $EnvFile --lookback-hours $LookbackHours --json
if ($LASTEXITCODE -ne 0) {
  throw "supabase_usage_audit.py failed"
}

$payload = $jsonRaw | ConvertFrom-Json
if (-not $payload.ok) {
  throw "supabase_usage_audit returned ok=false"
}

$result = $payload.result
$tableStats = $result.table_stats
$hbRow = $result.heartbeat.row
$meta = $hbRow.meta_json

$hbRecent = To-Int $tableStats.engine_heartbeat.recent.count
$setupsRecent = To-Int $tableStats.model_setups.recent.count
$auditRecent = To-Int $tableStats.model_signal_audit.recent.count
$openPosRecent = To-Int $tableStats.positions.recent.count

$runner = [string](Get-Prop -Object $meta -Name "runner" -Default "")
$tradeMode = [string](Get-Prop -Object $meta -Name "trade_mode" -Default "")
$readonly = [string](Get-Prop -Object $meta -Name "bybit_readonly_sync" -Default "")
$bybitTs = [int64](To-Int (Get-Prop -Object $meta -Name "last_bybit_sync_ts" -Default 0))
$publicStatus = To-Int (Get-Prop -Object $meta -Name "bybit_preflight_public_status" -Default 0)
$authStatus = To-Int (Get-Prop -Object $meta -Name "bybit_preflight_auth_status" -Default 0)
$bybitErr = [string](Get-Prop -Object $meta -Name "bybit_preflight_error" -Default "")

Write-Kv "heartbeat.last_seen_at" ([string]$hbRow.last_seen_at)
Write-Kv "runner" $runner
Write-Kv "trade_mode" $tradeMode
Write-Kv "bybit_readonly_sync" $readonly
Write-Kv "last_bybit_sync_ts" ([string]$bybitTs)
Write-Kv "bybit_preflight_public" ([string]$publicStatus)
Write-Kv "bybit_preflight_auth" ([string]$authStatus)
Write-Kv "model_setups_recent" ([string]$setupsRecent)
Write-Kv "model_signal_audit_recent" ([string]$auditRecent)
Write-Kv "open_positions_recent" ([string]$openPosRecent)

if (-not [string]::IsNullOrWhiteSpace($bybitErr)) {
  Write-Kv "bybit_preflight_error" $bybitErr
}

$failures = New-Object System.Collections.Generic.List[string]
if ($hbRecent -lt 1) { $failures.Add("engine_heartbeat recent count is 0") }
if ($setupsRecent -lt 1) { $failures.Add("model_setups recent count is 0") }
if ($auditRecent -lt 1) { $failures.Add("model_signal_audit recent count is 0") }
if ($RequireBybitSync -and $bybitTs -le 0) { $failures.Add("last_bybit_sync_ts <= 0") }

if ($failures.Count -gt 0) {
  $msg = ($failures -join "; ")
  throw "verify-stack failed: $msg"
}

if ($bybitTs -le 0) {
  Write-Warning "Bybit sync timestamp is 0. Check bybit_preflight status/network route."
}

Write-Host "verify-stack: OK" -ForegroundColor Green
