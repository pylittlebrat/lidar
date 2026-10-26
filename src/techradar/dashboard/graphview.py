"""知识图谱渲染器：自定义 HTML/CSS/JS（不依赖 pyvis 默认外观）。

设计参考成熟图谱产品（Neo4j Bloom / Obsidian Graph / Gephi）的常见做法：
- 三栏布局：左侧类型过滤与搜索，中间画布，右侧详情卡
- 深色画布 + 高对比节点，标签按缩放级别自动隐藏，避免文字糊成一片
- 按类型分组着色 + 图例可点击过滤；悬停高亮邻接、淡出其余
- 点击节点在右侧显示详情（类型/连接数/评分/链接）
"""

from __future__ import annotations

import json
from typing import Any

GRAPH_CSS = """
.gwrap { display:flex; gap:10px; font-family:-apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif; }
.gpanel { width:196px; flex:0 0 196px; background:#111823; border:1px solid #1f2b3a;
          border-radius:10px; padding:10px 12px; color:#c8d6e5; font-size:13px; }
.gpanel h4 { margin:10px 0 6px; font-size:12px; color:#7d93a8; font-weight:600; letter-spacing:.4px; }
.gpanel h4:first-child { margin-top:2px; }
.glegend { display:flex; align-items:center; gap:7px; padding:4px 6px; border-radius:6px;
           cursor:pointer; user-select:none; transition:background .15s; }
.glegend:hover { background:#18222f; }
.glegend.off { opacity:.32; }
.glegend .dot { width:10px; height:10px; border-radius:50%; flex:0 0 10px; }
.glegend .cnt { margin-left:auto; color:#6d8299; font-size:12px; font-variant-numeric:tabular-nums; }
.gsearch { width:100%; box-sizing:border-box; background:#0d141d; border:1px solid #24313f;
           border-radius:7px; color:#dbe7f3; padding:6px 8px; font-size:13px; }
.gsearch:focus { outline:none; border-color:#3d7fa6; }
.gtip { color:#63798d; font-size:11.5px; line-height:1.7; }
.gtip b { color:#8fa6bd; font-weight:600; }
.gstage { position:relative; flex:1 1 auto; min-width:0; }
#gcanvas { width:100%; border-radius:10px; border:1px solid #1f2b3a; background:#0b1017; }
.gcrumb { position:absolute; top:10px; left:12px; display:none; align-items:center; gap:8px;
          background:rgba(17,24,35,.94); border:1px solid #26374a; border-radius:7px;
          padding:5px 10px; font-size:12px; color:#9fb3c8; }
.gcrumb.show { display:flex; }
.gcrumb button { background:none; border:1px solid #2f4356; color:#7fc7e8; border-radius:5px;
                 padding:2px 8px; font-size:11.5px; cursor:pointer; }
.gcrumb button:hover { border-color:#4a9dc4; }
.gcard { position:absolute; top:12px; right:12px; width:262px; background:rgba(17,24,35,.97);
         border:1px solid #26374a; border-radius:10px; padding:13px 15px; color:#d7e3ef;
         font-size:13px; box-shadow:0 10px 28px rgba(0,0,0,.5); display:none; }
.gcard.show { display:block; }
.gcard .t { font-weight:600; color:#eaf2fa; margin-bottom:4px; line-height:1.45; }
.gcard .kind { font-size:11px; color:#7d93a8; margin-bottom:8px; }
.gcard .row { margin:4px 0; color:#9fb3c8; display:flex; gap:6px; }
.gcard .k { color:#6d8299; flex:0 0 56px; }
.gcard .acts { margin-top:10px; padding-top:9px; border-top:1px solid #223140; display:flex;
               flex-direction:column; gap:6px; }
.gcard .acts a, .gcard .acts button {
  display:block; text-align:center; text-decoration:none; font-size:12.5px;
  padding:6px 8px; border-radius:6px; border:1px solid #2f4356; background:#141f2b;
  color:#7fc7e8; cursor:pointer; }
.gcard .acts a:hover, .gcard .acts button:hover { border-color:#4a9dc4; background:#182636; }
.gstats { display:flex; gap:12px; flex-wrap:wrap; color:#8fa6bd; font-size:12px; padding:6px 2px; }
.gstats b { color:#cfe0f0; font-weight:600; font-variant-numeric:tabular-nums; }
"""

