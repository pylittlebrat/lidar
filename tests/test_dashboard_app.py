"""Dashboard App 冒烟测试：逐个渲染工作台页面，断言无运行异常。

用 streamlit.testing.v1.AppTest 在内存中真实执行 app.py 并切换页面，
可捕获此前 bare-import 发现不了的页面级错误（如格式化/语法问题）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from techradar.storage.database import Database
from techradar.storage.models import TechItem
from techradar.storage.repository import AnalysisRepository, ItemRepository

APP_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "techradar" / "dashboard" / "app.py"
)
PAGES = ["情报概览", "技术雷达", "情报库", "技术趋势", "研究报告", "技术问答"]


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    """在 tmp 数据目录建一个带样例数据的 radar.db，并通过 env 注入 app。"""
    data_dir = tmp_path / "data"
    db = Database(data_dir / "radar.db")
    db.init()
    item = TechItem(
        source_type="arxiv", source_name="arXiv", title="Real2Sim Benchmark Paper",
        url="https://arxiv.org/abs/1", abstract="real2sim benchmark physics engine",
    )
    ItemRepository(db).insert(item)
    AnalysisRepository(db).upsert_analysis(item.id, {
        "summary": "Real2Sim 基准", "categories": ["real2sim"],
        "score": 88, "recommended_action": "INVESTIGATE",
    })
    monkeypatch.setenv("TECHRADAR_DATA_DIR", str(data_dir))
    return db


def test_all_dashboard_pages_render(seeded_db) -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=60)
    app.run()  # 默认情报概览
    assert not app.exception, app.exception
    for page in PAGES[1:]:
        app.sidebar.radio[0].set_value(page)
        app.run()
        assert not app.exception, f"页面 {page} 异常: {app.exception}"
