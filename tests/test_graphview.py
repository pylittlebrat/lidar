"""图谱渲染器测试：HTML 结构、过滤面板、离线库、物理参数。"""

from __future__ import annotations

from techradar.config import Config
from techradar.dashboard.graphview import (
    build_options,
    build_vis_edges,
    build_vis_nodes,
    render_graph_html,
)
from techradar.knowledge import build_cooccurrence_graph
from techradar.storage.models import TechItem
from techradar.storage.repository import AnalysisRepository, ItemRepository


def _seed(db) -> None:
    item = TechItem(
        source_type="github", source_name="IsaacLab",
        title="isaac-sim/IsaacLab v3.0.0-EA Release",
        url="https://github.com/isaac-sim/IsaacLab/releases/tag/v3.0.0-EA",
        abstract="multi-physics backend robot learning real2sim",
        metadata={"repo": "isaac-sim/IsaacLab", "watched": True},
    )
    ItemRepository(db).insert(item)
    AnalysisRepository(db).upsert_analysis(item.id, {
        "summary": "多物理后端", "categories": ["simulation_engine", "机器人学习"],
        "keywords": ["Isaac Lab", "physics backend"], "score": 88,
    })
    AnalysisRepository(db).upsert_relevance(item.id, {"funlab": 5})


def test_vis_nodes_and_edges(config: Config, database) -> None:
    _seed(database)
    graph = build_cooccurrence_graph(config, database, days=None, min_score=0,
                                     limit=100, min_cooccur=1)
    nodes = build_vis_nodes(graph)
    edges = build_vis_edges(graph)
    assert nodes and edges
    for node in nodes:
        assert node["color"]["background"].startswith("#")
        assert node["size"] >= 7
        assert node["fullLabel"]          # 完整标题始终保留（悬停/检索用）
        # 标签策略：骨架节点显示标签，关键词/情报节点默认隐藏以免糊成一片
        if node["kind"] in ("project", "category", "repo", "source"):
            assert node["label"], f"{node['kind']} 应有常驻标签"
        else:
            assert node["label"] == "", f"{node['kind']} 默认不应显示标签"
        # 全部节点使用 3D 球体自定义绘制，不再使用五角星/六边形等平面图元
        assert node["shape"] == "sphere3d"
        assert {"base", "hi", "mid", "lo", "depth"} <= set(node["palette"])
        assert node["palette"]["hi"] != node["palette"]["base"]
    # 虚线关系使用虚线
    assert any(e["dashes"] for e in edges)
    # 边有稳定 id，供交互高亮更新
    assert all("id" in e for e in edges)


def test_html_uses_sphere_renderer(config: Config, database) -> None:
    """HTML 中应注册并使用 3D 球体渲染器（高光/阴影/渐变）。"""
    _seed(database)
    graph = build_cooccurrence_graph(config, database, days=None, min_score=0,
                                     limit=100, min_cooccur=1)
    html = render_graph_html(graph, height=500)
    assert "sphereRenderer" in html          # 渲染函数存在
    assert "createRadialGradient" in html     # 径向渐变（球体体积感）
    assert "ctxRenderer" in html              # 挂到 vis 节点上
    assert "sphere3d" in html


def test_render_html_contains_panel_and_legend(config: Config, database) -> None:
    _seed(database)
    graph = build_cooccurrence_graph(config, database, days=None, min_score=0,
                                     limit=100, min_cooccur=1)
    html = render_graph_html(graph, height=500)
    assert 'class="gpanel"' in html          # 左筛选面板
    assert 'class="glegend"' in html          # 可点击图例
    assert 'class="gsearch"' in html          # 搜索框
    assert 'class="gcard"' in html            # 详情卡
    assert "forceAtlas2Based" in html         # 力导向布局
    assert "height:500px" in html
    # 内联库或 CDN 二者其一
    assert ("vis-network" in html) or ("DataSet" in html)


def test_options_scale_with_size() -> None:
    small = build_options(50)
    large = build_options(500)
    assert (large["physics"]["forceAtlas2Based"]["springLength"]
            > small["physics"]["forceAtlas2Based"]["springLength"])
    assert large["interaction"]["hideEdgesOnDrag"] is True
    assert small["interaction"]["hideEdgesOnDrag"] is False