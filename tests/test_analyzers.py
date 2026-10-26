"""分析器测试：Classifier / NoveltyScorer / RelevanceEvaluator / Summarizer / coerce。"""

from __future__ import annotations

import pytest

from techradar.analyzers.classifier import Classifier
from techradar.analyzers.novelty import NoveltyScorer
from techradar.analyzers.relevance import RelevanceEvaluator
from techradar.analyzers.summarizer import Summarizer
from techradar.llm.schemas import AnalysisValidationError, coerce_analysis
from techradar.storage.models import TechItem


def make_item(title: str, abstract: str = "", **meta) -> TechItem:
    return TechItem(
        source_type="github",
        source_name="IsaacLab",
        title=title,
        url=f"https://e.example/{abs(hash(title))}",
        abstract=abstract,
        metadata=meta,
    )


# ---------------------------------------------------------------- Classifier
def test_classifier_hits_category_and_keywords(config) -> None:
    classifier = Classifier(config)
    item = make_item("Isaac Lab v2.0 Release", abstract="physics engine with real2sim support")
    categories = classifier.classify(item)
    assert "simulation_engine" in categories
    assert classifier.keyword_hits(item) >= 1


def test_classifier_no_hits(config) -> None:
    classifier = Classifier(config)
    item = make_item("Interior design trends", abstract="minimalism and furniture")
    assert classifier.classify(item) == []
    assert classifier.keyword_hits(item) == 0


def test_classifier_system_identification(config) -> None:
    """系统辨识内容应归入 system_identification 与 real2sim 分类。"""
    classifier = Classifier(config)
    item = make_item(
        "System Identification for Robot Manipulators",
        abstract="physics parameter estimation contact parameter identification",
    )
    categories = classifier.classify(item)
    assert "system_identification" in categories
    assert "real2sim" in categories


def test_classifier_embodied_ai(config) -> None:
    classifier = Classifier(config)
    item = make_item("Embodied AI Foundation Model", abstract="embodied intelligence robot")
    categories = classifier.classify(item)
    assert "embodied_ai" in categories


def test_analysis_prompt_uses_taxonomy_directions(config) -> None:
    """LLM 重点关注方向应从 taxonomy 配置生成（含新增分类）。"""
    from techradar.llm.prompts import build_analysis_system

    system = build_analysis_system(config)
    assert "system_identification" in system
    assert "embodied_ai" in system
    assert "real2sim" in system


# ---------------------------------------------------------------- Novelty
def test_novelty_scorer(config) -> None:
    scorer = NoveltyScorer(config)
    item = make_item("Isaac Lab v2.0 Release")
    assert scorer.score(item, ["Isaac Lab v2.0 Release"]) == 0
    assert scorer.is_novel(item, ["Isaac Lab v2.0 Release"]) is False
    assert scorer.score(item, ["Completely different topic"]) > 80
    assert scorer.is_novel(item, ["Completely different topic"]) is True
    assert scorer.score(item, []) == 100  # 无历史 -> 全新


# ---------------------------------------------------------------- Relevance
def test_relevance_full_score(config) -> None:
    evaluator = RelevanceEvaluator(config)
    item = make_item("Isaac Lab v2.0 Release", watched=True)
    res = evaluator.evaluate(item, keyword_hits=3, llm_relevance=80, novelty=90)
    # 0.25*100 + 0.25*100 + 0.30*80 + 0.20*90 = 92
    assert res["score"] == 92
    assert res["breakdown"]["source_score"] == 100  # watched


def test_relevance_renormalizes_without_llm(config) -> None:
    evaluator = RelevanceEvaluator(config)
    item = make_item("Isaac Lab v2.0 Release")  # github 非 watch
    res = evaluator.evaluate(item, keyword_hits=2, llm_relevance=None, novelty=90)
    assert res["breakdown"]["source_score"] == 90  # github source_weights
    # (0.25*70 + 0.25*90 + 0.20*90) / 0.7 = 58/0.7 = 82.86 -> 83
    assert res["score"] == 83


def test_keyword_bands(config) -> None:
    evaluator = RelevanceEvaluator(config)
    assert evaluator.keyword_score(0) == 0
    assert evaluator.keyword_score(1) == 40
    assert evaluator.keyword_score(5) == 100  # 超过上限取最高档


