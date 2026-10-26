"""UI 优化后的结构测试：决策卡、今日重点、图谱视角。"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from techradar.storage.database import Database
from techradar.storage.models import TechItem
from techradar.storage.repository import AnalysisRepository, ItemRepository

APP_PATH = Path(__file__).resolve().parents[1] / "src" / "techradar" / "dashboard" / "app.py"
PAGES = ["情报概览", "技术雷达", "知识图谱", "情报库", "技术趋势", "研究报告", "技术问答"]


def _seed(db: Database) -> None:
    item = TechItem(
        source_type="github", source_name="IsaacLab",
        title="isaac-sim/IsaacLab v3.0.0-EA Release",
        url="https://github.com/isaac-sim/IsaacLab/releases/tag/v3.0.0-EA",
        abstract="multi-physics backend for robot learning real2sim system identification",
        metadata={"repo": "isaac-sim/IsaacLab", "watched": True},
    )
    ItemRepository(db).insert(item)
    AnalysisRepository(db).upsert_analysis(item.id, {
        "summary": "多物理后端架构，统一任务 API", "categories": ["simulation_engine", "robot_learning"],
        "keywords": ["Isaac Lab", "physics backend"], "score": 88,
        "recommended_action": "INVESTIGATE",
    })
    AnalysisRepository(db).upsert_relevance(item.id, {"funlab": 5, "simulation_platform": 5})


def test_all_pages_render(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    db = Database(data_dir / "radar.db")
    db.init()
    _seed(db)
    monkeypatch.setenv("TECHRADAR_DATA_DIR", str(data_dir))

    app = AppTest.from_file(str(APP_PATH), default_timeout=90)
    app.run()
    assert not app.exception, app.exception
    # 首屏按工作流组织：今日重点 / 新进入雷达 作为分区标题
    headings = "\n".join(s.value for s in app.subheader)
    assert "今日重点" in headings

    for page in PAGES[1:]:
        app.sidebar.radio[0].set_value(page)
        app.run()
        assert not app.exception, f"页面 {page} 异常: {app.exception}"


def test_knowledge_graph_has_unified_galaxy_entry(tmp_path, monkeypatch) -> None:
    """图谱统一使用星系；原项目视角收进筛选，不保留网络切换。"""
    data_dir = tmp_path / "data"
    db = Database(data_dir / "radar.db")
    db.init()
    _seed(db)
    monkeypatch.setenv("TECHRADAR_DATA_DIR", str(data_dir))

    app = AppTest.from_file(str(APP_PATH), default_timeout=90)
    app.run()
    app.sidebar.radio[0].set_value("知识图谱")
    app.run()
    assert not app.exception, app.exception
    assert all(r.label != "从哪个视角看" for r in app.radio)
    assert any(s.label == "项目视角" for s in app.selectbox)
    assert "3D KNOWLEDGE ATLAS" in app.get("iframe")[0].proto.srcdoc
