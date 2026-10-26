"""知识图谱测试：实体/关系抽取、筛选、邻域、分类归一化。"""

from __future__ import annotations

from techradar.knowledge import build_cooccurrence_graph, build_graph, build_neighborhood
from techradar.utils.time import utcnow
from techradar.storage.models import TechItem
from techradar.storage.repository import AnalysisRepository, ItemRepository


def _seed(database) -> None:
    irepo = ItemRepository(database)
    arepo = AnalysisRepository(database)
    items = [
        TechItem(source_type="github", source_name="IsaacLab",
                 title="isaac-sim/IsaacLab v3.0.0-EA Release",
                 url="https://github.com/isaac-sim/IsaacLab/releases/tag/v3.0.0-EA",
                 abstract="multi-physics backend for robot learning",
                 metadata={"repo": "isaac-sim/IsaacLab", "watched": True}),
        TechItem(source_type="arxiv", source_name="arXiv",
                 title="Real2Sim Asset Generation via Gaussian Splatting",
                 url="https://arxiv.org/abs/2601.1",
                 abstract="real2sim gaussian splatting scene reconstruction"),
        TechItem(source_type="arxiv", source_name="arXiv",
                 title="System Identification for Excavator Dynamics",
                 url="https://arxiv.org/abs/2601.2",
                 abstract="system identification parameter estimation excavator"),
    ]
    for item in items:
        irepo.insert(item)
    arepo.upsert_analysis(items[0].id, {
        "summary": "多物理后端", "categories": ["simulation_engine"],
        "keywords": ["Isaac Lab", "physics backend"], "score": 88,
        "recommended_action": "INVESTIGATE",
    })
    arepo.upsert_relevance(items[0].id, {"funlab": 5, "simulation_platform": 5})
    arepo.upsert_analysis(items[1].id, {
        "summary": "real2sim 资产生成", "categories": ["real2sim", "asset_generation"],
        "keywords": ["real2sim", "3D Gaussian Splatting"], "score": 72,
    })
    arepo.upsert_relevance(items[1].id, {"real2sim": 5})
    arepo.upsert_analysis(items[2].id, {
        "summary": "系统辨识", "categories": ["system_identification", "engineering_machinery"],
        "keywords": ["system identification"], "score": 30,  # 低于默认阈值
    })
    arepo.upsert_relevance(items[2].id, {"engineering_machinery": 4})


def test_graph_nodes_and_edges(config, database) -> None:
    _seed(database)
    graph = build_graph(config, database, days=None, min_score=50, limit=100)
    kinds = {n["kind"] for n in graph["nodes"]}
    assert {"item", "repo", "category", "keyword", "project", "source"} <= kinds

    relations = {e["relation"] for e in graph["edges"]}
    assert {"belongs_to", "mentions", "relates_to", "from", "implements"} <= relations

    # 仓库节点来自 metadata.repo
    repo_nodes = [n for n in graph["nodes"] if n["kind"] == "repo"]
    assert any(n["label"] == "isaac-sim/IsaacLab" for n in repo_nodes)
    # 内部项目节点使用中文显示名
    project_nodes = {n["label"] for n in graph["nodes"] if n["kind"] == "project"}
    assert {"FunLab", "Real2Sim", "仿真平台"} & project_nodes


def test_graph_keeps_full_titles_summaries_and_all_tags(config, database) -> None:
    title = ("A long research title " * 8).strip()
    item = TechItem(source_type="arxiv", source_name="arxiv", title=title,
                    url="https://example.org/full-title", abstract="test")
    ItemRepository(database).insert(item)
    AnalysisRepository(database).upsert_analysis(item.id, {
        "summary": "完整摘要用于星图阅读和搜索。", "score": 80,
        "categories": ["simulation_engine", "robot_learning", "real2sim", "world_model"],
        "keywords": [],
    })
    graph = build_graph(config, database, days=None, min_score=0)
    node = next(n for n in graph["nodes"] if n["kind"] == "item")
    assert node["label"] == title
    assert node["summary"] == "完整摘要用于星图阅读和搜索。"
    tags = [e for e in graph["edges"] if e["relation"] == "belongs_to"]
    assert len(tags) == 4


