"""日报生成器（Phase 5）：生成 reports/daily/YYYY-MM-DD.md。

结构（方案第 19 节）：
- 今日概览（近 N 天采集/分析/高分统计）
- 🔥 Top Technology（按相关性评分 Top N，含摘要/核心变化/影响/建议）
- Papers / New GitHub Projects / Product Updates / Engineering Machinery 分组列表
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from techradar.config import Config
from techradar.storage.database import Database
from techradar.storage.repository import AnalysisRepository, ItemRepository
from techradar.utils.logging import get_logger
from techradar.utils.time import BEIJING, local_today, report_window


def analysis_status_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    """Count analysis methods only for entries inside this report's window."""
    counts = {"llm": 0, "skipped": 0, "legacy": 0}
    for entry in entries:
        status = entry["analysis"].get("llm_status")
        counts[status if status in ("llm", "skipped") else "legacy"] += 1
    return counts


def analysis_basis(analysis: dict[str, Any]) -> str:
    return {"llm": "模型分析", "skipped": "规则评分", "llm_failed": "失败待重试"}.get(
        analysis.get("llm_status"), "历史未知（分析方式未标记）"
    )


def analysis_overview(counts: dict[str, int]) -> str:
    return f"模型成功 {counts['llm']} / 规则评分 {counts['skipped']} / 历史未知 {counts['legacy']}"


def evidence_note(analysis: dict[str, Any]) -> str:
    breakdown = analysis.get("breakdown") or {}
    evidence = breakdown.get("evidence", {}) if isinstance(breakdown, dict) else {}
    if not isinstance(evidence, dict):
        return "采集文本质量未标记"
    return evidence.get("note") or {
        "available": "采集文本可用，技术结论需核对原文",
        "limited": "采集文本有限，暂作线索",
        "missing": "缺少摘要或正文，仅作线索",
    }.get(evidence.get("quality"), "采集文本质量未标记")


def display_window(start, end) -> str:
    return f"{start.astimezone(BEIJING):%Y-%m-%d %H:%M} ~ {end.astimezone(BEIJING):%Y-%m-%d %H:%M}（结束时间不含）"

SECTION_TITLES = {
    "papers": "## Papers（论文）",
    "github_new": "## New GitHub Projects（新发现仓库）",
    "product_updates": "## Product Updates（产品/版本更新）",
    "engineering": "## Engineering Machinery（工程机械）",
}


