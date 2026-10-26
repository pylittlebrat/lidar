# Simulation Tech Radar 每日运行入口（Windows 计划任务用）
# 完整链路：采集 -> 分析评分 -> 日报和周报（共用同步锁）
# 用法（计划任务）：
#   powershell -ExecutionPolicy Bypass -File scripts\run_daily.ps1
$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

$techradar = Join-Path $PSScriptRoot "..\.venv\Scripts\techradar.exe"
if (-not (Test-Path $techradar)) {
  Write-Error "[techradar] 未找到 $techradar，请先执行: python -m venv .venv && .venv\Scripts\pip install -e ."
  exit 1
}

Write-Output "[techradar] daily run start: $(Get-Date -Format o)"

& $techradar run
$radarExitCode = $LASTEXITCODE
if ($radarExitCode -ne 0) { exit $radarExitCode }

Write-Output "[techradar] daily run done: $(Get-Date -Format o)"
