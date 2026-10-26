"""Pipeline 端到端测试（假采集器注入，验证 采集->过滤->去重->入库）。"""

from __future__ import annotations

import pytest

from techradar.collectors.base import BaseCollector
from techradar.pipeline import Pipeline
from techradar.storage.models import TechItem
from techradar.storage.repository import ItemRepository


class FakeCollector(BaseCollector):
    source_type = "github"
    source_name = "GitHub"

    def __init__(self, items: list[TechItem], config: dict | None = None) -> None:
        super().__init__(config)
        self._items = items

    def fetch(self) -> list[TechItem]:
        return self._items


def make_item(url: str, title: str, abstract: str = "", **meta) -> TechItem:
    return TechItem(
        source_type="github", source_name="x", title=title, url=url,
        abstract=abstract, metadata=meta,
    )


SAMPLE_ITEMS = [
    make_item(
        "https://e/1", "Isaac Lab v2.0 Release", "Multi-physics backend for robot learning",
    ),
    make_item("https://e/1", "Isaac Lab v2.0 Release"),  # 同 URL -> 去重
    make_item("https://e/2", "Crypto market update"),  # 负向词 -> 过滤
    make_item("https://e/3", "Unrelated random content"),  # 无关键词 -> 过滤
]


def test_pipeline_collect_stores(config, database, monkeypatch) -> None:
    monkeypatch.setattr(
        "techradar.pipeline.build_collectors",
        lambda cfg, sources: [FakeCollector(SAMPLE_ITEMS, config={})],
    )
    stats = Pipeline(config, database).collect()
    entry = stats["github"]
    assert entry["collected"] == 4
    assert entry["dropped"] == 2
    assert entry["duplicates"] == 1
    assert entry["inserted"] == 1
    assert ItemRepository(database).count() == 1


def test_pipeline_dedup_against_existing(config, database, monkeypatch) -> None:
    existing = make_item(
        "https://e/old", "Physics engine v2 deep dive", abstract="physics engine"
    )
    ItemRepository(database).insert(existing)
    new_items = [
        # 身份与正文都未变化，更新层应报告 unchanged。
        make_item("https://e/old", "Physics engine v2 deep dive", abstract="physics engine"),
        make_item("https://e/new", "A brand new simulation engine release"),
    ]
    fake = FakeCollector(new_items, config={})
    monkeypatch.setattr(
        "techradar.pipeline.build_collectors", lambda cfg, sources: [fake]
    )
    stats = Pipeline(config, database).collect()
    assert stats["github"]["dropped"] == 0
    assert stats["github"]["duplicates"] == 1
    assert stats["github"]["inserted"] == 1
    assert ItemRepository(database).count() == 2


def test_pipeline_source_error_isolated(config, database, monkeypatch) -> None:
    class BrokenCollector(BaseCollector):
        source_type = "broken"
        source_name = "Broken"

        def fetch(self) -> list[TechItem]:
            raise RuntimeError("network down")

    good = FakeCollector(
        [make_item("https://e/good", "A real2sim training dataset"), ], config={}
    )
    monkeypatch.setattr(
        "techradar.pipeline.build_collectors",
        lambda cfg, sources: [BrokenCollector({}), good],
    )
    stats = Pipeline(config, database).collect()
    assert "error" in stats["broken"]
    assert stats["github"]["inserted"] == 1
    assert ItemRepository(database).count() == 1  # 失败源不影响成功源


def test_collect_no_store(config, database, monkeypatch) -> None:
    monkeypatch.setattr(
        "techradar.pipeline.build_collectors",
        lambda cfg, sources: [FakeCollector(SAMPLE_ITEMS[:1], config={})],
    )
    stats = Pipeline(config, database).collect(store=False)
    assert stats["github"]["inserted"] == 1
    assert ItemRepository(database).count() == 0