def test_full_library_graph_includes_unanalyzed_and_has_no_row_cap(config, database) -> None:
    conn = database.connect()
    try:
        with conn:
            conn.executemany(
                "INSERT INTO items (id,title,url,source_type,source_name,collected_at,content_hash)"
                " VALUES (?,?,?,?,?,?,?)",
                [(f"bulk-{i}", f"Bulk Item {i}", f"https://e/bulk/{i}", "github", "test",
                  utcnow().isoformat(), f"hash-{i}") for i in range(905)],
            )
            conn.executemany(
                "INSERT INTO analysis (item_id,score,categories) VALUES (?,?,?)",
                [("bulk-0", 20, '["simulation_engine"]'), ("bulk-1", 88, '["robot_learning"]')],
            )
            conn.execute("INSERT INTO relevance (item_id,project_key,score) VALUES (?,?,?)",
                         ("bulk-904", "funlab", 5))
    finally:
        conn.close()
    full = build_graph(config, database, days=None, min_score=0, limit=None,
                       include_keywords=False, include_unanalyzed=True)
    assert full["stats"]["items"] == 905
    assert len([n for n in full["nodes"] if n["kind"] == "item"]) == 905
    assert any(e["from"] == "item:bulk-904" and e["relation"] == "relates_to" for e in full["edges"])
    analyzed = build_graph(config, database, days=None, min_score=0, limit=None)
    assert analyzed["stats"]["items"] == 2  # 保留其他调用方的分析范围默认值
    filtered = build_graph(config, database, days=None, min_score=55, limit=None, include_unanalyzed=True)
    assert filtered["stats"]["items"] == 1


def test_graph_min_score_filters(config, database) -> None:
    _seed(database)
    high = build_graph(config, database, days=None, min_score=50, limit=100)
    low = build_graph(config, database, days=None, min_score=0, limit=100)
    high_items = high["stats"]["by_kind"].get("item", 0)
    low_items = low["stats"]["by_kind"].get("item", 0)
    assert high_items == 2 and low_items == 3  # 30 分条目被阈值过滤
    assert high["stats"]["edges"] < low["stats"]["edges"]


def test_graph_keyword_toggle(config, database) -> None:
    _seed(database)
    with_kw = build_graph(config, database, days=None, min_score=0, limit=100, include_keywords=True)
    without_kw = build_graph(config, database, days=None, min_score=0, limit=100, include_keywords=False)
    assert with_kw["stats"]["by_kind"].get("keyword", 0) > 0
    assert without_kw["stats"]["by_kind"].get("keyword", 0) == 0


def test_neighborhood(config, database) -> None:
    _seed(database)
    neighbor = build_neighborhood(config, database, "category:real2sim", days=None, limit=100)
    assert neighbor["center"] == "category:real2sim"
    ids = {n["id"] for n in neighbor["nodes"]}
    assert "category:real2sim" in ids
    # 邻域内所有边都连着中心节点
    for edge in neighbor["edges"]:
        assert "category:real2sim" in (edge["from"], edge["to"])


def test_category_names_normalized(config, database) -> None:
    """LLM 返回中文分类名时，也应归一到配置 ID，避免同一方向出现两个节点。"""
    item = TechItem(source_type="arxiv", source_name="arXiv",
                    title="Mixed Category Naming", url="https://arxiv.org/abs/2601.9",
                    abstract="physics engine robot learning")
    ItemRepository(database).insert(item)
    AnalysisRepository(database).upsert_analysis(item.id, {
        "summary": "分类命名混用", "categories": ["机器人学习", "simulation_engine"],
        "keywords": ["physics engine"], "score": 80,
    })
    graph = build_graph(config, database, days=None, min_score=0, limit=100)
    cat_ids = [n["id"] for n in graph["nodes"] if n["kind"] == "category"]
    assert len(cat_ids) == len(set(cat_ids))          # 无重复 ID
    assert "category:robot_learning" in cat_ids        # 中文名已归一为配置 ID
    labels = {n["id"]: n["label"] for n in graph["nodes"]}
    assert labels["category:robot_learning"] == "机器人学习"


def test_skeleton_graph_cooccurrence(config, database) -> None:
    """骨架图：只含方向/项目/仓库/关键词，边来自共现。"""
    _seed(database)
    graph = build_cooccurrence_graph(config, database, days=None, min_score=0,
                                     limit=100, min_cooccur=1)
    kinds = {n["kind"] for n in graph["nodes"]}
    assert "item" not in kinds
    assert kinds <= {"category", "project", "repo", "keyword"}
    assert graph["edges"], "共现边不应为空"
    assert all(e["relation"] == "co_occurs" for e in graph["edges"])
