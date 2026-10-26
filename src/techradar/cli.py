"""techradar CLI（stdlib argparse 实现，无额外依赖）。

支持：
    techradar --help
    techradar --version
    techradar config                          # 查看并校验 YAML 配置
    techradar db init [--db-path PATH]        # 初始化数据库（幂等）
    techradar db status [--db-path PATH]      # 查看数据库状态

其余命令（collect / analyze / daily / weekly / run）为 Phase 2+ 预留。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Callable, Sequence

from dotenv import load_dotenv

from techradar import __version__
from techradar.config import Config, default_data_dir, project_root


def build_parser() -> argparse.ArgumentParser:
    """构建 argparse 解析器。"""
    parser = argparse.ArgumentParser(
        prog="techradar",
        description="Simulation Tech Radar — 仿真技术情报自动采集与分析平台",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="日志级别: DEBUG/INFO/WARNING/ERROR/CRITICAL",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_config = sub.add_parser("config", help="查看并校验 YAML 配置")
    p_config.set_defaults(handler=command_config)

    p_collect = sub.add_parser("collect", help="采集 -> 归一化 -> 过滤 -> 去重 -> 入库")
    p_collect.add_argument(
        "--source",
        action="append",
        choices=["github", "arxiv", "rss"],
        default=[],
        help="只采集指定信息源（可多次使用；默认全部启用的源）",
    )
    p_collect.add_argument(
        "--limit", type=int, default=None, help="每源最多处理的原始条数（调试用）"
    )
    p_collect.add_argument("--db-path", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_collect.add_argument("--no-store", action="store_true", help="只统计不写库")
    p_collect.set_defaults(handler=command_collect)

    p_analyze = sub.add_parser(
        "analyze", help="LLM 分析与评分未处理条目并入库（Phase 4）"
    )
    p_analyze.add_argument(
        "--limit", type=int, default=None, help="最多处理的条目数（默认 config/llm.yaml）"
    )
    p_analyze.add_argument(
        "--all", action="store_true", help="忽略单次上限，扫描全部未分析条目（分级成本控制仍生效）"
    )
    p_analyze.add_argument(
        "--min-hits", type=int, default=None,
        help="调用 LLM 所需最低优先级词命中数（分级；默认读 config/llm.yaml llm_min_keyword_hits）",
    )
    p_analyze.add_argument(
        "--force", action="store_true",
        help="补全模式：重跑所有 llm_status != 'llm' 的条目（含仅关键词评分/旧数据/失败）",
    )
    p_analyze.add_argument("--db-path", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_analyze.add_argument("--dry-run", action="store_true", help="只计算不写库")
    p_analyze.set_defaults(handler=command_analyze)

    p_daily = sub.add_parser("daily", help="生成 Markdown 日报（Phase 5）")
    p_daily.add_argument("--date", default=None, help="YYYY-MM-DD（默认今天）")
    p_daily.add_argument("--days", type=int, default=1, help="统计窗口天数（默认 1）")
    p_daily.add_argument("--top", type=int, default=8, help="Top Technology 条数")
    p_daily.add_argument("--out-dir", default=None, help="输出目录（默认 reports/daily）")
    p_daily.add_argument("--db-path", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_daily.set_defaults(handler=command_daily)

    p_run = sub.add_parser("run", help="完整链路：collect -> analyze -> daily -> weekly")
    p_run.add_argument("--limit", type=int, default=None, help="collect/analyze 每步限量（调试用）")
    p_run.add_argument("--db-path", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_run.set_defaults(handler=command_run)

    p_weekly = sub.add_parser("weekly", help="生成趋势周报（Phase 6）")
    p_weekly.add_argument("--date", default=None, help="结束日期 YYYY-MM-DD（默认今天）")
    p_weekly.add_argument("--top", type=int, default=8, help="趋势/重点技术条数")
    p_weekly.add_argument("--trends", type=int, default=10, help="趋势条数")
    p_weekly.add_argument("--out-dir", default=None, help="输出目录（默认 reports/weekly）")
    p_weekly.add_argument("--db-path", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_weekly.set_defaults(handler=command_weekly)

    p_backfill = sub.add_parser(
        "backfill", help="历史回填：采集近 N 天内容入库（不污染每日统计）"
    )
    p_backfill.add_argument("--days", type=int, default=90, help="回填窗口天数（默认 90）")
    p_backfill.add_argument(
        "--source", action="append", choices=["github", "arxiv"], default=[],
        help="只回填指定源（默认全部）",
    )
    p_backfill.add_argument("--max-per-source", type=int, default=300)
    p_backfill.add_argument("--analyze", action="store_true", help="入库后立即 LLM 分析")
    p_backfill.add_argument("--db-path", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_backfill.set_defaults(handler=command_backfill)

    p_radar = sub.add_parser(
        "radar", help="技术雷达状态表：由评分推导 WATCH/INVESTIGATE/POC 并沉淀到 radar 表"
    )
    p_radar.add_argument("--days", type=int, default=90, help="扫描窗口天数")
    p_radar.add_argument("--db-path", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_radar.set_defaults(handler=command_radar)

    p_search = sub.add_parser("search", help="关键词检索知识库（Phase 8）")
    p_search.add_argument("query", help="检索关键词")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.add_argument("--db-path", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_search.set_defaults(handler=command_search)

    p_ask = sub.add_parser("ask", help="技术问答（检索 + LLM，Phase 8 RAG）")
    p_ask.add_argument("question", help="技术问题（如：最近有哪些可能替代 PhysX 的方案？）")
    p_ask.add_argument("--top", type=int, default=10, help="检索候选条数")
    p_ask.add_argument("--db-path", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_ask.set_defaults(handler=command_ask)

    p_dash = sub.add_parser("dashboard", help="启动 Web Dashboard（Phase 7，Streamlit）")
    p_dash.add_argument("--port", type=int, default=8501)
    p_dash.add_argument("--db-path", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_dash.set_defaults(handler=command_dashboard)

    p_db = sub.add_parser("db", help="SQLite 数据库管理")
    p_db.add_argument("--db-path", default=None, help="SQLite 文件路径（默认 <data>/radar.db）")
    db_sub = p_db.add_subparsers(dest="db_command", required=True)
    for name, handler, help_text in (
        ("init", command_db_init, "初始化数据库（幂等，可重复执行）"),
        ("status", command_db_status, "查看数据库状态"),
    ):
        p = db_sub.add_parser(name, help=help_text)
        # 允许 `techradar db init --db-path <path>`：子命令不覆盖父级值时保留父级值
        p.add_argument("--db-path", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        p.set_defaults(handler=handler)

    return parser


def _resolve_db_path(args: argparse.Namespace) -> str:
    """解析数据库路径：--db-path 优先，否则 <data_dir>/radar.db。"""
    db_path = getattr(args, "db_path", None)
    if db_path:
        return str(db_path)
    return str(default_data_dir() / "radar.db")


def command_weekly(args: argparse.Namespace) -> int:
    """`techradar weekly`：生成趋势周报。"""
    from datetime import date

    from techradar.config import Config
    from techradar.reports.weekly import WeeklyReportGenerator
    from techradar.storage.database import Database

    cfg = Config.load()
    db = Database(_resolve_db_path(args))
    db.init()
    from techradar.utils.time import local_today
    end_date = date.fromisoformat(args.date) if args.date else local_today()
    path = WeeklyReportGenerator(cfg, db).generate(
        end_date=end_date, top_n=args.top, trend_limit=args.trends, out_dir=args.out_dir
    )
    print(f"周报已生成: {path}")
    return 0


def command_run(args: argparse.Namespace) -> int:
    """`techradar run`：collect -> analyze -> daily -> weekly 完整链路。"""
    from techradar.config import Config
    from techradar.sync import run_sync
    from techradar.storage.database import Database

    cfg = Config.load()
    db = Database(_resolve_db_path(args))
    db.init()
    print("开始同步；若已有同步正在执行，将等待其完成。", flush=True)
    result = run_sync(cfg, db, limit=args.limit)
    print("run 汇总（collect -> analyze -> daily -> weekly）")
    print(f"  状态: {result['status']}")
    print(f"  采集入库: {sum(s.get('inserted', 0) for s in result['collect'].values())}")
    print(f"  内容更新: {sum(s.get('updated', 0) for s in result['collect'].values())}")
    print(f"  分析记录: {result['analyze'].get('stored', 0)}")
    print(f"  模型成功: {result['analyze'].get('analyzed', 0)} / 规则评分: {result['analyze'].get('skipped_llm', 0)} / 失败: {result['analyze'].get('llm_errors', 0)}")
    for kind, path in result['reports'].items():
        print(f"  {kind}: {path}")
    for warning in result['warnings']:
        print(f"  提示: {warning}")
    print(f"  运行时: {result.get('duration', 0):.1f}s")
    return 1 if result['status'] == 'failed' else 0


def command_search(args: argparse.Namespace) -> int:
    """`techradar search <词>`：知识库关键词检索。"""
    from techradar.config import Config
    from techradar.storage.database import Database
    from techradar.storage.repository import ItemRepository
    from techradar.utils.time import to_iso

    cfg = Config.load()
    db = Database(_resolve_db_path(args))
    db.init()
    items = ItemRepository(db).search(args.query, limit=args.limit)
    print(f"命中 {len(items)} 条（关键词: {args.query}）\n")
    for item in items:
        ts = to_iso(item.published_at)[:10] if item.published_at else "-"
        print(f"- [{item.title}]({item.url})  [{item.source_type}/{item.source_name}] {ts}")
    return 0


def command_ask(args: argparse.Namespace) -> int:
    """`techradar ask <问题>`：检索知识库并用 LLM 生成回答（RAG）。"""
    from techradar.config import Config
    from techradar.llm.client import LLMError
    from techradar.search import ask
    from techradar.storage.database import Database

    cfg = Config.load()
    db = Database(_resolve_db_path(args))
    db.init()
    try:
        result = ask(cfg, db, args.question, top=args.top)
    except LLMError as exc:
        print(f"LLM 调用失败: {exc}", file=sys.stderr)
        return 1
    print(f"问题: {args.question}\n")
    print(result["answer"])
    if result.get("sources"):
        print(f"\n来源（{len(result['sources'])} 条）:")
        for source in result["sources"]:
            print(f"- {source['title']}: {source['url']}")
    return 0


def command_dashboard(args: argparse.Namespace) -> int:
    """`techradar dashboard`：启动 Streamlit Web Dashboard。"""
    import subprocess
    import sys
    from pathlib import Path

    app_path = Path(__file__).resolve().parent / "dashboard" / "app.py"
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app_path),
        "--server.headless", "true",
        "--server.port", str(args.port),
        "--browser.gatherUsageStats", "false",
    ]
    print("启动 Simulation Tech Radar Dashboard: " + " ".join(cmd))
    print(f"打开 http://127.0.0.1:{args.port}（Ctrl-C 停止）\n")
    return subprocess.run(cmd).returncode


def command_collect(args: argparse.Namespace) -> int:
    """`techradar collect`：采集 -> 归一化 -> 过滤 -> 去重 -> 入库。"""
    import time

    from techradar.config import Config
    from techradar.pipeline import Pipeline
    from techradar.storage.database import Database

    cfg = Config.load()
    db = Database(_resolve_db_path(args))
    db.init()
    pipeline = Pipeline(cfg, db)

    sources = list(args.source) or None
    t0 = time.monotonic()
    stats = pipeline.collect(sources=sources, limit=args.limit, store=not args.no_store)
    runtime = time.monotonic() - t0

    print(f"\n{'=' * 52}")
    print("采集汇总（Simulation Tech Radar）")
    print(f"{'=' * 52}")
    total_collected = total_inserted = total_duplicates = total_dropped = 0
    errors = 0
    for source, entry in stats.items():
        if "error" in entry:
            errors += int(entry.get('status') != 'partial')
            print(f"  {source:<10} {entry.get('status', 'failed')}: {entry['error']}")
        total_collected += entry["collected"]
        total_inserted += entry["inserted"]
        total_duplicates += entry["duplicates"]
        total_dropped += entry["dropped"]
        print(f"  {source:<10} collected={entry['collected']:<4} "
              f"dropped={entry['dropped']:<4} duplicates={entry['duplicates']:<4} "
              f"inserted={entry['inserted']} updated={entry.get('updated',0)}")
    print(f"{'=' * 52}")
    print(f"  合计       collected={total_collected} dropped={total_dropped} "
          f"duplicates={total_duplicates} inserted={total_inserted}")
    print(f"  运行时     {runtime:.1f}s；源失败数: {errors}")
    if errors == len(stats) and errors > 0:
        return 1
    return 0


def command_analyze(args: argparse.Namespace) -> int:
    """`techradar analyze`：LLM 分析未处理条目并评分入库。"""
    import time

    from techradar.config import Config
    from techradar.llm.client import LLMError
    from techradar.pipeline import Pipeline
    from techradar.storage.database import Database

    cfg = Config.load()
    db = Database(_resolve_db_path(args))
    db.init()
    pipeline = Pipeline(cfg, db)

    t0 = time.monotonic()
    try:
        stats = pipeline.analyze(
            limit=1_000_000 if args.all else args.limit,
            dry_run=args.dry_run,
            min_hits=args.min_hits,
            force=args.force,
        )
    except LLMError as exc:
        print(f"LLM 配置/调用错误: {exc}", file=sys.stderr)
        print("请检查 .env 中的 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL", file=sys.stderr)
        return 1
    runtime = time.monotonic() - t0

    if "error" in stats:
        print(f"analyze 未执行: {stats['error']}", file=sys.stderr)
        return 1
    print(f"\n{'=' * 52}")
    print("分析汇总（Simulation Tech Radar · Phase 4）")
    print(f"{'=' * 52}")
    print(f"  待分析条目      {stats['items']}")
    print(f"  LLM 调用        {stats['llm_calls']}（失败 {stats['llm_errors']}，跳过 {stats['skipped_llm']}）")
    print(f"  tokens          {stats['tokens']}")
    if not args.dry_run:
        print(f"  已入库分析      {stats['stored']}")
    print(f"  运行时          {runtime:.1f}s")
    if args.dry_run:
        print("（--dry-run：未写库）")
    return 0


def command_daily(args: argparse.Namespace) -> int:
    """`techradar daily`：生成 Markdown 日报。"""
    from datetime import date

    from techradar.config import Config
    from techradar.reports.daily import DailyReportGenerator
    from techradar.storage.database import Database

    cfg = Config.load()
    db = Database(_resolve_db_path(args))
    db.init()
    from techradar.utils.time import local_today
    report_date = date.fromisoformat(args.date) if args.date else local_today()
    generator = DailyReportGenerator(cfg, db)
    path = generator.generate(
        report_date=report_date, days=args.days, top_n=args.top, out_dir=args.out_dir
    )
    print(f"日报已生成: {path}")
    return 0


def command_backfill(args: argparse.Namespace) -> int:
    """`techradar backfill`：近 N 天历史内容入库（不影响每日雷达统计）。"""
    import time

    from techradar.config import Config
    from techradar.pipeline import Pipeline
    from techradar.storage.database import Database

    cfg = Config.load()
    db = Database(_resolve_db_path(args))
    db.init()
    pipeline = Pipeline(cfg, db)

    t0 = time.monotonic()
    stats = pipeline.backfill(
        days=args.days,
        sources=list(args.source) or None,
        max_per_source=args.max_per_source,
    )
    runtime = time.monotonic() - t0

    print(f"\n{'=' * 56}")
    print(f"历史回填汇总（近 {args.days} 天 · 不影响每日统计）")
    print(f"{'=' * 56}")
    total_collected = total_inserted = total_duplicates = total_dropped = 0
    errors = 0
    for source, entry in stats.items():
        if "error" in entry:
            errors += int(entry.get('status') != 'partial')
            print(f"  {source:<8} {entry.get('status', 'failed')}: {entry['error']}")
        total_collected += entry["collected"]
        total_inserted += entry["inserted"]
        total_duplicates += entry["duplicates"]
        total_dropped += entry["dropped"]
        print(f"  {source:<8} collected={entry['collected']:<4} dropped={entry['dropped']:<4} "
              f"duplicates={entry['duplicates']:<4} inserted={entry['inserted']} updated={entry.get('updated',0)}")
    print(f"{'=' * 56}")
    print(f"  合计 collected={total_collected} dropped={total_dropped} "
          f"duplicates={total_duplicates} inserted={total_inserted}")
    print(f"  运行时 {runtime:.1f}s（源失败 {errors}）")
    if args.analyze:
        print("== 入库后 LLM 分析 ==")
        from techradar.llm.client import LLMError

        try:
            astats = pipeline.analyze()
            print(f"  分析: 条目 {astats.get('items')} / LLM {astats.get('llm_calls')} / "
                  f"失败 {astats.get('llm_errors')}")
        except LLMError as exc:
            print(f"  （analyze 跳过: {exc}）")
    return 1 if errors == len(stats) and errors > 0 else 0


def command_radar(args: argparse.Namespace) -> int:
    """`techradar radar`：扫描分析数据并维护技术雷达状态表。"""
    from techradar.config import Config
    from techradar.radar import RadarService
    from techradar.storage.database import Database

    cfg = Config.load()
    db = Database(_resolve_db_path(args))
    db.init()
    rows = RadarService(cfg, db).sync(days=args.days)
    print(f"\n{'=' * 64}")
    print(f"Technology Radar（近 {args.days} 天，{len(rows)} 项技术）")
    print(f"{'=' * 64}")
    print(f"{'状态':<14}{'评分':<6}{'技术':<42}{'建议'}")
    for r in rows:
        flag = {"POC": "🔴", "INVESTIGATE": "🟡", "WATCH": "⚪"}.get(r["status"], "·")
        print(f"{flag} {r['status']:<12}{r['score']:<6}{r['tech_name'][:42]:<42}{r['action'] or '-'}")
    return 0


def command_config(args: argparse.Namespace) -> int:
    """`techradar config`：加载并校验全部配置，输出摘要。"""
    cfg = Config.load()
    print(f"配置目录: {cfg.config_dir}")
    print("已加载文件:")
    for name in ("sources.yaml", "keywords.yaml", "taxonomy.yaml", "projects.yaml", "scoring.yaml"):
        print(f"  - {name}")
    categories = cfg.categories()
    print(f"taxonomy 一级分类: {len(categories)}")
    for name, terms in categories:
        print(f"  - {name:<24} {len(terms)} 关键词")
    weights = cfg.scoring.get("weights", {})
    total = sum(float(v) for v in weights.values())
    print(f"scoring 权重: {weights}（合计 {total:.3f}）")
    projects = cfg.projects.get("projects", {})
    print(f"内部关注项目: {len(projects)} -> {', '.join(projects) or '(无)'}")
    return 0


def command_db_init(args: argparse.Namespace) -> int:
    """`techradar db init`：幂等初始化数据库。"""
    from techradar.storage.database import Database

    db_path = _resolve_db_path(args)
    db = Database(db_path)
    db.init()
    print(f"数据库已初始化（幂等）: {db.db_path.resolve()}")
    return 0


def command_db_status(args: argparse.Namespace) -> int:
    """`techradar db status`：打印数据库路径、schema 版本与各表行数。"""
    from techradar.storage.database import Database

    db = Database(_resolve_db_path(args))
    status = db.status()
    print(f"数据库路径: {status['path']}")
    print(f"schema 版本: {status['schema_version'] or '(未初始化)'}")
    if "items" not in status["tables"]:
        print("提示: 数据库尚未初始化，请先运行: techradar db init")
        return 0
    print("各表行数:")
    for table, count in status["tables"].items():
        print(f"  {table:<16} {count}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口：加载 .env、解析参数、配置日志、分发命令。出错时记录日志并返回 1。"""
    load_dotenv(project_root() / ".env")  # 供 LLM_API_KEY / GITHUB_TOKEN 等使用
    parser = build_parser()
    args = parser.parse_args(argv)

    from techradar.utils.logging import get_logger, setup_logging

    level = args.log_level or os.environ.get("TECHRADAR_LOG_LEVEL", "INFO")
    setup_logging(level=level, log_file=os.environ.get("TECHRADAR_LOG_FILE"))

    try:
        handler: Callable[[argparse.Namespace], int] = args.handler
        return handler(args)
    except Exception as exc:  # 单点失败不崩溃：记录并返回非零退出码
        get_logger("techradar.cli").error("命令执行失败: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