# ---------------------------------------------------------------- analyze
class FakeLLMClient:
    def __init__(self, config=None) -> None:
        self.calls = 0

    def chat_json(self, system, user, json_mode=True):
        self.calls += 1
        return {
            "summary": "多物理后端融合框架",
            "problem": "单物理后端局限",
            "solution": "统一后端抽象",
            "innovation": ["统一 API"],
            "technical_details": ["C++ 核心"],
            "comparison": "优于旧方案",
            "open_source": True,
            "maturity": "release",
            "categories": ["simulation_engine"],
            "keywords": ["physics backend"],
            "related_projects": ["Isaac Lab"],
            "platform_relevance": "影响 FunLab PhysicsBackend",
            "recommended_action": "POC",
            "overall_relevance": 85,
            "relevance": {"funlab": 5, "simulation_platform": 5, "real2sim": 1,
                          "engineering_machinery": 2, "robot_learning": 4},
            "_usage": {"total_tokens": 300},
        }


def test_pipeline_analyze(config, database, monkeypatch) -> None:
    from techradar.storage.repository import AnalysisRepository

    high = make_item(
        "https://e/high", "Isaac Lab v2.0 Release", abstract="physics engine multi backend"
    )
    low = make_item("https://e/low", "Random unrelated interior design", abstract="noise")
    ItemRepository(database).insert(high)
    ItemRepository(database).insert(low)

    fake_llm = FakeLLMClient()
    monkeypatch.setattr("techradar.llm.client.LLMClient", lambda config=None: fake_llm)

    stats = Pipeline(config, database).analyze(limit=10)
    assert stats["items"] == 2
    assert stats["llm_calls"] == 1  # 只有命中关键词的调用 LLM
    assert stats["skipped_llm"] == 1
    assert stats["llm_errors"] == 0
    assert stats["stored"] == 2
    assert AnalysisRepository(database).analyzed_count() == 2

    conn = database.connect()
    try:
        rows = conn.execute("SELECT item_id, score FROM analysis").fetchall()
    finally:
        conn.close()
    scores = {row["item_id"]: row["score"] for row in rows}
    assert scores[high.id] > scores[low.id]  # 高相关条目得分更高


def test_pipeline_analyze_dry_run(config, database, monkeypatch) -> None:
    item = make_item("https://e/dry", "Real2Sim asset pipeline release", abstract="real2sim")
    ItemRepository(database).insert(item)
    monkeypatch.setattr("techradar.llm.client.LLMClient", lambda config=None: FakeLLMClient())
    stats = Pipeline(config, database).analyze(limit=10, dry_run=True)
    assert stats["stored"] == 0
    from techradar.storage.repository import AnalysisRepository

    assert AnalysisRepository(database).analyzed_count() == 0


