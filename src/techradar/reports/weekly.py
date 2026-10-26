"""周报生成器（Phase 6）：周报 = 趋势分析 + 重点技术，而非七份日报拼接。

输出 reports/weekly/YYYY-Www.md（ISO 周）。内容：
- 本周概览（新增 / 已分析 / 重点技术数）
- 趋势分析（TrendDetector：关键词 / 分类 / 来源，上升趋势优先）
- 重点技术 Top（本周高分条目）
- 新增论文 / 新仓库 / 产品更新简表
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from techradar.analyzers.trends import TrendDetector
from techradar.config import Config
from techradar.storage.database import Database
from techradar.storage.repository import AnalysisRepository, ItemRepository
from techradar.utils.logging import get_logger
from techradar.utils.time import utcnow, local_today, report_window
from techradar.reports.daily import analysis_basis, analysis_overview, analysis_status_counts, display_window

DIRECTION_TEXT = {
    "up": "明显上升",
    "new": "新出现方向",
    "flat": "平稳",
    "down": "下降",
    "insufficient": "数据不足，暂不判断涨跌",
}


class WeeklyReportGenerator:
    """周报生成器。"""

    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.db = db
        self.logger = get_logger("techradar.reports.weekly")

    def generate(
        self,
        end_date: date | None = None,
        top_n: int = 8,
        trend_limit: int = 10,
        out_dir: str | Path | None = None,
    ) -> Path:
        """生成周报并写盘、记录 reports 表，返回输出路径。"""
        end_date = end_date or local_today()
        start, end = report_window(end_date, 7)
        since, until = start.isoformat(), end.isoformat()

        arepo = AnalysisRepository(self.db)
        irepo = ItemRepository(self.db)
        entries = arepo.scored_items(since=since, until=until, limit=None)
        trends = TrendDetector(self.config, self.db).trends(days=90, limit=trend_limit, as_of=min(end, utcnow()))
        focus_band = int(self.config.scoring.get("bands", {}).get("focus", 75))
        high = [e for e in entries if (e["analysis"].get("score") or 0) >= focus_band]
        overview = {
            "by_source": irepo.count_by_source(since=since, until=until),
            "total_collected": irepo.count_since(since=since, until=until),
            "total_analyzed": len(entries),
            "high_count": len(high),
            "focus_band": focus_band,
            "window": display_window(start, end),
            "analysis_counts": analysis_status_counts(entries),
        }

        markdown = self._render(end_date, overview, high, trends, top_n=top_n)
        out_dir = Path(out_dir) if out_dir else self.default_out_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        year, week, _ = end_date.isocalendar()
        path = out_dir / f"{year}-W{week:02d}.md"
        temporary = path.with_suffix(".md.tmp")
        temporary.write_text(markdown, encoding="utf-8")
        temporary.replace(path)
        self._record(end_date, path, overview)
        self.logger.info(
            "周报已生成: %s（新增 %d / 高分 %d / 趋势 %d）",
            path, overview["total_collected"], overview["high_count"],
            sum(len(v) for v in trends.values()),
        )
        return path

    def default_out_dir(self) -> Path:
        """默认输出目录：<项目根>/reports/weekly。"""
        from techradar.config import project_root

        return project_root() / "reports" / "weekly"

    # ------------------------------------------------------------------ render
    def _render(
        self,
        end_date: date,
        overview: dict[str, Any],
        high: list[dict[str, Any]],
        trends: dict[str, list[dict[str, Any]]],
        top_n: int,
    ) -> str:
        lines: list[str] = [
            "# Simulation Technology Weekly",
            "",
            f"Date: {end_date.isoformat()}（截至周）",
            f"统计窗口（北京时间近七个自然日）: {overview['window']}",
            "",
            "## 本周概览",
            "",
            f"- 新增: {overview['total_collected']}（" +
            ", ".join(f"{src} {n}" for src, n in overview["by_source"].items()) + "）",
            f"- 已完成分析或评分: {overview['total_analyzed']}（{analysis_overview(overview['analysis_counts'])}）",
            f"- 重点技术（>={overview['focus_band']}）: {overview['high_count']}",
            "",
            "> 模型成功表示调用及结构校验通过，技术结论仍应核对原文。规则评分未执行模型技术分析；历史未知未标记分析方式。",
            "",
        ]
        flat = [
            item
            for group in ("keywords", "categories", "sources")
            for item in trends.get(group, [])
        ]
        if flat:
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append("## 趋势分析")
            lines.append("")
            for i, trend in enumerate(flat[:top_n], start=1):
                lines.extend(self._render_trend(i, trend))
        if high:
            lines.append("## 重点技术（本周高分）")
            lines.append("")
            for entry in sorted(
                high, key=lambda e: -(e["analysis"].get("score") or 0)
            )[:top_n]:
                lines.append(self._render_compact(entry))
                lines.append("")
        if not flat and not high:
            lines.append("> 本周数据不足：趋势与重点技术待积累更多分析后生成。")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _render_trend(self, index: int, trend: dict[str, Any]) -> list[str]:
        counts = trend["counts"]
        change = trend.get("change_pct")
        change_text = f"（{'↑' if change and change >= 0 else '↓'}{abs(change or 0):.0f}%）" if change else ""
        lines = [
            f"## 趋势 {index}: {trend['name']}",
            "",
            f"- 出现频率: 7d={counts['7d']} 30d={counts['30d']} 90d={counts['90d']}{change_text}",
            f"- 判断: {DIRECTION_TEXT.get(trend['direction'], trend['direction'])}",
            f"- 对比口径: 最近 7 天 {counts['7d']} 条与之前 23 天 {trend.get('previous_count', 0)} 条的日均频率；历史样本跨度 {trend.get('observed_days', 0):.0f} 天",
        ]
        projects = self._related_projects(trend["name"])
        if projects:
            lines.append(f"- 涉及内部项目: {', '.join(projects)}")
        lines.append("")
        return lines

    def _related_projects(self, term: str) -> list[str]:
        """趋势词与 config/projects.yaml 兴趣词做包含匹配，提示涉及哪些内部项目。"""
        matched: list[str] = []
        term_l = term.lower()
        for key, profile in self.config.projects.get("projects", {}).items():
            interests = [str(i).lower() for i in profile.get("interests", [])]
            if any(term_l in interest or interest in term_l for interest in interests):
                matched.append(key)
        return matched

    def _render_compact(self, entry: dict[str, Any]) -> str:
        item = entry["item"]
        analysis = entry["analysis"]
        score = round(analysis.get("score") or 0)
        cats = ", ".join(analysis.get("categories", [])) or "-"
        return f"- [{item.title}]({item.url}) — 评分 {score}｜{cats}｜{analysis_basis(analysis)}"

    # ------------------------------------------------------------------ record
    def _record(self, end_date: date, path: Path, overview: dict[str, Any]) -> None:
        conn = self.db.connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO reports (kind, report_date, path, summary)"
                    " VALUES ('weekly', ?, ?, ?) ON CONFLICT(kind,path) DO UPDATE SET report_date=excluded.report_date,summary=excluded.summary,created_at=datetime('now')",
                    (
                        end_date.isoformat(),
                        str(path.resolve()),
                        f"新增 {overview['total_collected']} / 分析 {overview['total_analyzed']}"
                        f" / 重点 {overview['high_count']} / {analysis_overview(overview['analysis_counts'])}",
                    ),
                )
        finally:
            conn.close()
