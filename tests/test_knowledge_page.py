"""知识图谱页面冒烟测试：AppTest 真实渲染该页。"""

from __future__ import annotations

from pathlib import Path
from datetime import timedelta
import json

from streamlit.testing.v1 import AppTest

from techradar.storage.database import Database
from techradar.storage.models import TechItem
from techradar.storage.repository import AnalysisRepository, ItemRepository
from techradar.utils.time import utcnow

APP_PATH = Path(__file__).resolve().parents[1] / "src" / "techradar" / "dashboard" / "app.py"


def _seed(db: Database) -> None:
    item = TechItem(
        source_type="github", source_name="IsaacLab",
        title="isaac-sim/IsaacLab v3.0.0-EA Release",
        url="https://github.com/isaac-sim/IsaacLab/releases/tag/v3.0.0-EA",
        abstract="multi-physics backend for robot learning real2sim",
        metadata={"repo": "isaac-sim/IsaacLab", "watched": True},
    )
    ItemRepository(db).insert(item)
    AnalysisRepository(db).upsert_analysis(item.id, {
        "summary": "多物理后端", "categories": ["simulation_engine", "robot_learning"],
        "keywords": ["Isaac Lab", "physics backend"], "score": 88,
        "recommended_action": "INVESTIGATE",
    })
    AnalysisRepository(db).upsert_relevance(item.id, {"funlab": 5, "simulation_platform": 5})


def test_knowledge_graph_page_renders(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    db = Database(data_dir / "radar.db")
    db.init()
    _seed(db)
    old_low = TechItem(source_type="github", source_name="old", title="Old low-score item",
                       url="https://e/old-low", collected_at=utcnow() - timedelta(days=730))
    ItemRepository(db).insert(old_low)
    AnalysisRepository(db).upsert_analysis(old_low.id, {
        "summary": "old", "score": 20, "categories": ["simulation_engine"], "keywords": [],
    })
    pending = TechItem(source_type="github", source_name="pending", title="Unanalyzed item", url="https://e/pending")
    ItemRepository(db).insert(pending)
    unclassified = TechItem(source_type="github", source_name="unknown", title="Unclassified item", url="https://e/unknown")
    ItemRepository(db).insert(unclassified)
    AnalysisRepository(db).upsert_analysis(unclassified.id, {
        "summary": "unknown", "score": 40, "categories": [], "keywords": [],
    })
    monkeypatch.setenv("TECHRADAR_DATA_DIR", str(data_dir))

    app = AppTest.from_file(str(APP_PATH), default_timeout=90)
    app.run()
    app.sidebar.radio[0].set_value("知识图谱")
    app.run()
    assert not app.exception, app.exception
    titles = [t.value for t in app.title]
    assert any("知识图谱" in t for t in titles)
    assert all(r.label != "从哪个视角看" for r in app.radio)
    assert len(app.get("iframe")) == 1
    html = app.get("iframe")[0].proto.srcdoc
    assert "3D KNOWLEDGE ATLAS" in html
    assert "IsaacLab" in html
    payload = json.loads(html.split('  const DATA = ', 1)[1].split(';\n', 1)[0])
    assert payload["stats"]["items"] == payload["stats"]["library_items"] == 4
    assert payload["stats"]["unassigned"] == 2
    assert sum(g["count"] for g in payload["galaxies"]) == 4
    assert next(s for s in app.selectbox if s.label == "时间窗口").value == 0
    assert next(s for s in app.slider if s.label == "纳入情报的最低评分").value == 0

    # 项目视角仍可筛选同一个星系组件，不再切换成另一种网络图。
    project = next(s for s in app.selectbox if s.label == "项目视角")
    project.set_value("funlab")
    app.run()
    assert not app.exception, app.exception
    assert "IsaacLab" in app.get("iframe")[0].proto.srcdoc
    project = next(s for s in app.selectbox if s.label == "项目视角")
    project.set_value("engineering_machinery")
    app.run()
    assert not app.exception, app.exception
    html = app.get("iframe")[0].proto.srcdoc
    assert '"galaxies": []' in html