GRAPH_JS = """
(function () {
  var DATA = __DATA__;

  /** 自定义 3D 球体：径向渐变 + 镜面高光 + 接触阴影（替代五角星等平面图元）。 */
  function sphereRenderer(ctx, node, selected, hover) {
    var p = node.palette || {base:'#60a5fa', hi:'#cfe4ff', mid:'#60a5fa', lo:'#1c3a5e', depth:0.6};
    // 半径以 node.size 为准（vis 的自定义形状不写回 radius，故此处直接采用 size）
    var r = node.size || 12;
    var depth = p.depth || 0.6;
    return {
      drawNode: function () {
        var c = ctx;
        // 接触阴影：让球体"落"在画布上
        c.save();
        c.beginPath();
        c.ellipse(0, r * 0.72, r * 0.92, r * 0.34, 0, 0, Math.PI * 2);
        c.fillStyle = 'rgba(0,0,0,' + (0.28 + 0.22 * depth) + ')';
        c.filter = 'blur(3px)';
        c.fill();
        c.restore();

        // 球体本体：偏心径向渐变制造体积感
        var g = c.createRadialGradient(-r * 0.34, -r * 0.38, r * 0.08, 0, 0, r);
        g.addColorStop(0, p.hi);
        g.addColorStop(0.42, p.mid);
        g.addColorStop(1, p.lo);
        c.save();
        c.beginPath();
        c.arc(0, 0, r, 0, Math.PI * 2);
        c.fillStyle = g;
        c.shadowColor = p.base;
        c.shadowBlur = selected ? 26 : (hover ? 18 : 10 * depth + 4);
        c.fill();
        c.restore();

        // 边缘描边：增强轮廓，选中/悬停时提亮
        c.beginPath();
        c.arc(0, 0, r - 0.5, 0, Math.PI * 2);
        c.strokeStyle = selected ? '#ffffff' : 'rgba(255,255,255,' + (0.10 + 0.22 * depth) + ')';
        c.lineWidth = selected ? 2 : 1;
        c.stroke();

        // 镜面高光点
        c.save();
        c.beginPath();
        c.ellipse(-r * 0.36, -r * 0.42, r * 0.26, r * 0.18, -0.6, 0, Math.PI * 2);
        c.fillStyle = 'rgba(255,255,255,' + (0.42 + 0.3 * depth) + ')';
        c.filter = 'blur(1.2px)';
        c.fill();
        c.restore();

        // 底部环境反射光（模拟地面反光，进一步强化立体）
        c.save();
        c.beginPath();
        c.ellipse(r * 0.18, r * 0.5, r * 0.4, r * 0.16, 0.4, 0, Math.PI * 2);
        c.fillStyle = 'rgba(255,255,255,0.10)';
        c.filter = 'blur(2.5px)';
        c.fill();
        c.restore();
      },
      nodeDimensions: { width: r * 2 + 8, height: r * 2 + 10, borderWidth: 0 }
    };
  }
  // 注册到 vis 的形状系统（vis 通过节点的 ctxRenderer 调用自定义绘制）
  DATA.nodes.forEach(function (n) { n.ctxRenderer = sphereRenderer; });

  var nodes = new vis.DataSet(DATA.nodes);
  var edges = new vis.DataSet(DATA.edges);
  var container = document.getElementById('gcanvas');
  var network = new vis.Network(container, {nodes: nodes, edges: edges}, DATA.options);
  window.__net__ = network;   // 暴露句柄：便于自动化测试与外部控制
  var card = document.querySelector('.gcard');
  var crumb = document.querySelector('.gcrumb');
  var hiddenKinds = {};
  var focusStack = [];   // 面包屑：记录聚焦历史

  function baseNode(n) {
    // 保持标签策略：骨架节点常驻，情报节点无标签
    return {id: n.id, opacity: 1, font: {color: '#c3d3e3'},
            label: DATA.keep_label[n.kind] ? (n.fullLabel ? truncate(n.fullLabel, DATA.keep_label[n.kind]) : '') : ''};
  }
  function truncate(text, limit) {
    return text.length <= limit ? text : text.slice(0, limit - 1) + '…';
  }

  /** 高亮某节点的邻域，其余淡化（成熟图谱产品的核心交互）。 */
  function highlight(id) {
    var keep = {};
    keep[id] = true;
    network.getConnectedNodes(id).forEach(function (n) { keep[n] = true; });
    nodes.update(DATA.nodes.map(function (n) {
      var on = !!keep[n.id];
      return {id: n.id, opacity: on ? 1 : 0.12,
              font: {color: on ? '#e6f0f8' : '#3a4a5c'},
              label: on ? (n.fullLabel || '') : (DATA.keep_label[n.kind] ? n.label : ''),
              borderWidth: on ? 2 : 1};
    }));
    edges.update(DATA.edges.map(function (e) {
      var on = e.from === id || e.to === id;
      return {id: e.id, color: {opacity: on ? 0.95 : 0.06},
              width: on ? Math.max(2, e.width) : e.width};
    }));
  }

  function clearHighlight() {
    nodes.update(DATA.nodes.map(baseNode));
    edges.update(DATA.edges.map(function (e) {
      return {id: e.id, color: {opacity: e._opacity !== undefined ? e._opacity : 0.5}, width: e.width};
    }));
  }

  function showCard(id) {
    var n = nodes.get(id);
    var connected = network.getConnectedNodes(id);
    var html = '<div class="t">' + (n.label || id) + '</div>';
    html += '<div class="kind">' + (DATA.kind_labels[n.kind] || n.kind) + '</div>';
    html += '<div class="row"><span class="k">连接</span><span>' + connected.length + ' 个节点</span></div>';
    if (n.score !== undefined && n.score !== null) {
      html += '<div class="row"><span class="k">相关度</span><span>' + Math.round(n.score) + ' / 100</span></div>';
    }
    if (n.count) html += '<div class="row"><span class="k">出现</span><span>' + n.count + ' 条情报</span></div>';
    html += '<div class="acts">';
    if (n.url) html += '<a href="' + n.url + '" target="_blank" rel="noopener">打开原文 ↗</a>';
    // 可操作：在图谱中聚焦该节点邻域
    html += '<button data-act="focus">只看它的关联</button>';
    // 可操作：跳到情报库按该主题筛选
    if (n.kind === 'category') html += '<a href="?view=library&category=' + encodeURIComponent(n.label) + '">在情报库中筛选该方向</a>';
    if (n.kind === 'project') html += '<a href="?view=library&project=' + encodeURIComponent(n.label) + '">查看该项目的线索</a>';
    if (n.kind === 'keyword' || n.kind === 'repo') html += '<a href="?view=library&q=' + encodeURIComponent(n.label) + '">检索「' + n.label + '」</a>';
    html += '</div>';
    card.innerHTML = html;
    card.classList.add('show');
    var focusBtn = card.querySelector('[data-act="focus"]');
    if (focusBtn) focusBtn.addEventListener('click', function () { focusOn(id); });
  }

  /** 聚焦：只显示该节点及其邻域，并记录面包屑。 */
  function focusOn(id) {
    var keep = {}; keep[id] = true;
    network.getConnectedNodes(id).forEach(function (n) { keep[n] = true; });
    nodes.update(DATA.nodes.map(function (n) {
      return {id: n.id, hidden: !keep[n.id]};
    }));
    focusStack.push(id);
    var n = nodes.get(id);
    crumb.innerHTML = '聚焦：<b>' + (n.label || id) + '</b> <button>返回全图</button>';
    crumb.classList.add('show');
    crumb.querySelector('button').addEventListener('click', resetFocus);
    network.fit({nodes: Object.keys(keep), animation: {duration: 450}});
  }

  function resetFocus() {
    focusStack = [];
    nodes.update(DATA.nodes.map(function (n) {
      return {id: n.id, hidden: !!hiddenKinds[n.kind]};
    }));
    crumb.classList.remove('show');
    clearHighlight();
    network.fit({animation: {duration: 450}});
  }

  network.once('stabilizationIterationsDone', function () { network.fit({animation:false}); });

  // 图例过滤
  document.querySelectorAll('.glegend').forEach(function (el) {
    el.addEventListener('click', function () {
      var kind = el.getAttribute('data-kind');
      el.classList.toggle('off');
      hiddenKinds[kind] = el.classList.contains('off');
      nodes.update(DATA.nodes.filter(function (n) { return n.kind === kind; })
        .map(function (n) { return {id: n.id, hidden: hiddenKinds[kind]}; }));
    });
  });

  // 搜索：高亮并聚焦命中节点
  var search = document.querySelector('.gsearch');
  if (search) {
    search.addEventListener('input', function () {
      var q = search.value.trim().toLowerCase();
      if (!q) { clearHighlight(); card.classList.remove('show'); return; }
      var hits = DATA.nodes.filter(function (n) {
        return ((n.fullLabel || n.label || '')).toLowerCase().indexOf(q) >= 0;
      });
      nodes.update(DATA.nodes.map(function (n) {
        var hit = hits.some(function (h) { return h.id === n.id; });
        return {id: n.id, opacity: hit ? 1 : 0.08,
                label: hit ? (n.fullLabel || '') : (DATA.keep_label[n.kind] ? n.label : ''),
                font: {color: hit ? '#ffffff' : '#3a4a5c'}};
      }));
      if (hits.length) network.focus(hits[0].id, {scale: 1.2, animation: {duration: 550}});
    });
  }

  // 悬停：显示完整标签（情报节点默认无标签，避免糊成一片）
  network.on('hoverNode', function (params) {
    var n = nodes.get(params.node);
    if (n && n.fullLabel && n.label !== n.fullLabel) {
      nodes.update({id: n.id, label: n.fullLabel});
    }
  });
  network.on('blurNode', function (params) {
    var n = nodes.get(params.node);
    if (n && n.fullLabel && n.label === n.fullLabel && n.kind === 'item') {
      nodes.update({id: n.id, label: ''});
    }
  });

  // 单击：高亮邻域 + 详情卡；双击：聚焦
  network.on('click', function (params) {
    if (!params.nodes.length) { clearHighlight(); card.classList.remove('show'); return; }
    var id = params.nodes[0];
    highlight(id);
    showCard(id);
    if (params.event && params.event.srcEvent && params.event.srcEvent.detail === 2) focusOn(id);
  });
  network.on('doubleClick', function (params) { if (params.nodes.length) focusOn(params.nodes[0]); });
})();
"""

