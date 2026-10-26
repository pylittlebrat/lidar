"""采集与分析编排：来源隔离、内容更新、可重试分析与透明评分。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Sequence

from techradar.collectors.registry import COLLECTORS, build_collectors
from techradar.config import Config
from techradar.processors.deduplicate import Deduplicator
from techradar.processors.filter import KeywordFilter
from techradar.processors.normalize import Normalizer
from techradar.storage.database import Database
from techradar.storage.repository import ItemRepository
from techradar.storage.models import TechItem
from techradar.utils.logging import get_logger
from techradar.utils.time import utcnow

# 采集先保存原始条目，分析阶段再按内容质量执行分类与评分。
STAGES: tuple[str, ...] = (
    "collect",
    "normalize",
    "filter",
    "deduplicate",
    "enrich",
    "classify",
    "analyze",
    "score",
    "store",
)


class Pipeline:
    """流水线编排器。"""

    def __init__(self, config: Config, db: Database | None = None) -> None:
        self.config = config
        self.db = db
        self.logger = get_logger("techradar.pipeline")
        self.normalizer = Normalizer()
        self.filter = KeywordFilter(config)

    @staticmethod
    def _source_outcome(collector, entry: dict[str, Any]) -> None:
        """子请求部分失败仍保留成功内容，并将来源健康状态上报。"""
        outcome = getattr(collector, "last_result", {}) or {}
        for key in ("attempted", "succeeded", "failures", "status"):
            if key in outcome:
                entry[key] = outcome[key]
        entry.setdefault("status", "success")
        failures = entry.get("failures", [])
        if failures:
            entry["status"] = "partial" if entry.get("succeeded", 0) else "failed"
            entry["error"] = f"{len(failures)} 个采集请求失败"

    def _process_collected(
        self,
        raw: list[TechItem],
        repo: ItemRepository | None,
        existing_hashes: set[str],
        existing_titles: list,
        store: bool,
    ) -> dict[str, Any]:
        """共用清洗流程；同一 URL 再次出现交给存储层判断内容变化。"""
        normalized = [self.normalizer.normalize(item) for item in raw]
        kept, dropped = self.filter.apply(normalized)
        batch: dict[str, TechItem] = {}
        duplicate_count = 0
        for item in kept:
            identity = item.content_hash()
            previous = batch.get(identity)
            if previous is not None:
                duplicate_count += 1
                # 同批搜索可能重复发现同一仓库，保留更完整的来源内容。
                if len(previous.raw_text) + len(previous.abstract) > len(item.raw_text) + len(item.abstract):
                    continue
            batch[identity] = item
        updates = [item for identity, item in batch.items() if identity in existing_hashes]
        fresh = [item for identity, item in batch.items() if identity not in existing_hashes]
        unique, dedup_count = Deduplicator(self.config).deduplicate(
            fresh, existing_hashes, existing_titles
        )
        candidates = unique + updates
        entry = {"collected": len(raw), "dropped": len(dropped),
                 "duplicates": duplicate_count + dedup_count, "inserted": 0,
                 "updated": 0, "unchanged": 0}
        if store and repo is not None:
            result = repo.upsert_many(candidates)
            for key in ("inserted", "updated", "unchanged"):
                entry[key] = result.get(key, 0)
            entry["duplicates"] += entry["unchanged"]
        else:
            entry["inserted"] = len(unique)
            entry["unchanged"] = len(updates)
            entry["duplicates"] += len(updates)
        existing_hashes.update(item.content_hash() for item in candidates)
        existing_titles.extend((item.source_type, item.title) for item in candidates)
        return entry

    def collect(
        self,
        sources: Sequence[str] | None = None,
        limit: int | None = None,
        store: bool = True,
    ) -> dict[str, Any]:
        """执行 采集 -> 归一化 -> 关键词过滤 -> 去重 -> 入库。

        :param sources: 要采集的 source_type 白名单（None = 全部启用项）
        :param limit: 每源最多处理的原始条目数（调试用）
        :param store: False 时只统计不写库
        :return: 每信息源的统计 dict，如 {"collected": 12, "inserted": 9, ...}
        """
        repo = ItemRepository(self.db) if self.db is not None else None
        existing_hashes = repo.all_hashes() if repo else set()
        existing_titles = repo.recent_titles() if repo else []
        stats: dict[str, Any] = {}

        for collector in build_collectors(self.config, sources):
            source = collector.source_type
            self.logger.info("== 采集开始: %s ==", source)
            entry: dict[str, Any] = {"collected": 0, "dropped": 0, "duplicates": 0,
                                     "inserted": 0, "updated": 0, "unchanged": 0}
            try:
                raw = collector.collect()
                if limit is not None:
                    raw = raw[:limit]
                entry.update(self._process_collected(raw, repo, existing_hashes, existing_titles, store))
                self._source_outcome(collector, entry)
                self.logger.info(
                    "[%s] collected=%d kept=%d dropped=%d duplicates=%d inserted=%d updated=%d status=%s",
                    source,
                    entry["collected"],
                    entry["collected"] - entry["dropped"],
                    entry["dropped"],
                    entry["duplicates"],
                    entry["inserted"],
                    entry["updated"],
                    entry["status"],
                )
            except Exception as exc:
                entry["error"] = f"采集请求失败（{type(exc).__name__}）"
                entry["status"] = "failed"
                self.logger.error("信息源 %s 失败（不影响其他源）: %s", source, entry["error"])
            stats[source] = entry
        return stats

    def backfill(
        self,
        days: int = 90,
        sources: Sequence[str] | None = None,
        max_per_source: int = 300,
        store: bool = True,
    ) -> dict[str, Any]:
        """历史回填：把最近 days 天的历史内容一次性入库。

        与 collect() 的差异（保证"不影响每日雷达"）：
        1. GitHub watch 仓库改为抓取近 days 天全部 releases（history_days）；
           arXiv 放大 max_results。
        2. 入库条目的 collected_at 置为 published_at（历史时间语义），
           使日报/周报/趋势的近期窗口统计不含回填数据；检索/问答不受影响。
        3. 只处理 github / arxiv（RSS 无法回溯历史）。
        """
        collections = self.config.sources.get("collections", {})
        overrides: dict[str, dict] = {}
        gh = dict(collections.get("github", {}))
        gh["limits"] = {**gh.get("limits", {}), "history_days": days,
                        "fetch_repo_meta": False, "max_releases": 100}
        overrides["github"] = gh
        ax = dict(collections.get("arxiv", {}))
        ax["limits"] = {**ax.get("limits", {}), "max_queries": 5, "max_results": 60,
                        "history_days": days, "history_pages": 4}
        overrides["arxiv"] = ax

        built = []
        for source_type in ("github", "arxiv"):
            if sources is not None and source_type not in sources:
                continue
            section = overrides.get(source_type, {})
            if not section.get("enabled", True):
                continue
            built.append(COLLECTORS[source_type](config=section))

        repo = ItemRepository(self.db) if self.db is not None else None
        existing_hashes = repo.all_hashes() if repo else set()
        existing_titles = repo.recent_titles(days=365, limit=2000) if repo else []
        stats: dict[str, Any] = {}
        for collector in built:
            source = collector.source_type
            self.logger.info("== 历史回填开始: %s（近 %d 天）==", source, days)
            entry: dict[str, Any] = {"collected": 0, "dropped": 0, "duplicates": 0,
                                     "inserted": 0, "updated": 0, "unchanged": 0}
            try:
                raw = collector.collect()
                raw.sort(
                    key=lambda i: i.published_at if i.published_at else i.collected_at,
                    reverse=True,
                )
                raw = raw[:max_per_source]
                for item in raw:
                    if item.published_at is not None:
                        item.collected_at = item.published_at  # 历史时间语义
                entry.update(self._process_collected(raw, repo, existing_hashes, existing_titles, store))
                self._source_outcome(collector, entry)
                self.logger.info(
                    "[backfill:%s] collected=%d kept=%d dropped=%d duplicates=%d inserted=%d",
                    source, entry["collected"],
                    entry["collected"] - entry["dropped"],
                    entry["dropped"], entry["duplicates"], entry["inserted"],
                )
            except Exception as exc:
                entry["error"] = f"历史采集请求失败（{type(exc).__name__}）"
                entry["status"] = "failed"
                self.logger.error("历史回填源 %s 失败（不影响其他源）: %s", source, entry["error"])
            stats[source] = entry
        return stats

    def analyze(
        self,
        limit: int | None = None,
        dry_run: bool = False,
        min_hits: int | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """LLM 分析未处理条目：分类 -> 过滤（LLM 成本控制）-> 分析 -> 评分 -> 入库。

        :param limit: 最多处理的条目数（None 时用 config 默认；配合 --all 可一次全量）
        :param dry_run: 只计算不写库
        :param min_hits: 调用 LLM 所需的最低优先级词命中数（分级控制，覆盖配置）
            命中低于该值（且非 watch 仓库）的条目仅存关键词评分，不调用 LLM。
        :param force: 补全模式：重跑所有 llm_status != 'llm' 的条目
            （含仅关键词评分 / 旧数据未标记 / llm_failed / 待分析）。
        :return: 统计 dict（items/llm_calls/llm_errors/skipped_llm/stored/tokens）
        """
        from techradar.analyzers.classifier import Classifier
        from techradar.analyzers.novelty import NoveltyScorer
        from techradar.analyzers.relevance import RelevanceEvaluator
        from techradar.analyzers.summarizer import Summarizer
        from techradar.llm.client import LLMClient, LLMError, LLMResponseError, LLMRetryableError
        from techradar.llm.prompts import content_evidence
        from techradar.llm.schemas import PROMPT_VERSION, coerce_analysis
        from techradar.storage.repository import AnalysisRepository

        llm_cfg = self.config.llm or {}
        if self.db is None:
            return {"error": "analyze 需要数据库（--db-path 或默认 data/radar.db）"}

        arepo = AnalysisRepository(self.db)
        max_items = limit if limit is not None else int(llm_cfg.get("max_items_per_run", 20))
        items = arepo.unanalyzed(limit=max_items, force=force)
        threshold = (
            int(min_hits)
            if min_hits is not None
            else int(llm_cfg.get("llm_min_keyword_hits", 1))
        )
        stats: dict[str, Any] = {
            "items": len(items),
            "llm_calls": 0,
            "llm_errors": 0,
            "skipped_llm": 0,
            "stored": 0,
            "tokens": 0,
            "analyzed": 0,
            "retry_attempts": 0,
            "insufficient_content": 0,
            "stale_skipped": 0,
        }
        if not items:
            return stats

        classifier = Classifier(self.config)
        novelty = NoveltyScorer(self.config)
        evaluator = RelevanceEvaluator(self.config)
        client = None
        summarizer = None
        item_repo = ItemRepository(self.db)
        lookback_days = int(self.config.scoring.get("novelty", {}).get("lookback_days", 90))

        for item in items:
            previous = arepo.analysis_state(item.id)
            previous_attempts = int(previous.get("attempts") or 0)
            evidence = content_evidence(item, min_chars=int(llm_cfg.get("min_evidence_chars", 120)))
            hits = classifier.keyword_hits(item)
            watched = item.metadata.get("watched") is True
            use_llm = bool(llm_cfg.get("enabled", True)) and (hits >= threshold or watched)
            if evidence["quality"] == "missing":
                use_llm = False
                stats["insufficient_content"] += 1
            elif evidence["quality"] == "limited":
                stats["insufficient_content"] += 1
            analysis = None
            last_error = ""
            if use_llm:
                if previous_attempts:
                    stats["retry_attempts"] += 1
                try:
                    if client is None:
                        client = LLMClient(config=self.config)
                        summarizer = Summarizer(client, self.config)
                    stats["llm_calls"] += 1
                    analysis = summarizer.analyze(item)
                    try:
                        tokens = max(0, int(analysis.get("_usage", {}).get("total_tokens", 0)))
                    except (TypeError, ValueError, OverflowError):
                        tokens = 0
                    stats["tokens"] += tokens
                    analysis.pop("_usage", None)
                    analysis["llm_status"] = "llm"
                except LLMError as exc:
                    stats["llm_errors"] += 1
                    if isinstance(exc, LLMResponseError):
                        last_error = "响应格式无效，模型输出未通过校验。"
                    elif isinstance(exc, LLMRetryableError):
                        last_error = "模型服务限流。" if "HTTP 429" in str(exc) else "网络或模型服务暂时不可用。"
                    elif "未配置" in str(exc):
                        last_error = "分析配置未完成，请检查模型连接配置。"
                    else:
                        last_error = "模型请求失败，请检查分析配置或服务状态。"
                    self.logger.warning("条目 %s 的 LLM 分析失败: %s", item.title[:60], last_error)
            if analysis is None:
                analysis = coerce_analysis({})
                analysis["llm_status"] = "skipped" if not use_llm else "llm_failed"
                if not use_llm:
                    stats["skipped_llm"] += 1
                    analysis["summary"] = (item.abstract or item.raw_text or item.title)[:400]
                    if evidence["quality"] != "available":
                        analysis["summary"] += f"\n\n{evidence['note']}"
            attempts = previous_attempts + (1 if use_llm else 0)
            analysis["attempts"] = attempts
            analysis["last_error"] = last_error
            analysis["next_retry_at"] = (
                (utcnow() + timedelta(minutes=5 * (3 ** min(max(attempts - 1, 0), 4)))).isoformat()
                if last_error else None
            )
            analysis["model"] = getattr(client, "model", str(llm_cfg.get("model", ""))) if use_llm else ""
            analysis["prompt_version"] = PROMPT_VERSION
            llm_relevance = (
                analysis.get("overall_relevance") if analysis.get("llm_status") == "llm" else None
            )
            history = item_repo.analysis_history(item, days=lookback_days)
            novelty_score = novelty.score(item, history)
            result = evaluator.evaluate(
                item, hits, llm_relevance, novelty_score, existing_titles=history
            )
            analysis["score"] = result["score"]
            analysis["breakdown"] = result["breakdown"]
            analysis["breakdown"]["evidence"] = evidence
            analysis["breakdown"]["validation_warnings"] = analysis.get("validation_warnings", [])
            # taxonomy 关键词分类与 LLM 分类合并（关键词结果优先展示）
            merged = list(dict.fromkeys(classifier.classify(item) + analysis.get("categories", [])))
            analysis["categories"] = merged
            saved = dry_run
            if not dry_run:
                saved = arepo.save_analysis_result(
                    item.id, analysis, analysis.get("relevance", {}),
                    expected_payload_hash=item.payload_hash(),
                )
                if saved:
                    stats["stored"] += 1
                else:
                    stats["stale_skipped"] += 1
                    self.logger.info("条目 %s 的来源内容已更新，旧分析结果已丢弃并保留待分析", item.title[:50])
            if saved and analysis["llm_status"] == "llm":
                stats["analyzed"] += 1
            self.logger.info(
                "[analyze] %s | score=%s | status=%s | attempt=%d",
                item.title[:50], result["score"], analysis["llm_status"], attempts
            )
        return stats

    def run(self) -> dict[str, Any]:
        """有数据库时运行共享完整同步流程；无数据库时仅预览采集。"""
        self.config.validate()
        self.logger.info("pipeline stages: %s", " -> ".join(STAGES))
        if self.db is None:
            return self.collect(store=False)
        from techradar.sync import run_sync
        return run_sync(self.config, self.db, trigger="pipeline")