def test_routine_update_downgraded(config) -> None:
    """大型框架例行 release（watch + 版本号 + 与同源历史相似）应降权。"""
    evaluator = RelevanceEvaluator(config)
    history = [("github", "newton-physics/newton v1.4.0 Release")]
    routine = make_item("newton-physics/newton v1.5.0 Release", watched=True)
    assert evaluator.is_routine_update(routine, history) is True
    assert evaluator.source_score(routine, history) == 50  # 100 -> 50（例行降权）
    # 无历史时不误伤（首次里程碑 / 兼容旧调用）
    assert evaluator.source_score(routine, None) == 100

    # 论文/新具体工作不受影响
    paper = make_item("Real2Sim Benchmark for Grasping", watched=False)
    assert evaluator.is_routine_update(paper, history) is False

    # same 参数下，识别为例行更新的总分会更低
    res_routine = evaluator.evaluate(routine, keyword_hits=2, llm_relevance=80, novelty=10,
                                     existing_titles=history)
    res_plain = evaluator.evaluate(routine, keyword_hits=2, llm_relevance=80, novelty=10,
                                   existing_titles=None)
    assert res_routine["score"] < res_plain["score"]
    # 例行更新进入"普通信息"档，而非重点
    assert res_routine["score"] < 75


def test_routine_update_major_change_exempt(config) -> None:
    """主版本变化（v2.x -> v3.x）视为重大发布，不判例行。"""
    evaluator = RelevanceEvaluator(config)
    history = [("github", "isaac-sim/IsaacLab v2.0.0 Release")]
    v3 = make_item("isaac-sim/IsaacLab v3.0.0 Release", watched=True)
    assert evaluator.is_routine_update(v3, history) is False
    assert evaluator.source_score(v3, history) == 100


def test_routine_update_milestone_marker_exempt(config) -> None:
    """EA 标记（如 v3.0.0-EA）豁免；同主版本的 patch 仍判例行。"""
    evaluator = RelevanceEvaluator(config)
    history = [("github", "isaac-sim/IsaacLab v3.0.0 Release")]
    ea = make_item("isaac-sim/IsaacLab v3.0.0-EA Release", watched=True)
    assert evaluator.is_routine_update(ea, history) is False  # EA 标记豁免
    patch = make_item("isaac-sim/IsaacLab v3.0.1 Release", watched=True)
    assert evaluator.is_routine_update(patch, history) is True  # 同主版本补丁 -> 例行


# ---------------------------------------------------------------- Summarizer
class FakeLLMClient:
    def chat_json(self, system, user, json_mode=True):
        return {
            "summary": "多物理后端融合框架",
            "problem": "单物理后端局限",
            "solution": "统一后端抽象",
            "innovation": ["统一 API", "多后端调度"],
            "technical_details": ["C++ 核心", "GPU 加速"],
            "comparison": "优于旧方案",
            "open_source": True,
            "maturity": "release",
            "categories": ["simulation_engine"],
            "keywords": ["physics backend"],
            "related_projects": ["Isaac Lab", "Newton"],
            "platform_relevance": "影响 FunLab PhysicsBackend",
            "recommended_action": "POC",
            "overall_relevance": 85,
            "relevance": {"funlab": 5, "simulation_platform": 5, "real2sim": 1,
                          "engineering_machinery": 2, "robot_learning": 4},
            "_usage": {"total_tokens": 321},
        }


def test_summarizer_analyze(config) -> None:
    item = make_item("Isaac Lab v2.0 Release", abstract=(
        "This open-source release introduces a unified physics backend API for robot simulation. "
        "The documented implementation uses a C++ core and GPU acceleration, with example scenes "
        "and a reproducible benchmark for evaluating several physics solvers."
    ))
    analysis = Summarizer(FakeLLMClient(), config).analyze(item)
    assert analysis["summary"]
    assert analysis["open_source"] is True
    assert analysis["overall_relevance"] == 85
    assert analysis["relevance"]["funlab"] == 5
    assert analysis["innovation"] == ["统一 API", "多后端调度"]
    assert analysis["llm_error"] is None
    assert analysis["_usage"]["total_tokens"] == 321


def test_summarizer_tolerates_missing_summary(config) -> None:
    """模型输出缺 summary 但有实质内容：用其它字段兜底，仍算成功分析。"""

    class NoSummaryLLM:
        def chat_json(self, system, user, json_mode=True):
            return {
                "problem": "解决什么问题", "solution": "核心技术方案",
                "innovation": ["创新点 A"], "overall_relevance": 60,
                "relevance": {"funlab": 4}, "_usage": {"total_tokens": 3},
            }

    item = make_item("X update", "physics engine for robot learning real2sim sim2real")
    analysis = Summarizer(NoSummaryLLM(), config).analyze(item)
    assert analysis["summary"]  # 兜底非空
    assert analysis["solution"] == "核心技术方案"


def test_summarizer_rejects_empty_shell(config) -> None:
    """模型输出完全空壳（无任何有效字段）：判失败。"""
    import pytest

    from techradar.llm.client import LLMResponseError

    class EmptyLLM:
        def chat_json(self, system, user, json_mode=True):
            return {"summary": "", "categories": []}

    item = make_item("Y update", "physics engine for robot learning real2sim sim2real")
    with pytest.raises(LLMResponseError):
        Summarizer(EmptyLLM(), config).analyze(item)