KIND_LABELS = {
    "item": "情报", "repo": "仓库", "category": "技术方向",
    "keyword": "关键词", "project": "内部项目", "source": "来源",
}

# 配色：低饱和、深色底可读（参考常见深色主题图谱）
KIND_COLORS = {
    "project":  "#a78bfa",
    "category": "#34d399",
    "repo":     "#fbbf24",
    "source":   "#38bdf8",
    "keyword":  "#fb7185",
    "item":     "#60a5fa",
}
# 标签策略：只给"骨架"节点显示常驻标签，情报节点仅在悬停/选中时显示，
# 避免上百个长标题挤成一条线。
LABEL_ALWAYS = {"project", "category", "repo", "source"}
LABEL_MAX_CHARS = {"project": 18, "category": 14, "repo": 22, "source": 8, "keyword": 14, "item": 0}
KIND_DEPTH = {           # 立体感强度：核心实体更亮、更"凸"
    "project": 1.0, "category": 0.92, "repo": 0.85,
    "source": 0.7, "keyword": 0.62, "item": 0.5,
}
KIND_BASE_SIZE = {"project": 34, "category": 28, "repo": 24, "source": 20, "keyword": 15, "item": 10}
EDGE_COLORS = {
    "belongs_to": "#2f7f68", "relates_to": "#6d5bb8", "implements": "#9a7b2c",
    "mentions": "#7a4a52", "co_occurs": "#7a4a52", "from": "#2f5f73",
}


