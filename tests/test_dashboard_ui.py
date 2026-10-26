"""Exercise navigation and filters without a browser or live LLM requests."""
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest
from techradar.storage.models import TechItem
from techradar.storage.repository import ItemRepository, AnalysisRepository

APP = Path(__file__).parents[1] / "src/techradar/dashboard/app.py"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("TECHRADAR_DATA_DIR", str(tmp_path))
    import streamlit as st
    st.cache_resource.clear()
    from techradar.storage.database import Database
    db = Database(tmp_path / "radar.db")
    db.init()
    for index in range(15):
        item = TechItem(source_type="github", source_name="test", title=f"Physics engine {index}", url=f"https://example.com/{index}", abstract="Simulation research")
        ItemRepository(db).insert(item)
        if index < 2:
            AnalysisRepository(db).upsert_analysis(item.id, {"score": 90 - index, "summary": "Research summary", "categories": ["simulation_engine"]})
    yield AppTest.from_file(str(APP), default_timeout=20).run()
    st.cache_resource.clear()


def test_navigation_filters_pagination_and_reports(app):
    assert not app.exception
    next(b for b in app.button if b.label == "浏览全部情报 →").click().run()
    assert not app.exception
    assert app.radio(key="page").value == "情报库"
    app.number_input(key="library_page").set_value(2).run()
    assert not app.exception
    app.selectbox(key="status_filter").select("重点关注").run()
    assert not app.exception
    assert app.session_state["library_page"] == 1
    assert len(app.expander) == 2
    app.text_input[0].set_value("no matching data").run()
    assert not app.exception
    assert len(app.expander) == 0
    for page in ["技术趋势", "研究报告", "技术问答", "情报概览"]:
        app.radio(key="page").set_value(page).run()
        assert not app.exception
    next(b for b in app.button if b.label == "研究论文 →").click().run()
    assert app.selectbox(key="source_filter").value == "arxiv"
    assert not app.exception


def test_question_examples_answer_and_error(app):
    app.radio(key="page").set_value("技术问答").run()
    next(b for b in app.button if b.label == "最近有哪些值得调研的 Real2Sim 技术？").click().run()
    assert "Real2Sim" in app.text_area[0].value
    with patch("techradar.search.ask", return_value={"answer": "Example answer", "sources": []}):
        next(b for b in app.button if b.label == "检索并回答").click().run()
    assert not app.exception
    assert any(m.value == "Example answer" for m in app.markdown)
    with patch("techradar.search.ask", side_effect=RuntimeError("secret must not be displayed")):
        next(b for b in app.button if b.label == "检索并回答").click().run()
    assert not app.exception
    assert app.error
    assert "secret" not in app.error[0].value


def test_empty_library(tmp_path, monkeypatch):
    import streamlit as st
    st.cache_resource.clear()
    monkeypatch.setenv("TECHRADAR_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    assert not app.exception
    assert all(m.value == "0" for m in app.metric)
    for page in ["情报库", "技术趋势"]:
        app.radio(key="page").set_value(page).run()
        assert not app.exception
    st.cache_resource.clear()


def test_sync_button_starts_once_and_disables_while_running(app):
    state = {"status": "idle"}
    def start(*args):
        state.update(status="running", id="test-run", stage="采集最新资料", progress=0)
        return "test-run"
    with patch("techradar.sync.get_status", side_effect=lambda db: dict(state)), patch("techradar.sync.start_sync", side_effect=start) as launch:
        app.run()
        next(b for b in app.button if b.label == "刷新 · 同步最新情报").click().run()
        assert not app.exception
        assert launch.call_count == 1
        assert next(b for b in app.button if b.label == "正在同步…").disabled
        state.update(status="success", finished_at="2026-09-28T10:00:00+00:00", collect={"github": {"inserted": 2}}, analyze={"stored": 2})
        app.run()
        assert not app.exception
        assert any("同步完成" in message.value for message in app.success)
        assert launch.call_count == 1


def test_analysis_quality_filters_and_historical_notice(app):
    import os
    from techradar.storage.database import Database
    db = Database(Path(os.environ["TECHRADAR_DATA_DIR"]) / "radar.db")
    for index, status in enumerate(("llm", "skipped", "llm_failed")):
        item = TechItem(source_type="arxiv", source_name="test", title=f"Quality {status}", url=f"https://example.com/quality/{index}", abstract="physics evidence")
        ItemRepository(db).insert(item)
        AnalysisRepository(db).upsert_analysis(item.id, {"llm_status": status, "score": 80, "last_error": "Provider temporarily unavailable" if status == "llm_failed" else "", "attempts": 2})
    app.run()
    assert not app.exception
    # 首屏改用仪表读数条（替代原先的指标卡）：模型分析计数应体现在读数条中
    strips = "\n".join(m.value for m in app.markdown)
    assert "模型分析" in strips
    next(b for b in app.button if b.label == "浏览全部情报 →").click().run()
    app.selectbox(key="status_filter").select("失败待重试").run()
    assert not app.exception
    assert any("模型分析未完成" in c.value for c in app.caption)
    assert any("Provider temporarily unavailable" in m.value for m in app.markdown)
    app.selectbox(key="status_filter").select("模型分析").run()
    assert any("尚未按当前规则重新分析" in c.value for c in app.caption)
    app.selectbox(key="status_filter").select("规则评分").run()
    assert any("尚无模型技术判断" in c.value for c in app.caption)


def test_partial_sync_and_history_show_failure_details(app):
    state = {"id": "partial-test", "status": "partial", "started_at": "2026-09-30T00:30:00+00:00", "finished_at": "2026-09-30T00:35:00+00:00", "warnings": ["RSS source unavailable"], "failures": ["Analysis retry scheduled"]}
    with patch("techradar.sync.get_status", return_value=state), patch("techradar.sync.get_history", return_value=[state]):
        app.run()
    assert not app.exception
    assert any("部分步骤未完成" in w.value for w in app.warning)
    assert any("Analysis retry scheduled" in m.value for m in app.markdown)
    assert any(e.label == "最近同步记录" for e in app.expander)
