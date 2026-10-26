#!/usr/bin/env bash
# Simulation Tech Radar 每周运行入口（周一趋势分析 + 周报）
# Phase 6 起接入：trend detector + weekly report
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v techradar >/dev/null 2>&1; then
  echo "[techradar] 未找到 techradar 命令，请先执行: pip install -e ." >&2
  exit 1
fi

echo "[techradar] weekly run start: $(date -u +%FT%TZ)"
techradar weekly
echo "[techradar] weekly run done: $(date -u +%FT%TZ)"