def _degree(graph: dict[str, Any]) -> dict[str, int]:
    degree: dict[str, int] = {}
    for edge in graph["edges"]:
        degree[edge["from"]] = degree.get(edge["from"], 0) + 1
        degree[edge["to"]] = degree.get(edge["to"], 0) + 1
    return degree


def _lighten(hex_color: str, factor: float) -> str:
    """把颜色向白色插值，用于生成球体高光。"""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def _darken(hex_color: str, factor: float) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return f"#{int(r * (1 - factor)):02x}{int(g * (1 - factor)):02x}{int(b * (1 - factor)):02x}"


def build_vis_nodes(graph: dict[str, Any]) -> list[dict[str, Any]]:
    degree = _degree(graph)
    out: list[dict[str, Any]] = []
    for node in graph["nodes"]:
        kind = node["kind"]
        links = degree.get(node["id"], 0)
        size = KIND_BASE_SIZE.get(kind, 10) + min(16, int(links ** 0.6))
        color = KIND_COLORS.get(kind, "#60a5fa")
        # 标签策略：骨架节点常驻（截断），情报节点默认隐藏、悬停/选中时显示
        limit = LABEL_MAX_CHARS.get(kind, 0)
        raw_label = node["label"] or ""
        if kind in LABEL_ALWAYS and limit:
            label = raw_label if len(raw_label) <= limit else raw_label[: limit - 1] + "…"
        else:
            label = ""
        out.append({
            "id": node["id"],
            "label": label,
            "fullLabel": raw_label,          # 悬停/选中时恢复完整标题
            "kind": kind,
            "score": node.get("score"),
            "count": node.get("count"),
            "url": node.get("url", ""),
            "shape": "sphere3d",                 # 自定义绘制（见 graphview.js 的 ctxRenderer）
            "size": size,
            "radius": size,                      # 显式半径：自定义形状需它参与计算
            # 供 3D 着色使用：主色 / 高光 / 暗部
            "palette": {
                "base": color,
                "hi": _lighten(color, 0.78),
                "mid": color,
                "lo": _darken(color, 0.55),
                "depth": KIND_DEPTH.get(kind, 0.6),
            },
            "color": {"background": color, "border": color,
                      "highlight": {"background": _lighten(color, 0.5), "border": "#ffffff"}},
            "font": {"size": 14 if kind in ("project", "category") else 12,
                     "color": "#c3d3e3", "strokeWidth": 3, "strokeColor": "#0b1017",
                     "face": "-apple-system,'PingFang SC','Microsoft YaHei',sans-serif"},
        })
    return out


