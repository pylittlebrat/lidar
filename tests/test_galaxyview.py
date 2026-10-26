"""星系图测试：按方向聚类、核心选择、星系间连线、渲染输出。"""

from __future__ import annotations

from techradar.config import Config
from techradar.dashboard.galaxyview import build_galaxy, render_galaxy_html
from techradar.knowledge import build_graph
from techradar.storage.models import TechItem
from techradar.storage.repository import AnalysisRepository, ItemRepository


def _seed(db) -> None:
    data = [
        ("https://e/1", "IsaacLab v3.0.0-EA Release", ["simulation_engine", "robot_learning"], 88),
        ("https://e/2", "Newton v1.7.0 Release", ["simulation_engine"], 76),
        ("https://e/3", "Real2Sim Asset Generation", ["real2sim", "asset_generation"], 72),
        ("https://e/4", "Excavator System Identification", ["system_identification", "real2sim"], 64),
    ]
    for url, title, cats, score in data:
        item = TechItem(source_type="github", source_name="x", title=title, url=url,
                        abstract="physics engine robot learning real2sim")
        ItemRepository(db).insert(item)
        AnalysisRepository(db).upsert_analysis(item.id, {
            "summary": title, "categories": cats, "keywords": ["physics"], "score": score,
        })


def test_galaxy_structure(config: Config, database) -> None:
    _seed(database)
    graph = build_graph(config, database, days=None, min_score=0, limit=100,
                        include_keywords=False)
    galaxy = build_galaxy(graph)
    stats = galaxy["stats"]
    assert stats["galaxies"] >= 3
    # 多标签不复制星点，统计采用唯一主归属。
    assert stats["items"] == 4
    assert stats["cross_items"] == 3
    ids = [n["id"] for g in galaxy["galaxies"] for n in g["items"]]
    assert len(ids) == len(set(ids)) == 4

    labels = {g["label"] for g in galaxy["galaxies"]}
    assert "仿真引擎" in labels and "Real2Sim" in labels

    # 核心 = 该方向评分最高的条目
    engine = next(g for g in galaxy["galaxies"] if g["category"] == "simulation_engine")
    assert engine["core"]["score"] == 88
    assert "IsaacLab" in engine["core"]["label"]
    assert engine["count"] == 2
    assert len(engine["satellites"]) == 1  # Newton 作为行星

    # 跨方向共现 -> 星系间连线（键按字母序规范存储）
    assert galaxy["bridges"], "应有星系间连线"
    pairs = {(b["source"], b["target"]) for b in galaxy["bridges"]}
    assert ("robot_learning", "simulation_engine") in pairs


def test_galaxy_render_html(config: Config, database) -> None:
    _seed(database)
    graph = build_graph(config, database, days=None, min_score=0, limit=100,
                        include_keywords=False)
    galaxy = build_galaxy(graph)
    html = render_galaxy_html(galaxy, height=500)
    assert 'id="gxcanvas"' in html
    assert "createRadialGradient" in html     # 恒星发光
    assert ".gxinfo" in html                   # 详情卡
    assert 'style="height:500px"' in html
    # 图例包含所有星系
    for g in galaxy["galaxies"]:
        assert g["label"] in html


def test_galaxy_satellites_limited(config: Config, database) -> None:
    """单个星系的行星数量受限，避免超大星系撑爆画布。"""
    for i in range(30):
        item = TechItem(source_type="github", source_name="x", title=f"Engine Tool {i}",
                        url=f"https://e/bulk/{i}", abstract="physics engine simulator")
        ItemRepository(database).insert(item)
        AnalysisRepository(database).upsert_analysis(item.id, {
            "summary": "t", "categories": ["simulation_engine"], "keywords": [], "score": 60 + i % 20,
        })
    graph = build_graph(config, database, days=None, min_score=0, limit=200,
                        include_keywords=False)
    galaxy = build_galaxy(graph, max_items_per_galaxy=10)
    engine = next(g for g in galaxy["galaxies"] if g["category"] == "simulation_engine")
    assert engine["count"] == 30
    assert len(engine["satellites"]) == 10
    assert len(engine["items"]) == 30  # 全部情报仍能搜索和浏览


def test_primary_category_weights_and_secondary_only_galaxies() -> None:
    graph = {
        "nodes": [{"id": "item:1", "kind": "item", "label": "A", "score": 92},
                  {"id": "item:2", "kind": "item", "label": "B", "score": 65,
                   "category_scores": {"simulation_engine": .9, "robot_learning": .3}}],
        "edges": [
            {"from": "item:1", "to": "category:simulation_engine", "relation": "belongs_to", "weight": .2},
            {"from": "item:1", "to": "category:robot_learning", "relation": "belongs_to", "weight": .8},
            {"from": "item:1", "to": "category:robot_learning", "relation": "belongs_to", "weight": .8},
            {"from": "item:2", "to": "category:robot_learning", "relation": "belongs_to"},
            {"from": "item:2", "to": "category:simulation_engine", "relation": "belongs_to"},
            {"from": "item:2", "to": "category:real2sim", "relation": "belongs_to"},
        ],
    }
    galaxy = build_galaxy(graph)
    groups = {g["category"]: g for g in galaxy["galaxies"]}
    assert groups["robot_learning"]["core"]["id"] == "item:1"
    assert groups["simulation_engine"]["core"]["id"] == "item:2"
    assert groups["real2sim"]["count"] == 0
    assert groups["real2sim"]["tagged_count"] == 1
    assert groups["real2sim"]["core"] is None
    bridge = next(b for b in galaxy["bridges"] if {b["source"], b["target"]} == {"robot_learning", "simulation_engine"})
    assert bridge["weight"] == 2  # 重复边不重复计数
    assert set(bridge["items"]) == {"item:1", "item:2"}


def test_empty_and_unassigned_items() -> None:
    galaxy = build_galaxy({"nodes": [{"id": "item:1", "label": "unknown", "kind": "item"}], "edges": []})
    assert galaxy["stats"]["items"] == 1
    assert galaxy["stats"]["unassigned"] == 1
    group = galaxy["galaxies"][0]
    assert group["label"] == "待分类" and group["count"] == 1
    assert group["items"][0]["categories"] == []
    assert group["items"][0]["scored"] is False
    assert galaxy["bridges"] == []
    empty = build_galaxy({"nodes": [], "edges": []})
    assert empty["stats"]["items"] == 0 and empty["galaxies"] == []
    assert 'class="gxempty"' in render_galaxy_html(galaxy)


def test_render_does_not_allow_script_breakout() -> None:
    graph = {"nodes": [{"id": "item:1", "kind": "item", "label": "</script><script>alert(1)</script>"}],
             "edges": [{"from": "item:1", "to": "category:simulation_engine", "relation": "belongs_to"}]}
    html = render_galaxy_html(build_galaxy(graph))
    assert "</script><script>alert" not in html
    assert "\\u003c/script>" in html