class DailyReportGenerator:
    """把数据库中的已分析条目渲染成当日 Markdown 日报。"""

    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.db = db
        self.logger = get_logger("techradar.reports.daily")

    def generate(
        self,
        report_date: date | None = None,
        days: int = 1,
        top_n: int = 8,
        out_dir: str | Path | None = None,
    ) -> Path:
        """生成日报并写盘、记录 reports 表，返回输出路径。"""
        report_date = report_date or local_today()
        start, end = report_window(report_date, days)
        since, until = start.isoformat(), end.isoformat()

        arepo = AnalysisRepository(self.db)
        irepo = ItemRepository(self.db)
        entries = arepo.scored_items(since=since, until=until, limit=None)

        by_source = irepo.count_by_source(since=since, until=until)
        total_collected = sum(by_source.values())
        focus_band = int(self.config.scoring.get("bands", {}).get("focus", 75))
        high_count = sum(
            1 for e in entries if (e["analysis"].get("score") or 0) >= focus_band
        )
        overview = {
            "by_source": by_source,
            "total_collected": total_collected,
            "total_analyzed": len(entries),
            "high_count": high_count,
            "focus_band": focus_band,
            "window": display_window(start, end),
            "analysis_counts": analysis_status_counts(entries),
        }

        markdown = self._render(report_date, overview, entries, top_n=top_n)
        out_dir = Path(out_dir) if out_dir else self.default_out_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{report_date.isoformat()}.md"
        temporary = path.with_suffix(".md.tmp")
        temporary.write_text(markdown, encoding="utf-8")
        temporary.replace(path)
        self._record(report_date, path, overview)
        self.logger.info("日报已生成: %s（%d 条目，%d 高分）", path, total_collected, high_count)
        return path

    def default_out_dir(self) -> Path:
        """默认输出目录：<项目根>/reports/daily。"""
        from techradar.config import project_root

        return project_root() / "reports" / "daily"

    # ------------------------------------------------------------------ render
    def _render(
        self, report_date: date, overview: dict[str, Any], entries: list[dict[str, Any]], top_n: int
    ) -> str:
        lines: list[str] = [
            "# Simulation Technology Daily",
            "",
            f"Date: {report_date.isoformat()}",
            f"统计窗口（北京时间自然日）: {overview['window']}",
            "",
            "## 今日概览",
            "",
            f"- 新增采集（近窗口）: {overview['total_collected']}（" +
            ", ".join(f"{src} {n}" for src, n in overview["by_source"].items()) + "）",
            f"- 已完成分析或评分: {overview['total_analyzed']}（{analysis_overview(overview['analysis_counts'])}）",
            f"- 高相关（>={overview.get('focus_band', 75)}）: {overview['high_count']}",
            "",
            "> 模型成功表示调用及结构校验通过，技术结论仍应核对原文。规则评分未执行模型技术分析；历史未知未标记分析方式。",
            "",
        ]
        top_n = max(0, int(top_n))
        top = entries[:top_n]
        if top:
            lines.append("# 🔥 Top Technology")
            lines.append("")
            for entry in top:
                lines.extend(self._render_top_item(entry))
        rest = entries[top_n:]
        sections = self._group(rest)
        for key in ("papers", "github_new", "product_updates", "engineering"):
            items = sections.get(key, [])
            if not items:
                continue
            lines.append(SECTION_TITLES[key])
            lines.append("")
            for entry in items:
                lines.append(self._render_compact(entry))
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _group(self, entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """按来源 / 分类把条目分到四个分组。"""
        groups: dict[str, list[dict[str, Any]]] = {key: [] for key in SECTION_TITLES}
        eng_terms = {
            term.lower()
            for name, terms in self.config.categories()
            if name == "engineering_machinery"
            for term in terms
        }
        for entry in entries:
            item = entry["item"]
            cats = [c.lower() for c in entry["analysis"].get("categories", [])]
            text = " ".join((item.title or "", item.abstract or "")).lower()
            if "engineering_machinery" in cats or any(t in text for t in eng_terms):
                groups["engineering"].append(entry)
            elif item.source_type == "arxiv":
                groups["papers"].append(entry)
            elif item.source_type == "github" and item.metadata.get("watched") is not True:
                groups["github_new"].append(entry)
            else:
                groups["product_updates"].append(entry)
        return groups

    # ------------------------------------------------------------------ helpers
    def _render_top_item(self, entry: dict[str, Any]) -> list[str]:
        item = entry["item"]
        analysis = entry["analysis"]
        relevance = entry["relevance"]
        score = round(analysis.get("score") or 0)
        lines = [
            f"## {item.title}",
            "",
            f"- 来源: {item.source_name}（{item.source_type}）",
            f"- 原文: [{item.title}]({item.url})",
            f"- 分析依据: {analysis_basis(analysis)}",
            f"- 资料情况: {evidence_note(analysis)}",
            f"- 分类: {', '.join(analysis.get('categories', [])) or '-'}",
            f"- 相关性: {score} / 100",
            f"- 成熟度: {analysis.get('maturity') or '-'} | 开源: "
            + self._yes_no(analysis.get("open_source")),
            "",
            "### 技术是什么",
            "",
            analysis.get("summary") or "-",
            "",
            "### 核心变化",
            "",
            analysis.get("solution") or analysis.get("problem") or "-",
            "",
        ]
        innovation = analysis.get("innovation", [])
        if innovation:
            lines += ["### 为什么值得关注", ""]
            lines += [f"- {point}" for point in innovation]
            lines += [""]
        top_projects = sorted(
            relevance.items(), key=lambda kv: kv[1], reverse=True
        )[:2]
        if top_projects:
            lines += ["### 对我们的影响", ""]
            lines += [
                f"- {key}: {value:.0f}/5" for key, value in top_projects
            ]
            lines += [""]
        lines += ["### 建议", "", analysis.get("recommended_action") or "-", ""]
        return lines

    def _render_compact(self, entry: dict[str, Any]) -> str:
        item = entry["item"]
        analysis = entry["analysis"]
        score = round(analysis.get("score") or 0)
        cats = ", ".join(analysis.get("categories", [])) or "-"
        return (
            f"- [{item.title}]({item.url}) — 评分 {score}｜{cats}｜{analysis_basis(analysis)}"
        )

    @staticmethod
    def _yes_no(value: Any) -> str:
        if value is None:
            return "未知"
        return "是" if value else "否"

    # ------------------------------------------------------------------ record
    def _record(self, report_date: date, path: Path, overview: dict[str, Any]) -> None:
        conn = self.db.connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO reports (kind, report_date, path, summary)"
                    " VALUES ('daily', ?, ?, ?) ON CONFLICT(kind,path) DO UPDATE SET report_date=excluded.report_date,summary=excluded.summary,created_at=datetime('now')",
                    (
                        report_date.isoformat(),
                        str(path.resolve()),
                        f"新增 {overview['total_collected']} / 分析 {overview['total_analyzed']}"
                        f" / 高分 {overview['high_count']} / {analysis_overview(overview['analysis_counts'])}",
                    ),
                )
        finally:
            conn.close()