def build_vis_edges(graph: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, edge in enumerate(graph["edges"]):
        relation = edge["relation"]
        weight = float(edge.get("weight", 1))
        dashed = relation in ("mentions", "co_occurs", "from")
        opacity = 0.42 if dashed else 0.62
        out.append({
            "id": f"e{index}",          # 稳定 id：供高亮/还原更新
            "from": edge["from"], "to": edge["to"],
            "color": {"color": EDGE_COLORS.get(relation, "#3a4a5c"), "opacity": opacity},
            "_opacity": opacity,         # 记住原始透明度
            "width": min(3.5, 0.6 + weight ** 0.45 * 0.45),
            "dashes": dashed,
            "smooth": {"type": "continuous", "roundness": 0.18},
            "title": relation,
        })
    return out


def build_options(node_count: int) -> dict[str, Any]:
    """物理参数按规模自适应：节点越大、斥力越强，避免球体互相重叠。"""
    scale = 1.0 if node_count < 80 else (1.35 if node_count < 200 else 1.8)
    return {
        "nodes": {"borderWidth": 1.2, "shadow": {"enabled": False}},
        "edges": {"selectionWidth": 2, "hoverWidth": 1.5},
        "interaction": {
            "hover": True, "tooltipDelay": 120, "navigationButtons": False,
            "zoomView": True, "dragView": True,
            "hideEdgesOnDrag": node_count > 150,
            # 悬停时显示完整标签（情报节点默认不显示标签）
            "hoverConnectedEdges": True,
        },
        "physics": {
            "solver": "forceAtlas2Based",
            "forceAtlas2Based": {
                "gravitationalConstant": -160 * scale,
                "centralGravity": 0.008,
                "springLength": 190 * scale,
                "springConstant": 0.06,
                "damping": 0.6,
                "avoidOverlap": 0.9,      # 关键：让球体彼此不重叠
            },
            "stabilization": {"enabled": True, "iterations": 420, "fit": True},
        },
    }


def _vis_script() -> str:
    """内联 vis-network 库（优先使用 pyvis 自带的本地副本，保证离线可用）。"""
    from pathlib import Path

    candidates = [
        Path(__file__).with_name("vis-network.min.js"),
    ]
    try:
        import pyvis

        candidates.append(Path(pyvis.__file__).parent / "templates" / "lib" / "vis-9.1.2" / "vis-network.min.js")
    except Exception:
        pass
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return ""


def render_graph_html(graph: dict[str, Any], height: int = 680) -> str:
    """生成自包含的图谱 HTML（含左筛选面板 / 中画布 / 右详情卡）。

    优先内联本地 vis-network（离线可用）；缺失时回退 CDN。
    """
    stats = graph["stats"]
    counts: dict[str, int] = stats.get("by_kind", {})
    legend_rows = []
    for kind, label in KIND_LABELS.items():
        if not counts.get(kind):
            continue
        legend_rows.append(
            f'<div class="glegend" data-kind="{kind}">'
            f'<span class="dot" style="background:{KIND_COLORS[kind]}"></span>'
            f'<span>{label}</span><span class="cnt">{counts[kind]}</span></div>'
        )
    hint = (
        '<h4>怎么用</h4>'
        '<div class="gtip">单击节点：<b>高亮它的关联</b>并显示详情<br>'
        '双击节点：<b>只看它这一片</b><br>'
        '点空白：取消选择</div>'
    )
    payload = {
        "nodes": build_vis_nodes(graph),
        "edges": build_vis_edges(graph),
        "options": build_options(len(graph["nodes"])),
        "kind_labels": KIND_LABELS,
        "keep_label": LABEL_MAX_CHARS,   # 各类型标签字符上限（0 = 默认不显示）
    }
    data_json = json.dumps(payload, ensure_ascii=False)
    local_lib = _vis_script()
    if local_lib:
        library_tag = f'<script type="text/javascript">{local_lib}</script>'
    else:
        library_tag = ('<script type="text/javascript" '
                       'src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>')
    return f"""
<div class="gwrap">
  <div class="gpanel">
    <h4>节点类型</h4>
    {''.join(legend_rows)}
    <h4>搜索</h4>
    <input class="gsearch" placeholder="输入名称定位…" />
    {hint}
    <div class="gstats" style="margin-top:8px">
      <span><b>{stats.get('nodes', 0)}</b> 节点</span>
      <span><b>{stats.get('edges', 0)}</b> 关系</span>
    </div>
  </div>
  <div class="gstage">
    <div id="gcanvas" style="height:{height}px"></div>
    <div class="gcrumb"></div>
    <div class="gcard"></div>
  </div>
</div>
<style>{GRAPH_CSS}</style>
{library_tag}
<script type="text/javascript">{GRAPH_JS.replace('__DATA__', data_json)}</script>
"""
