"""技术星图：唯一主归属、跨标签航线与随缩放展开的信息层级。"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from techradar.knowledge import CATEGORY_LABELS

CATEGORY_HUES = {
    "simulation_engine": 204, "robot_learning": 164, "simulation_validation": 43,
    "real2sim": 262, "embodied_ai": 228, "sim2real": 24, "manipulation": 188,
    "world_model": 282, "asset_generation": 146, "humanoid": 335,
    "system_identification": 56, "engineering_machinery": 33, "simulation_platform": 210,
    "uncategorized": 215,
}


def build_galaxy(graph: dict[str, Any], *, min_items: int = 1,
                 max_items_per_galaxy: int = 60) -> dict[str, Any]:
    """一条情报只绘制一次，副标签仍贡献共现与详情中的跨星系连接。

    优先使用逐标签权重（category_scores / belongs_to.weight），没有权重时
    使用已有分类顺序中的首标签，不把情报总评分冒充为标签置信度。
    items 保留全部主归属情报供搜索和列表读取；satellites 兼容旧调用方。
    """
    nodes = {n["id"]: n for n in graph["nodes"] if n.get("kind") == "item"}
    category_nodes = {n["id"].split(":", 1)[1]: n["label"]
                      for n in graph["nodes"] if n.get("kind") == "category"}
    memberships: dict[str, dict[str, float]] = defaultdict(dict)
    for edge in graph["edges"]:
        if (edge["relation"] != "belongs_to" or
                not edge["to"].startswith("category:") or edge["from"] not in nodes):
            continue
        cat = edge["to"].split(":", 1)[1]
        weights = memberships[edge["from"]]
        weights[cat] = max(weights.get(cat, 0), float(edge.get("weight", 1)))

    primary: dict[str, list[dict[str, Any]]] = defaultdict(list)
    tagged: Counter[str] = Counter()
    pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
    cross_items = 0
    unassigned = set(nodes) - set(memberships)
    for item_id in unassigned:
        # 未分类条目也是真实情报，不能从全库星图中消失。
        memberships[item_id] = {"uncategorized": 1.0}
    for item_id, weights in memberships.items():
        node = nodes[item_id]
        categories = [] if item_id in unassigned else list(weights)
        scores = node.get("category_scores") or {}
        ranked = scores if any(c in scores for c in categories) else weights
        main = max(weights, key=lambda c: float(ranked.get(c, 0)))
        tagged.update(weights.keys())
        if len(categories) > 1:
            cross_items += 1
        for i, a in enumerate(sorted(categories)):
            for b in sorted(categories)[i + 1:]:
                pairs[(a, b)].append(item_id)
        primary[main].append({
            "id": item_id, "label": node["label"], "score": node.get("score") or 0,
            "scored": node.get("score") is not None,
            "url": node.get("url", ""), "summary": node.get("summary", ""),
            "source_type": node.get("source_type", ""),
            "category": main, "categories": categories,
        })

    galaxies = []
    for cat, tagged_count in tagged.items():
        if tagged_count < min_items:
            continue
        items = sorted(primary[cat], key=lambda n: (-n["score"], n["id"]))
        galaxies.append({
            "id": f"galaxy:{cat}", "category": cat,
            "label": "待分类" if cat == "uncategorized" else CATEGORY_LABELS.get(cat, category_nodes.get(cat, cat)),
            "hue": CATEGORY_HUES.get(cat, 210), "count": len(items),
            "tagged_count": tagged_count, "items": items,
            "core": items[0] if items else None,
            "satellites": items[1:max(0, max_items_per_galaxy) + 1],
        })
    galaxies.sort(key=lambda g: (-g["count"], g["category"]))
    known = {g["category"] for g in galaxies}
    bridges = [
        {"source": a, "target": b, "weight": len(ids), "items": ids}
        for (a, b), ids in sorted(pairs.items(), key=lambda p: (-len(p[1]), p[0]))
        if a in known and b in known
    ]
    return {
        "galaxies": galaxies, "bridges": bridges,
        "stats": {
            "galaxies": len(galaxies), "items": sum(g["count"] for g in galaxies),
            "bridges": len(bridges), "cross_items": cross_items,
            "unassigned": len(unassigned),
        },
    }


def render_galaxy_html(galaxy: dict[str, Any], height: int = 690) -> str:
    """生成无需网络依赖的完整星图组件（Canvas + 可访问 DOM 导航）。"""
    base = Path(__file__).parent
    css = (base / "galaxy.css").read_text(encoding="utf-8")
    js = "\n".join((base / name).read_text(encoding="utf-8")
                   for name in ("galaxy-space.js", "galaxy-scene.js", "galaxy.js"))
    payload = json.dumps(galaxy, ensure_ascii=False).replace("<", "\\u003c")
    payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><style>{css}</style></head>
<body><section class="gxwrap" style="height:{max(480, int(height))}px" aria-label="技术知识星图">
  <header class="gxtoolbar">
    <div class="gxidentity"><span class="gxmark">✦</span><b>技术星图</b><span class="gxedition">3D KNOWLEDGE ATLAS</span><span class="gx3d">3D</span></div>
    <label class="gxsearch"><span aria-hidden="true">⌕</span><input id="gxsearch" type="search" placeholder="搜索方向、项目或论文…" aria-label="搜索星图" autocomplete="off"><kbd>/</kbd></label>
    <button id="gxrelations" class="gxquiet" aria-pressed="false" title="显示全部跨方向关系">关系航线</button>
  </header>
  <div class="gxbody">
    <main class="gxstage">
      <div class="gxcrumb"><button id="gxhome">全部星系</button><span id="gxcrumbtext"> / 技术全景</span></div>
      <div class="gxscene-label"><span id="gxlevel">01 / 全景</span><p id="gxscenehint">拖拽旋转，探索技术宇宙</p></div>
      <button id="gxdetails" class="gxdetail-toggle" aria-expanded="true">收起详情</button>
      <canvas id="gxcanvas" tabindex="0" aria-label="3D 星图画布。拖拽旋转，Shift 拖拽平移，滚轮推进；方向键旋转，也可使用方向列表和缩放按钮。"></canvas>
      <div class="gxtooltip" role="tooltip" hidden></div>
      <div class="gxempty" hidden>这个范围内还没有星系<span>试试调整项目、时间范围或最低评分</span></div>
      <div class="gxzoom"><button id="gxout" aria-label="缩小">−</button><output id="gxscale">100%</output><button id="gxin" aria-label="放大">+</button><span></span><button id="gxfit" class="gxmode gxreset" aria-label="重置视角" title="恢复默认朝向、缩放和全景 · Esc">↺ 重置视角</button><span></span><button id="gxpan" class="gxmode" aria-pressed="false" title="切换拖拽模式；也可按住 Shift 平移">平移</button><button id="gxorbit" class="gxmode" aria-pressed="false" title="缓慢环绕；点击星系或拖拽停止">自动环绕</button></div>
      <div class="gxmap-caption">PERSPECTIVE / ORBIT<span>拖拽旋转 · Shift 平移 · 滚轮推进</span></div>
    </main>
    <aside class="gxinfo" aria-label="星系导航与情报详情"><div id="gxpanel"></div></aside>
  </div>
  <footer class="gxfooter"><span id="gxstats"></span><span class="gxkey" title="同一视距下，评分越高，星球越大"><i class="gxkey-core"></i>高评分<i class="gxkey-star"></i>低评分<i class="gxkey-line"></i>跨方向关联</span><span class="gxgesture">拖拽旋转 · 滚轮推进</span></footer>
  <div id="gxannounce" class="gxsr" role="status" aria-live="polite"></div>
</section><script>{js.replace('__DATA__', payload)}</script></body></html>'''