def test_coerce_analysis_defaults() -> None:
    analysis = coerce_analysis({"summary": "s", "open_source": "true", "relevance": {"funlab": "4"}})
    assert analysis["summary"] == "s"
    assert analysis["open_source"] is True
    assert analysis["relevance"] == {"funlab": 4}
    assert analysis["innovation"] == []
    assert analysis["overall_relevance"] == 0
    assert analysis["llm_error"] is None


def test_coerce_analysis_garbage() -> None:
    analysis = coerce_analysis({"summary": None, "innovation": "a,b", "overall_relevance": 999})
    assert analysis["summary"] == ""
    assert analysis["innovation"] == ["a", "b"]
    assert analysis["overall_relevance"] == 100  # clamp


def test_history_excludes_identity_and_keeps_other_matching_items(config) -> None:
    scorer = NoveltyScorer(config)
    item = make_item("Isaac Lab v2.0 Release")
    assert scorer.score(item, [item]) == 100
    duplicate = TechItem(source_type="rss", source_name="blog", title=item.title, url="https://e/other")
    assert scorer.score(item, [item, duplicate]) == 0


def test_routine_release_uses_same_repository_history(config) -> None:
    evaluator = RelevanceEvaluator(config)
    old = make_item("IsaacLab v2.0.0 Release", watched=True, repo="isaac-sim/IsaacLab")
    other = make_item("MuJoCo v3.0.0 Release", watched=True, repo="google-deepmind/mujoco")
    major = make_item("IsaacLab v3.0.0 Release", watched=True, repo="isaac-sim/IsaacLab")
    assert not evaluator.is_routine_update(major, [old, other, major])
    patch = make_item("IsaacLab v3.0.1 Release", watched=True, repo="isaac-sim/IsaacLab")
    assert evaluator.is_routine_update(patch, [major, other, patch])


def test_model_output_keys_and_enums_are_canonical(config) -> None:
    analysis = coerce_analysis({
        "summary": "Useful source-grounded summary",
        "categories": ["Simulation Engine", "simulation-engine", "not_in_taxonomy"],
        "maturity": "production-grade", "recommended_action": "MERGE_NOW",
        "open_source": "unknown", "overall_relevance": float("nan"),
        "relevance": {"Simulation Platform": "4.5", "fake_project": 5, "funlab": "NaN"},
    }, categories=[key for key, _ in config.categories()],
       projects=config.projects.get("projects", {}).keys(), strict=True)
    assert analysis["categories"] == ["simulation_engine"]
    assert analysis["maturity"] == "unknown"
    assert analysis["recommended_action"] == "WATCH"
    assert analysis["open_source"] is None
    assert analysis["overall_relevance"] == 0
    assert analysis["relevance"] == {"simulation_platform": 4}
    assert analysis["validation_warnings"]


@pytest.mark.parametrize("raw", [{}, {"summary": ""}, {"summary": {}}, []])
def test_invalid_model_analysis_is_rejected(raw) -> None:
    with pytest.raises(AnalysisValidationError):
        coerce_analysis(raw, strict=True)


def test_analysis_prompt_preserves_body_and_url() -> None:
    from techradar.llm.prompts import build_analysis_user
    item = make_item("Physics release")
    item.raw_text = "A complete source body with supported implementation details."
    prompt = build_analysis_user(item)
    assert item.raw_text in prompt
    assert item.url in prompt


def test_limited_evidence_restricts_technical_conclusions(config) -> None:
    item = make_item("Physics release", abstract="physics engine")
    analysis = Summarizer(FakeLLMClient(), config).analyze(item)
    assert analysis["summary"]
    assert analysis["recommended_action"] == "WATCH"
    assert analysis["maturity"] == "unknown"
    assert analysis["open_source"] is None
    assert analysis["innovation"] == []
    assert analysis["technical_details"] == []
    assert analysis["evidence"]["quality"] == "limited"


def test_invalid_analysis_retries_format_once(config) -> None:
    class FormattingClient(FakeLLMClient):
        def __init__(self):
            self.modes = []
        def chat_json(self, system, user, json_mode=True):
            self.modes.append(json_mode)
            return {} if json_mode else super().chat_json(system, user, json_mode=False)
    client = FormattingClient()
    result = Summarizer(client, config).analyze(make_item("Physics engine", abstract="physics engine"))
    assert result["summary"]
    assert client.modes == [True, False]


def test_unusable_analysis_is_a_failure_after_format_retry(config) -> None:
    from techradar.llm.client import LLMError
    class EmptyClient:
        def chat_json(self, *args, **kwargs):
            return {"summary": ""}
    with pytest.raises(LLMError):
        Summarizer(EmptyClient(), config).analyze(make_item("Physics engine", abstract="physics engine"))
