"""Phase 8 测试：keyword_search 与 LLM 问答（RAG）。"""

from __future__ import annotations

import pytest

import techradar.search as search_mod
from techradar.search import ask, keyword_search
from techradar.storage.models import TechItem
from techradar.storage.repository import ItemRepository


def seed(db) -> list[TechItem]:
    irepo = ItemRepository(db)
    items = [
        TechItem(
            source_type="github", source_name="IsaacLab", title="Isaac Lab v2.0 Release",
            url="https://github.com/isaac-sim/IsaacLab/releases/tag/v2.0",
            abstract="multi-physics backend for robot learning",
        ),
        TechItem(
            source_type="arxiv", source_name="arXiv", title="Real2Sim Asset Generation via Gaussian Splatting",
            url="https://arxiv.org/abs/2601.77", abstract="real2sim gaussian splatting scene reconstruction",
        ),
        TechItem(
            source_type="rss", source_name="NVIDIA Blog", title="Accelerating Isaac Sim with Warp",
            url="https://developer.nvidia.com/blog/isaac-warp", abstract="warp GPU simulation",
        ),
    ]
    for item in items:
        irepo.insert(item)
    return items


def test_keyword_search(database) -> None:
    items = seed(database)
    assert keyword_search(database, "real2sim", limit=10)[0].url == items[1].url
    assert keyword_search(database, "gaussian", limit=10)[0].title == items[1].title
    assert len(keyword_search(database, "not-exist-term", limit=10)) == 0


class FakeLLMClient:
    def __init__(self, config=None) -> None:
        self.calls = 0
        self.last_user = None

    def chat_json(self, system, user, json_mode=True):
        self.calls += 1
        self.last_user = user
        if "检索关键词" in system:
            return {"keywords": ["Isaac", "physics"]}
        return {
            "answer": "Isaac Lab v2.0 引入多物理后端，可关注其对 FunLab PhysicsBackend 的影响。",
            "source_urls": ["https://github.com/isaac-sim/IsaacLab/releases/tag/v2.0"],
            "_usage": {"total_tokens": 42},
        }


def test_ask_with_sources(config, database, monkeypatch) -> None:
    seed(database)
    fake = FakeLLMClient()
    monkeypatch.setattr(search_mod, "LLMClient", lambda config=None: fake)
    result = ask(config, database, "Isaac Lab 有哪些新变化？", top=10)
    assert result["answer"]
    assert len(result["sources"]) == 1
    assert result["sources"][0]["url"] == "https://github.com/isaac-sim/IsaacLab/releases/tag/v2.0"
    assert result["candidate_count"] >= 2  # 分词检索命中多条


def test_ask_empty_db(config, database, monkeypatch) -> None:
    monkeypatch.setattr(search_mod, "LLMClient", lambda config=None: FakeLLMClient())
    result = ask(config, database, "有什么新东西？", top=10)
    assert result["candidate_count"] == 0
    assert "知识库中没有检索到相关资料" in result["answer"]


def test_ask_query_expansion(config, database, monkeypatch) -> None:
    """中文问题首次检索为空 -> LLM 查询扩展（英文关键词）-> 命中。"""
    seed(database)
    fake = FakeLLMClient()
    monkeypatch.setattr(search_mod, "LLMClient", lambda config=None: fake)
    result = ask(config, database, "机器人仿真物理引擎最近有什么新进展？", top=10)
    assert result["candidate_count"] >= 2
    assert fake.calls == 2  # 扩展 + 回答
    assert len(result["sources"]) == 1


def test_ask_groups_same_repo_versions(config, database, monkeypatch) -> None:
    """同 repo 多版本在问答中聚合为一组，上下文标注版本数。"""
    from techradar.storage.repository import ItemRepository

    irepo = ItemRepository(database)
    for tag in ("v1.4.0", "v1.5.0"):
        irepo.insert(TechItem(
            source_type="github", source_name="newton",
            title=f"newton-physics/newton {tag} Release",
            url=f"https://github.com/newton-physics/newton/releases/tag/{tag}",
            abstract="physics engine system identification",
            metadata={"repo": "newton-physics/newton", "watched": True},
        ))
    fake = FakeLLMClient()
    monkeypatch.setattr(search_mod, "LLMClient", lambda config=None: fake)
    result = ask(config, database, "Newton 有什么新版本？", top=10)
    assert result["candidate_count"] == 1  # 聚合为 1 组
    assert fake.last_user is not None and "该仓库共 2 个版本" in fake.last_user


def test_ask_retains_low_platform_scores(config, database, monkeypatch) -> None:
    """A platform score must not exclude evidence matching a specific question."""
    from techradar.storage.repository import AnalysisRepository, ItemRepository

    irepo = ItemRepository(database)
    tracked = TechItem(
        source_type="arxiv", source_name="arXiv", title="ControlRadio Benchmark",
        url="https://arxiv.org/abs/cr1", abstract="simulation benchmark",
    )
    low = TechItem(
        source_type="arxiv", source_name="arXiv", title="ControlRadio Game Wiki",
        url="https://arxiv.org/abs/cr2", abstract="video game free setup universe sandbox",
    )
    irepo.insert(tracked)
    irepo.insert(low)
    AnalysisRepository(database).upsert_analysis(low.id, {
        "summary": "无关", "categories": [], "score": 25, "recommended_action": "ARCHIVE",
    })
    fake = FakeLLMClient()
    monkeypatch.setattr(search_mod, "LLMClient", lambda config=None: fake)
    result = ask(config, database, "ControlRadio 有什么？", top=10)
    assert result["candidate_count"] == 2
    assert "ControlRadio Benchmark" in fake.last_user
    assert "ControlRadio Game Wiki" in fake.last_user


