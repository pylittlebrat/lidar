"""Chinese intelligence workspace. Run with techradar dashboard."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from html import escape
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from techradar.config import Config, default_data_dir, project_root
from techradar.dashboard import data as ddata
from techradar.storage.database import Database
from techradar.sync import get_status, get_history, start_sync, SyncBusy

load_dotenv(project_root() / ".env")
st.set_page_config(page_title="仿真技术雷达", page_icon="◉", layout="wide")
st.markdown(f"<style>{Path(__file__).with_name('style.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

SOURCES = {"github": "GitHub · 开源项目", "arxiv": "arXiv · 研究论文", "rss": "RSS · 行业动态", "engineering_machinery": "工程机械 · 专用来源", "web": "网页", "document": "文档资料"}
ACTIONS = {"INVESTIGATE": "建议调研", "POC": "建议验证", "WATCH": "持续关注", "IGNORE": "暂不跟进", "ADOPT": "考虑采用", "ARCHIVE": "建议归档", "READ": "建议阅读"}
CATEGORIES = {"simulation_engine": "仿真引擎", "robot_learning": "机器人学习", "real2sim": "Real2Sim", "sim2real": "Sim2Real", "embodied_ai": "具身智能", "system_identification": "系统辨识", "asset_generation": "资产生成", "world_model": "世界模型", "humanoid": "人形机器人", "manipulation": "操作与抓取", "engineering_machinery": "工程机械", "simulation_validation": "仿真验证"}
PROJECTS = {"funlab": "FunLab", "real2sim": "Real2Sim", "simulation_platform": "仿真平台", "engineering_machinery": "工程机械", "robot_learning": "机器人学习"}
MATURITY = {"idea": "构想阶段", "experimental": "实验阶段", "release": "已发布", "stable": "稳定版本", "research": "研究阶段", "prototype": "原型阶段", "production": "生产应用", "mature": "成熟", "unknown": "待确认"}


@st.cache_resource
def load_database(path):
    db = Database(path)
    db.init()
    return db


cfg, db = Config.load(), load_database(str(default_data_dir() / "radar.db"))
focus = int(cfg.scoring.get("bands", {}).get("focus", 75))
stats = ddata.library_stats(db, focus)


def go(page, status="全部", source="全部来源"):
    st.session_state["page"] = page
    st.session_state["status_filter"] = status
    st.session_state["source_filter"] = source


def render_knowledge_graph():
    """统一的 3D 星系入口：项目是筛选视角，不再切换另一种图。"""
    from techradar.dashboard.galaxyview import build_galaxy, render_galaxy_html

    scope_column, filter_column = st.columns([4.5, 1.2], vertical_alignment="center")
    with filter_column, st.popover("筛选星图范围", use_container_width=True):
        project = st.selectbox("项目视角", ["all", *PROJECTS],
                               format_func=lambda p: "全部项目" if p == "all" else PROJECTS[p], key="atlas_project")
        window = st.selectbox("时间窗口", [90, 180, 365, 0],
                              format_func=lambda days: "全部时间" if not days else f"近 {days} 天", index=3, key="atlas_window")
        min_score = st.slider("纳入情报的最低评分", 0, 90, 0, step=5, key="atlas_min_score")
    graph = ddata.build_graph(cfg, db, days=window or None, min_score=min_score,
                              limit=None, include_keywords=False, include_unanalyzed=True)
    if project != "all":
        keep = {e["from"] for e in graph["edges"]
                if e["relation"] == "relates_to" and e["to"] == f"project:{project}"}
        graph = {**graph, "nodes": [n for n in graph["nodes"] if n["kind"] != "item" or n["id"] in keep],
                 "edges": [e for e in graph["edges"] if not e["from"].startswith("item:") or e["from"] in keep]}
    galaxy = build_galaxy(graph)
    galaxy["stats"]["library_items"] = stats["total"]
    window_label = "全部时间" if not window else f"近 {window} 天"
    project_label = "全部项目" if project == "all" else PROJECTS[project]
    score_label = "全部评分" if not min_score else f"相关度 ≥ {min_score}"
    scope_column.caption(f"3D 星系 · {project_label} · {window_label} · {score_label} · 当前展示 {galaxy['stats']['items']} / 全库 {stats['total']} 条")
    st.components.v1.html(render_galaxy_html(galaxy, height=690), height=700)
    if galaxy["stats"]["unassigned"]:
        st.caption(f"其中 {galaxy['stats']['unassigned']} 条尚无有效方向标签，已纳入「待分类」星系。每个亮星点代表一条情报，点击星系可展开阅读。")


def stamp(value):
    if not value:
        return "暂无采集记录"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "时间待确认"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")


def safe_url(url):
    return url if urlparse(url or "").scheme in ("https", "http") else None


def issue_text(issue):
    if not isinstance(issue, dict):
        return str(issue)
    stage = {"collect": "采集", "analyze": "分析", "daily": "日报", "weekly": "周报", "radar": "技术雷达"}.get(issue.get("stage"), "同步")
    source = SOURCES.get(issue.get("source"), issue.get("source"))
    return f"{stage}{' · ' + source if source else ''}：{issue.get('reason') or '本步骤未完成'}"


@st.fragment(run_every=2)
def sync_controls():
    state = get_status(db)
    running = state.get("status") == "running"
    if st.button("正在同步…" if running else "刷新 · 同步最新情报", type="primary", disabled=running, use_container_width=True):
        try:
            # Reload YAML so newly configured information sources are used immediately.
            start_sync(Config.load(), db)
        except SyncBusy:
            pass
        except Exception:
            st.error("暂时无法启动同步，请稍后重试。")
            return
        st.rerun()
    st.caption("手动同步不改变每天 08:30 的自动更新")
    if running:
        st.progress(state.get("progress", 0), text=state.get("stage", "准备同步"))
        st.caption("后台处理中，可以继续浏览页面。")
    elif state.get("status") in ("success", "partial", "failed", "interrupted"):
        status = state["status"]
        inserted = sum(s.get("inserted", 0) for s in state.get("collect", {}).values())
        updated = sum(s.get("updated", 0) for s in state.get("collect", {}).values())
        label = {"success": "同步完成", "partial": "同步完成，部分步骤未完成", "failed": "同步失败", "interrupted": "上次同步被中断"}[status]
        (st.success if status == "success" else st.warning)(label)
        st.caption(f"最近同步：{stamp(state.get('finished_at') or state.get('started_at'))}")
        st.caption(f"新增 {inserted} 条 · 内容更新 {updated} 条 · 写入分析 {state.get('analyze', {}).get('stored', 0)} 条")
        if 'analyzed' in state.get('analyze', {}):
            st.caption(f"模型成功 {state['analyze'].get('analyzed',0)} · 规则评分 {state['analyze'].get('skipped_llm',0)} · 失败 {state['analyze'].get('llm_errors',0)}")
        if state.get("warnings") or state.get("failures"):
            with st.expander("查看同步提示"):
                for warning in state.get("warnings", []):
                    st.write(issue_text(warning))
                for failure in state.get("failures", []):
                    st.write(issue_text(failure))
        previous = st.session_state.get("sync_last_result")
        st.session_state["sync_last_result"] = state.get("id")
        if previous != state.get("id"):
            st.rerun()
    history = get_history(db, limit=5)
    if history:
        with st.expander("最近同步记录"):
            for run in history:
                status_text = {"success": "完成", "partial": "部分完成", "failed": "失败", "interrupted": "中断", "running": "处理中"}.get(run.get("status"), run.get("status", "待确认"))
                st.caption(f"{stamp(run.get('started_at'))} · {status_text}")
                for issue in [*run.get("warnings", []), *run.get("failures", [])]:
                    st.write(issue_text(issue))


def card(entry, compact=False):
    """决策卡：只呈现做判断需要的信息，细节收进展开。

    自左至右的信息优先级：信号强度色条 → 标题 → 评分 → 关联项目 → 建议动作 → 一句话结论。
    """
    score = entry.get("score")
    state = ddata.analysis_status(entry)
    strong = score is not None and score >= focus
    accent = "#37e0c8" if strong else ("#ffb454" if score is not None and score >= 60 else "#2a3946")
    pending = state == "pending"
    action = ACTIONS.get(entry.get("recommended_action"), entry.get("recommended_action") or "待评估")
    cats = list(dict.fromkeys(CATEGORIES.get(c.lower().replace(" ", "_"), c) for c in entry.get("categories", [])))
    related = [PROJECTS.get(k, k) for k, v in entry.get("relevance", {}).items() if v >= 3]
    st.markdown(f'<div class="signal-accent" style="background:{accent}"></div>', unsafe_allow_html=True)
    with st.container(border=True):
        head, rating = st.columns([7.5, 1.4])
        with head:
            source = SOURCES.get(entry["source_type"], entry["source_type"])
            st.markdown(
                f'<div class="eyebrow">{escape(source)} <span>/ {escape(entry["source_name"])}</span></div>',
                unsafe_allow_html=True,
            )
            title = entry.get("repo") if (entry.get("grouped") and entry.get("version_count", 1) > 1) else entry["title"]
            st.markdown(f'<div class="entry-title">{escape(title)}</div>', unsafe_allow_html=True)
        with rating:
            number = f"{score:.0f}" if score is not None else "—"
            st.markdown(
                f'<div class="rating {"strong" if strong else "normal"}">{number}'
                f'<small>{"相关度 / 100" if score is not None else "待评分"}</small></div>',
                unsafe_allow_html=True,
            )
        # 决策行：关联项目 → 建议动作（一眼看出"跟我有关吗、要我做什么"）
        decision = []
        if related:
            decision.append(f'<span class="d-project">{escape(" · ".join(related[:3]))}</span>')
        if not pending:
            decision.append(f'<span class="d-action">{escape(action)}</span>')
        decision.append(f'<span class="d-state">{escape(ddata.ANALYSIS_LABELS.get(state, "状态待确认"))}</span>')
        st.markdown('<div class="decision-row">' + "".join(decision) + '</div>', unsafe_allow_html=True)
        # 一句话结论
        summary = entry.get("summary") or entry.get("abstract") or "尚无摘要，可打开原文了解详情。"
        length = 90 if compact else 150
        st.markdown(f'<div class="verdict">{escape(summary[:length])}{"…" if len(summary) > length else ""}</div>', unsafe_allow_html=True)
        if cats:
            st.markdown('<div class="tags">' + ''.join(f'<span>{escape(t)}</span>' for t in cats[:4]) + '</div>', unsafe_allow_html=True)
        if state == "llm_failed":
            st.caption("模型分析未完成，评分由关键词与来源规则给出，将按重试计划处理。")
        elif state == "skipped":
            st.caption("已按关键词和来源评分，尚无模型技术判断。")
        elif state == "unknown":
            st.caption("历史记录未标记分析方式，判断依据待确认。")
        elif state == "llm" and not entry.get("prompt_version"):
            st.caption("历史模型分析：沿用当时的判断和评分，尚未按当前规则重新分析。")
        with st.expander("技术细节与原文"):
            if entry.get("version_count", 1) > 1:
                st.caption(f"当前范围 {entry['version_count']} 个版本，摘要与评分来自代表版本；最新：{entry.get('_latest_title', '')} · {stamp(entry.get('_latest_collected'))}")
            if pending:
                st.info("这条线索尚未生成分析，以下为采集内容。")
            elif state == "llm":
                st.caption(f"分析时间：{stamp(entry.get('analysis_updated_at'))} · 模型：{entry.get('model') or '未记录'}")
            elif state == "llm_failed":
                st.caption(f"尝试 {entry.get('attempts', 0)} 次 · 下次重试：{stamp(entry.get('next_retry_at')) if entry.get('next_retry_at') else '等待任务安排'}")
                if entry.get("last_error"):
                    st.write(entry["last_error"])
            if not pending:
                st.write(entry.get("summary") or "暂无分析摘要。")
                for key, label in (("problem", "解决什么问题"), ("solution", "技术方案"), ("platform_relevance", "对平台的价值"), ("comparison", "与已有方案对比")):
                    if entry.get(key):
                        st.markdown(f"**{label}**")
                        st.write(entry[key])
                if entry.get("innovation"):
                    st.markdown("**值得关注的变化**")
                    for point in entry["innovation"]:
                        st.write(f"• {point}")
                if entry.get("relevance"):
                    st.markdown("**内部项目相关度**")
                    st.write(" · ".join(f"{PROJECTS.get(k, k)} {v:g}/5" for k, v in entry["relevance"].items()))
                st.caption(f"成熟度：{MATURITY.get(entry.get('maturity'), entry.get('maturity') or '待确认')} · 建议：{action}")
                if entry.get("breakdown"):
                    st.markdown("**评分依据**")
                    labels = {"keyword_score": "关键词匹配", "source_score": "来源权威性", "llm_relevance": "模型相关性", "novelty_score": "内容新颖度"}
                    st.write(" · ".join(f"{labels.get(key, key)} {value:g}/100" for key, value in entry["breakdown"].items() if isinstance(value, (int, float))))
            if entry.get("abstract"):
                st.markdown("**采集摘要**")
                st.write(entry["abstract"])
            if entry.get("version_count", 1) > 1 and entry.get("versions"):
                st.markdown(f"**版本序列（{entry['version_count']} 个版本，最近 {len(entry['versions'])} 条）**")
                for version in entry["versions"][:5]:
                    stamp_text = stamp(version.get("collected_at"))
                    if safe_url(version.get("url")):
                        st.markdown(f"· [{escape(version['title'])}]({version['url']})（{stamp_text}）")
                    else:
                        st.markdown(f"· {escape(version['title'])}（{stamp_text}）")
        footer, link = st.columns([4, 1])
        published = entry.get("published_at")
        footer.caption(f"{'发布 ' + stamp(published) + ' · ' if published and not compact else ''}入库 {stamp(entry.get('collected_at'))} · 北京时间")
        if entry.get("content_updated_at") and entry["content_updated_at"] != entry.get("collected_at"):
            footer.caption(f"内容更新：{stamp(entry['content_updated_at'])}")
        if safe_url(entry.get("url")):
            link.link_button("阅读原文 ↗", entry["url"], use_container_width=True)


with st.sidebar:
    st.markdown('<div class="brand"><b>◉</b> 仿真技术雷达<small>SIMULATION TECH RADAR</small></div>', unsafe_allow_html=True)
    st.caption("从技术线索，到研究判断")
    # 支持从图谱/其它页面带参跳转：?view=library&category=xxx&q=xxx
    _params = st.query_params
    if _params.get("view") == "knowledge" and "page" not in st.session_state:
        st.session_state["page"] = "知识图谱"
    if _params.get("view") == "library" and st.session_state.get("page") != "情报库":
        st.session_state["page"] = "情报库"
        if _params.get("category"):
            st.session_state["_preset_category"] = _params["category"]
        if _params.get("q"):
            st.session_state["_preset_query"] = _params["q"]
        if _params.get("project"):
            st.session_state["_preset_project"] = _params["project"]
    page = st.radio("工作空间", ["情报概览", "技术雷达", "知识图谱", "情报库", "技术趋势", "研究报告", "技术问答"], key="page", label_visibility="collapsed")
    st.divider()
    st.caption("知识库")
    st.markdown(f"**{stats['total']:,}** 条技术线索 · **{stats['llm']:,}** 条模型分析")
    st.caption(f"最近采集：{stamp(stats['latest'])}（北京）")
    sync_controls()

header, date = st.columns([4, 1])
header.markdown('<div class="eyebrow">SIMULATION TECH RADAR · 机器人仿真技术情报</div>', unsafe_allow_html=True)
date.caption(datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M 北京时间"))

if page == "情报概览":
    st.title("信号总览")
    st.caption("采集中 → 分析 → 评分 → 报告，全链路状态与当前重点线索。")
    latest_run = get_status(db)
    run_label = {"success": "同步完成", "partial": "部分来源需要跟进", "failed": "同步失败", "interrupted": "上次同步中断", "running": "正在同步", "idle": "等待首次同步"}.get(latest_run.get("status"), "等待同步")
    run_time = latest_run.get("finished_at") or latest_run.get("started_at")
    banner, refresh = st.columns([5, 1.4])
    with banner:
        attention = "attention" if latest_run.get("status") in ("partial", "failed", "interrupted") else ""
        st.markdown(f'<div class="run-summary {attention}"><span class="run-dot"></span><b>{run_label}</b><span>{escape(stamp(run_time)) if run_time else "采集、分析、生成报告"}</span><span class="run-schedule">每天 08:30</span></div>', unsafe_allow_html=True)
    with refresh:
        running = latest_run.get("status") == "running"
        if st.button("同步中…" if running else "同步最新情报", disabled=running, use_container_width=True):
            try:
                start_sync(Config.load(), db)
            except SyncBusy:
                st.info("已有同步正在执行，完成后页面会更新。")
            except Exception:
                st.error("暂时无法启动同步，请稍后重试。")
            else:
                st.rerun()
    # 仪表读数条：一次给出全部关键计数，不再重复指标卡
    st.markdown('<div class="analysis-strip">' + ''.join(
        f'<span><b>{value:,}</b> {label}</span>'
        for label, value in (
            ("24h 新线索", stats["recent"]),
            (f"重点关注 ≥{focus}", stats["high"]),
            ("模型分析", stats["llm"]),
            ("失败待重试", stats["llm_failed"]),
            ("待分析", stats["pending"]),
            ("知识库总量", stats["total"]),
        )
    ) + '</div>', unsafe_allow_html=True)
    left, right = st.columns([2.3, 1], gap="large")
    with left:
        # 今日重点：只看需要决策的（高相关 + 新出现），不是库存罗列
        st.subheader("今日重点")
        week = ddata.browse(db, status="analyzed", days=7, order="score", page_size=6, focus=focus)
        if week["entries"]:
            st.caption(f"近 7 天采集、按相关度排序；阈值 {focus} 分以上为重点关注")
        else:
            st.info("近 7 天暂无已完成分析的线索。可先浏览全部情报，或等待下一次同步。")
        for entry in week["entries"]:
            card(entry, compact=True)

        fresh = ddata.browse(db, status="", days=2, order="recent", page_size=5, focus=focus)
        new_ones = [e for e in fresh["entries"] if e.get("analysis_id") is None]
        if new_ones:
            st.subheader("新进入雷达")
            st.caption("最近两天采集、尚未形成技术判断的线索")
            for entry in new_ones[:4]:
                card(entry, compact=True)
        st.button("浏览全部情报 →", on_click=go, args=("情报库",), use_container_width=True)
    with right:
        with st.container(border=True):
            st.markdown("#### 研究入口")
            st.caption("按当前任务快速进入")
            st.button("重点关注 →", on_click=go, args=("情报库", "重点关注"), use_container_width=True)
            st.button("研究论文 →", on_click=go, args=("情报库", "全部", "arxiv"), use_container_width=True)
            st.button("开源项目 →", on_click=go, args=("情报库", "全部", "github"), use_container_width=True)
            st.button("阅读最新报告 →", on_click=go, args=("研究报告",), use_container_width=True)
        with st.container(border=True):
            st.markdown("#### 分析质量与待办")
            st.progress(stats["llm"] / max(stats["total"], 1))
            st.caption(f"{stats['llm']} / {stats['total']} 条完成模型分析；规则评分 {stats['skipped']} 条")
            st.button(f"查看待分析线索（{stats['pending']}）", on_click=go, args=("情报库", "待分析"), use_container_width=True)
            if stats["llm_failed"]:
                st.button(f"查看失败待重试（{stats['llm_failed']}）", on_click=go, args=("情报库", "失败待重试"), use_container_width=True)
            if stats["unknown"]:
                st.caption(f"另有 {stats['unknown']} 条历史记录待确认分析方式。")
        with st.container(border=True):
            st.markdown("#### 采集来源健康")
            health = ddata.source_health(cfg, latest_run)
            for row in health:
                source = row["source"]
                badge = "healthy" if row["status"] == "success" else "attention" if row["status"] in ("partial", "failed", "unavailable") else "muted"
                st.markdown(f'<div class="source-health"><span>{escape(SOURCES.get(source, source))}</span><b class="{badge}">{row["label"]}</b></div>', unsafe_allow_html=True)
                counts = f"累计 {stats['sources'].get(source, 0)} 条"
                if row["attempted"] is not None:
                    counts += f" · 上次请求 {row['succeeded'] or 0}/{row['attempted']} 成功"
                st.caption(counts)
                if row["unsupported_events"]:
                    st.caption("尚未覆盖事件：" + "、".join(row["unsupported_events"]))
            if any(row["status"] == "unavailable" for row in health):
                st.caption("未接入的专用来源暂未采集；论文和 RSS 中的工程机械内容仍可分类。")

elif page == "技术雷达":
    st.title("技术雷达")
    st.caption("跟踪技术从关注到验证、采用的过程。自动建议会更新，人工采用或归档决定会保留。")
    if st.button("重新扫描评分并刷新状态", type="primary"):
        from techradar.radar import RadarService
        RadarService(cfg, db).sync(days=90)
        st.rerun()
    rows = ddata.build_radar(db)
    if rows:
        frame = pd.DataFrame([{
            "技术": r["tech_name"],
            "状态": r["status"],
            "自动建议": r["suggested_status"] or "WATCH",
            "决策方式": "人工" if r["decision_status"] or r["status"] in ("ADOPT", "ARCHIVE") else "自动",
            "理由": (r["decision_reason"] or r["reason"] or "")[:80],
            "建议动作": r["action"] or "-",
            "首次发现": stamp(r["first_seen"]),
            "最近更新": stamp(r["updated_at"]),
        } for r in rows])
        counts = frame["状态"].value_counts()
        st.caption("状态分布：" + " · ".join(f"**{k}** {v}" for k, v in counts.items()))
        st.dataframe(frame, hide_index=True, use_container_width=True)
    else:
        st.info("雷达表为空。点击上方按钮按当前评分生成状态，或运行 `techradar radar`。")

elif page == "知识图谱":
    st.title("知识图谱")
    st.caption("在技术星系之间，发现下一条值得追踪的线索。")
    render_knowledge_graph()

elif page == "情报库":
    st.title("情报库")
    st.caption("论文、开源项目与行业动态，在同一个入口查找。")
    preset_query = st.session_state.pop("_preset_query", "")
    preset_category = st.session_state.pop("_preset_category", "")
    if preset_query or preset_category:
        st.info(f"已按图谱跳转带入筛选：{'关键词 ' + preset_query if preset_query else ''}"
                f"{' · ' if preset_query and preset_category else ''}"
                f"{'方向 ' + str(preset_category) if preset_category else ''}")
    query = st.text_input("搜索技术线索", value=preset_query,
                          placeholder="搜索标题、摘要或正文，例如 real2sim、physics engine…")
    a, b, c, e = st.columns(4)
    source = a.selectbox("信息来源", ["全部来源", *dict.fromkeys([*SOURCES, *stats["sources"]])], format_func=lambda x: SOURCES.get(x, x), key="source_filter")
    status = b.selectbox("处理状态", ["全部", "重点关注", "已有分析", "待分析", "模型分析", "规则评分", "失败待重试", "历史未标记"], key="status_filter")
    all_cats = stats["categories"]
    cat_options = ["全部方向", *all_cats]
    if preset_category:
        # 图谱传的是中文显示名，映射回分类 ID
        reverse = {v: k for k, v in CATEGORIES.items()}
        preset_id = reverse.get(preset_category, preset_category)
        if preset_id in all_cats and preset_id not in cat_options:
            cat_options.append(preset_id)
        cat_index = cat_options.index(preset_id) if preset_id in cat_options else 0
    else:
        cat_index = 0
    category = c.selectbox("技术方向", cat_options, index=cat_index, format_func=lambda x: CATEGORIES.get(x, x))
    days = e.selectbox("采集时间", [0, 1, 7, 30, 90], format_func=lambda x: "全部时间" if not x else f"近 {x} 天")
    order = st.radio("排序", ["最近采集", "综合评分优先"], horizontal=True)
    include_low = st.checkbox("包含低评分（<40 分）", value=False)
    filters = dict(query=query, source="" if source == "全部来源" else source,
        status={"重点关注": "focus", "已有分析": "analyzed", "待分析": "pending", "模型分析": "llm", "规则评分": "skipped", "失败待重试": "llm_failed", "历史未标记": "unknown"}.get(status, ""),
        category="" if category == "全部方向" else category, days=days, focus=focus,
        order="score" if order == "综合评分优先" else "recent",
        min_score=0 if include_low else 40)
    signature = tuple(filters.values())
    if st.session_state.get("filters") != signature:
        st.session_state["library_page"] = 1
        st.session_state["filters"] = signature
    result = ddata.browse(db, **filters, page=st.session_state.get("library_page", 1))
    st.session_state["library_page"] = result["page"]
    st.caption(f"找到 {result['total']} 项技术线索 · 第 {result['page']} / {result['pages']} 页 · 同仓库版本已合并" +
               ("（已隐藏评分 <40 的已分析条目）" if not include_low else ""))
    if not result["entries"]:
        st.info("没有符合条件的线索。试试缩短关键词，或放宽来源、方向与时间范围。")
    for entry in result["entries"]:
        card(entry)
    if result["pages"] > 1:
        st.number_input("跳转页码", min_value=1, max_value=result["pages"], step=1, key="library_page")

elif page == "技术趋势":
    st.title("技术趋势")
    st.caption("观察技术方向的出现频次，辅助确定下一步研究重点。")
    st.info("这里展示的是已分析资料中的出现频次。采集历史较短时，频次不代表行业增长速度。")
    group = st.radio("观察维度", ["分类", "关键词", "来源"], horizontal=True)
    rows = [r for r in ddata.build_trend_table(cfg, db, limit=20) if r["group"] == group]
    if rows:
        directions = {"insufficient": "数据不足", "up": "上升", "down": "下降", "flat": "平稳", "new": "新出现"}
        frame = pd.DataFrame([{"方向": CATEGORIES.get(r["name"], SOURCES.get(r["name"], r["name"])), "近7天": r["7d"], "之前23天": r["previous_count"], "近30天": r["30d"], "近90天": r["90d"], "判断": directions.get(r["direction"], "待确认"), "日均变化": f"{r['change_pct']:+.1f}%" if r["change_pct"] is not None else "—"} for r in rows])
        st.subheader("近 7 天活跃方向")
        st.bar_chart(frame.head(12).set_index("方向")[["近7天"]], horizontal=True, color="#37e0c8", height=350)
        st.subheader("各时间窗口对比")
        st.dataframe(frame, hide_index=True, use_container_width=True)
        st.caption("单位：条目出现次数。时间窗口相互包含，按采集时间统计。")
        if any(r["direction"] == "insufficient" for r in rows):
            st.caption("数据不足的方向暂不判断升降；日均变化比较近 7 天与之前 23 天，样本历史跨度和数量足够后才显示。")
    else:
        st.info("暂无趋势数据，积累分析结果后会显示技术方向分布。")

elif page == "研究报告":
    st.title("研究报告")
    st.caption("回顾每日线索与每周技术观察，选择报告即可阅读或下载。")
    kind = st.radio("报告类型", ["daily", "weekly"], format_func=lambda x: "每日情报" if x == "daily" else "每周观察", horizontal=True)
    files = sorted((project_root() / "reports" / kind).glob("*.md"), reverse=True)
    if files:
        selected = st.selectbox("选择报告", files, format_func=lambda p: p.stem)
        content = selected.read_text(encoding="utf-8")
        st.download_button("下载 Markdown 报告", content, file_name=selected.name, mime="text/markdown")
        with st.container(border=True):
            st.markdown(content)
    else:
        st.info("暂无此类报告。报告生成后会出现在这里。")

elif page == "技术问答":
    st.title("带着问题，探索知识库。")
    st.caption("回答基于已采集资料，并附上可核查的来源。")
    st.markdown("**你可以这样问**")
    examples = ["最近有哪些值得调研的 Real2Sim 技术？", "哪些仿真引擎适合机器人学习？", "工程机械仿真有哪些新进展？"]
    def choose_question(question):
        st.session_state["question"] = question
    for col, example in zip(st.columns(3), examples):
        col.button(example, on_click=choose_question, args=(example,), use_container_width=True)
    with st.form("ask_form"):
        question = st.text_area("你的研究问题", key="question", placeholder="描述关注的技术、场景或待解决的问题…")
        submit = st.form_submit_button("检索并回答", type="primary")
    if submit:
        if not question.strip():
            st.warning("请先输入一个研究问题。")
        else:
            from techradar.search import ask
            try:
                with st.spinner("正在检索资料并整理回答…"):
                    result = ask(cfg, db, question.strip())
                st.session_state["answer"] = (question, result)
            except Exception:
                st.session_state.pop("answer", None)
                st.error("暂时无法完成问答，请检查分析服务配置或稍后重试。")
    if "answer" in st.session_state:
        answered_question, result = st.session_state["answer"]
        with st.container(border=True):
            st.caption(f"问题：{answered_question}")
            st.markdown(result["answer"])
            st.divider()
            st.markdown("**参考来源**")
            for source in result.get("sources", []):
                if safe_url(source["url"]):
                    st.link_button(source["title"], source["url"])
            if not result.get("sources"):
                st.caption("本次回答没有引用具体来源。")

