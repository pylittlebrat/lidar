#!/usr/bin/env bash
# Simulation Tech Radar 每日运行入口
# 完整链路：采集 -> 处理入库 -> LLM 分析评分 -> 生成日报
# Linux 建议 crontab：
#   0 8 * * * cd /path/to/simulation-tech-radar && bash scripts/run_daily.sh >> data/logs/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v techradar >/dev/null 2>&1; then
  echo "[techradar] 未找到 techradar 命令，请先执行: pip install -e ." >&2
  exit 1
fi

echo "[techradar] daily run start: $(date -u +%FT%TZ)"

techradar run

echo "[techradar] daily run done: $(date -u +%FT%TZ)"