def test_ask_llm_error(config, database, monkeypatch) -> None:
    seed(database)

    class Broken:
        def chat_json(self, system, user, json_mode=True):
            from techradar.llm.client import LLMError

            raise LLMError("boom")

    monkeypatch.setattr(search_mod, "LLMClient", lambda config=None: Broken())
    with pytest.raises(Exception):
        ask(config, database, "Isaac Lab 有什么新变化？", top=5)  # 有候选才会调 LLM


def test_all_tokens_are_ranked_before_limit(config, database, monkeypatch):
    target = TechItem(source_type="arxiv", source_name="test", title="Physics Gaussian scene reconstruction", url="https://example.com/target", abstract="physics Gaussian reconstruction")
    ItemRepository(database).insert(target)
    for index in range(12):
        ItemRepository(database).insert(TechItem(source_type="arxiv", source_name="test", title=f"Physics toolkit {index}", url=f"https://example.com/generic/{index}", abstract="physics engine"))
    class Answer:
        def chat_json(self, system, user, json_mode=True):
            assert target.title in user
            assert "Physics toolkit" not in user
            return {"answer": "Relevant evidence", "source_urls": [target.url]}
    monkeypatch.setattr(search_mod, "LLMClient", lambda config=None: Answer())
    result = ask(config, database, "Physics Gaussian", top=1)
    assert result["sources"][0]["url"] == target.url


def test_context_includes_late_raw_text_match(config, database, monkeypatch):
    item = TechItem(source_type="arxiv", source_name="test", title="Quaternion stability", url="https://example.com/quaternion", abstract="General robotics introduction", raw_text="Unrelated documentation. " * 200 + "Quaternion drift is reduced by calibration against measured trajectories.")
    ItemRepository(database).insert(item)
    class Answer:
        def chat_json(self, system, user, json_mode=True):
            assert "Quaternion drift is reduced by calibration" in user
            return {"answer": "Calibration evidence", "source_urls": [item.url]}
    monkeypatch.setattr(search_mod, "LLMClient", lambda config=None: Answer())
    assert ask(config, database, "Quaternion", top=1)["status"] == "answered"


def test_invalid_or_empty_citations_return_insufficient(config, database, monkeypatch):
    seed(database)
    class NoCitation:
        def chat_json(self, system, user, json_mode=True):
            return {"answer": "Unsupported technical recommendation", "source_urls": ["https://invented.example.com"]}
    monkeypatch.setattr(search_mod, "LLMClient", lambda config=None: NoCitation())
    result = ask(config, database, "Isaac", top=2)
    assert result["status"] == "insufficient_evidence"
    assert "Unsupported" not in result["answer"]
    assert not result["sources"]


def test_title_only_match_does_not_generate_technical_answer(config, database, monkeypatch):
    ItemRepository(database).insert(TechItem(source_type="arxiv", source_name="test", title="Quaternion", url="https://example.com/title-only"))
    def unexpected(*args, **kwargs):
        pytest.fail("A title without evidence must not invoke the answer model")
    monkeypatch.setattr(search_mod, "LLMClient", unexpected)
    result = ask(config, database, "Quaternion")
    assert result["status"] == "insufficient_evidence"


@pytest.mark.parametrize("analysis_status", ["llm", "skipped", "llm_failed"])
def test_specific_question_uses_low_platform_score_evidence(config, database, monkeypatch, analysis_status):
    from techradar.storage.repository import AnalysisRepository
    target = TechItem(source_type="arxiv", source_name="test", title="Hydraulic drift calibration", url="https://example.com/calibration", abstract="Hydraulic actuator drift is reduced by calibration against measured trajectories.")
    generic = TechItem(source_type="arxiv", source_name="test", title="Hydraulic simulation engine", url="https://example.com/engine", abstract="A hydraulic simulation engine for general robot learning.")
    ItemRepository(database).insert_many([target, generic])
    arepo = AnalysisRepository(database)
    arepo.upsert_analysis(target.id, {"score": 18, "llm_status": analysis_status})
    arepo.upsert_analysis(generic.id, {"score": 95, "llm_status": "llm"})
    class Answer:
        def chat_json(self, system, user, json_mode=True):
            assert target.title in user
            assert generic.title not in user
            return {"answer": "Measured calibration evidence", "source_urls": [target.url]}
    monkeypatch.setattr(search_mod, "LLMClient", lambda config=None: Answer())
    result = ask(config, database, "Hydraulic drift calibration", top=1)
    assert result["status"] == "answered"
    assert result["sources"] == [{"title": target.title, "url": target.url}]