def test_pipeline_analyze_min_hits_tiered(config, database, monkeypatch) -> None:
    """分级：--min-hits=2 时，命中不足 2 词的条目不调用 LLM（仅存关键词评分）。"""
    from techradar.storage.repository import AnalysisRepository

    low = make_item("https://e/low", "Interior design trends", abstract="physics engine")
    watched = make_item(
        "https://e/w", "newton-physics/newton v1.5.0 Release",
        "physics engine sim2real system identification", watched=True,
    )
    ItemRepository(database).insert(low)
    ItemRepository(database).insert(watched)

    fake = FakeLLMClient()
    monkeypatch.setattr("techradar.llm.client.LLMClient", lambda config=None: fake)

    stats = Pipeline(config, database).analyze(min_hits=2)
    # watch 仓库总是调用 LLM；非 watch 且命中 < 2 的条目不调用
    assert stats["llm_calls"] == 1
    assert fake.calls == 1
    assert stats["skipped_llm"] == 1
    assert AnalysisRepository(database).analyzed_count() == 2

    conn = database.connect()
    try:
        row = conn.execute(
            "SELECT llm_status FROM analysis WHERE item_id=?", (low.id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["llm_status"] == "skipped"  # 低命中条目仅关键词评分


def test_pipeline_analyze_force_reanalyzes_skipped(config, database, monkeypatch) -> None:
    """--force：重跑 llm_status != 'llm' 的条目（补全分析）。"""
    # hits=1（physics engine），min_hits=2 时被跳过；摘要需满足"内容充分"闸门
    item = make_item(
        "https://e/f", "Physics Engine Update",
        abstract="A physics engine update improving collision contact stability and "
                 "GPU acceleration for robot simulation workloads across learning "
                 "pipelines and benchmark suites.",
    )
    ItemRepository(database).insert(item)
    monkeypatch.setattr("techradar.llm.client.LLMClient", lambda config=None: FakeLLMClient())
    stats = Pipeline(config, database).analyze(min_hits=2)
    assert stats["llm_calls"] == 0
    assert stats["skipped_llm"] == 1

    # force + 默认 min_hits=1：补跑 LLM
    fake = FakeLLMClient()
    monkeypatch.setattr("techradar.llm.client.LLMClient", lambda config=None: fake)
    stats2 = Pipeline(config, database).analyze(min_hits=1, force=True)
    assert stats2["items"] == 1
    assert stats2["llm_calls"] == 1
    conn = database.connect()
    try:
        row = conn.execute(
            "SELECT llm_status FROM analysis WHERE item_id=?", (item.id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["llm_status"] == "llm"


def test_pipeline_source_partial_keeps_collected_items(config, database, monkeypatch) -> None:
    class PartialCollector(FakeCollector):
        def fetch(self):
            self.record_attempt()
            self.record_success()
            self.record_attempt()
            self.record_failure("unavailable feed", "HTTP 404", kind="network")
            return self._items
    fake = PartialCollector([make_item("https://e/partial", "Physics engine report")])
    monkeypatch.setattr("techradar.pipeline.build_collectors", lambda cfg, sources: [fake])
    entry = Pipeline(config, database).collect()["github"]
    assert entry["status"] == "partial"
    assert entry["attempted"] == 2
    assert entry["succeeded"] == 1
    assert len(entry["failures"]) == 1
    assert entry["inserted"] == 1
    assert ItemRepository(database).count() == 1


def test_pipeline_refresh_updates_changed_content_and_requeues(config, database, monkeypatch) -> None:
    from techradar.storage.repository import AnalysisRepository
    old = make_item("https://e/mutable", "Physics engine toolkit", abstract="Old physics engine API")
    ItemRepository(database).insert(old)
    arepo = AnalysisRepository(database)
    arepo.save_analysis_result(old.id, {"summary": "Old", "llm_status": "llm", "score": 50}, {})
    changed = make_item(old.url, old.title, abstract="New physics engine API with GPU contact solving")
    monkeypatch.setattr("techradar.pipeline.build_collectors", lambda cfg, sources: [FakeCollector([changed])])
    entry = Pipeline(config, database).collect()["github"]
    assert entry["updated"] == 1
    assert entry["inserted"] == 0
    assert entry["duplicates"] == 0
    assert ItemRepository(database).get(old.id).abstract == changed.abstract
    assert [item.id for item in arepo.unanalyzed()] == [old.id]
    assert Pipeline(config, database).collect()["github"]["unchanged"] == 1


def test_pipeline_first_release_has_full_novelty_and_source_score(config, database, monkeypatch) -> None:
    from techradar.storage.repository import AnalysisRepository
    item = make_item("https://e/first", "PhysicsLab v3.0.0 Release", abstract="physics engine", watched=True, repo="team/PhysicsLab")
    ItemRepository(database).insert(item)
    monkeypatch.setattr("techradar.llm.client.LLMClient", lambda config=None: FakeLLMClient())
    Pipeline(config, database).analyze()
    state = AnalysisRepository(database).analysis_state(item.id)
    assert state["breakdown"]["novelty_score"] == 100
    assert state["breakdown"]["source_score"] == 100
    assert state["breakdown"]["evidence"]["quality"] == "limited"


def test_pipeline_failed_analysis_retries_when_due_and_clears_failure(config, database, monkeypatch) -> None:
    from techradar.llm.client import LLMError
    from techradar.storage.repository import AnalysisRepository
    item = make_item("https://e/retry", "Physics engine release", abstract="physics engine contact solving")
    ItemRepository(database).insert(item)
    class FailingClient(FakeLLMClient):
        def chat_json(self, *args, **kwargs):
            raise LLMError("Temporary provider problem")
    monkeypatch.setattr("techradar.llm.client.LLMClient", lambda config=None: FailingClient())
    result = Pipeline(config, database).analyze()
    arepo = AnalysisRepository(database)
    failed = arepo.analysis_state(item.id)
    assert result["llm_errors"] == 1
    assert result["skipped_llm"] == 0
    assert failed["llm_status"] == "llm_failed"
    assert failed["attempts"] == 1
    assert failed["last_error"]
    assert failed["next_retry_at"]
    assert arepo.analyzed_count() == 0
    assert arepo.unanalyzed() == []
    conn = database.connect()
    try:
        with conn:
            conn.execute("UPDATE analysis SET next_retry_at='2000-01-01T00:00:00+00:00' WHERE item_id=?", (item.id,))
    finally:
        conn.close()
    monkeypatch.setattr("techradar.llm.client.LLMClient", lambda config=None: FakeLLMClient())
    result = Pipeline(config, database).analyze()
    succeeded = arepo.analysis_state(item.id)
    assert result["analyzed"] == 1
    assert result["retry_attempts"] == 1
    assert succeeded["llm_status"] == "llm"
    assert succeeded["attempts"] == 2
    assert not succeeded["last_error"]
    assert not succeeded["next_retry_at"]
    assert arepo.analyzed_count() == 1


def test_rule_only_analysis_does_not_require_llm_credentials(config, database, monkeypatch) -> None:
    item = make_item("https://e/missing", "Physics engine release", watched=True)
    ItemRepository(database).insert(item)
    def unexpected_client(*args, **kwargs):
        raise AssertionError("No content should not cause a model call")
    monkeypatch.setattr("techradar.llm.client.LLMClient", unexpected_client)
    result = Pipeline(config, database).analyze()
    assert result["skipped_llm"] == 1
    assert result["llm_calls"] == 0
    assert result["insufficient_content"] == 1


def test_failed_analysis_stops_automatic_retries_after_three_attempts(config, database, monkeypatch) -> None:
    from techradar.llm.client import LLMError
    from techradar.storage.repository import AnalysisRepository
    item = make_item("https://e/exhausted", "Physics engine", abstract="physics engine contact solving")
    ItemRepository(database).insert(item)
    arepo = AnalysisRepository(database)
    arepo.save_analysis_result(item.id, {"llm_status": "llm_failed", "attempts": 2,
        "last_error": "Temporary issue", "next_retry_at": "2000-01-01T00:00:00+00:00"}, {})
    class FailingClient(FakeLLMClient):
        def chat_json(self, *args, **kwargs):
            raise LLMError("Temporary issue")
    monkeypatch.setattr("techradar.llm.client.LLMClient", lambda config=None: FailingClient())
    result = Pipeline(config, database).analyze()
    assert result["retry_attempts"] == 1
    assert arepo.analysis_state(item.id)["attempts"] == 3
    assert arepo.unanalyzed() == []
    assert arepo.status_counts()["llm_failed"] == 1


def test_analysis_does_not_save_old_result_when_source_changes_during_model_call(config, database, monkeypatch) -> None:
    from techradar.storage.repository import AnalysisRepository
    item = make_item("https://e/concurrent", "Physics engine", abstract="Original physics engine release details")
    ItemRepository(database).insert(item)
    class SourceUpdatingClient(FakeLLMClient):
        def chat_json(self, *args, **kwargs):
            updated = make_item(item.url, item.title, abstract="Updated physics engine details and corrected benchmark results")
            ItemRepository(database).upsert_many([updated])
            return super().chat_json(*args, **kwargs)
    monkeypatch.setattr("techradar.llm.client.LLMClient", lambda config=None: SourceUpdatingClient())
    result = Pipeline(config, database).analyze()
    arepo = AnalysisRepository(database)
    assert result["stored"] == 0
    assert result["analyzed"] == 0
    assert result["stale_skipped"] == 1
    assert arepo.analyzed_count() == 0
    assert [pending.id for pending in arepo.unanalyzed()] == [item.id]
    assert "corrected benchmark" in ItemRepository(database).get(item.id).abstract
