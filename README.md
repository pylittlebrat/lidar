# Simulation Tech Radar（仿真技术雷达）

跨设备接续与核心演进记录见 [PROJECT_HISTORY.md](PROJECT_HISTORY.md)。

面向 **机器人仿真 / 具身智能 / Real2Sim / 数字孪生 / 工程机械仿真** 领域的自动技术情报采集与分析平台。

> 自动采集 → 清洗去重 → 技术分类 → LLM 深度分析 → 相关性评估 → 技术趋势识别 → 日报/周报 → 技术知识库 → 技术预警

它回答的问题不是"今天网上出现了什么"，而是：
**"最近仿真领域出现了什么值得关注的新技术？解决什么问题？成熟度如何？跟我们的仿真平台有什么关系？值不值得调研或做 PoC？"**

## 当前状态：可运行 MVP，采集、分析与报告已形成闭环

| Phase | 模块 | 说明 |
|---|---|---|
| 1 | 骨架 | 项目结构 / YAML 配置 / SQLite / CLI / 日志 / `TechItem` / `BaseCollector` |
| 2 | 采集器 | `GitHubCollector`（多条 Release + 关键词搜索 + Emerging 新仓库）、`ArxivCollector`（可重试状态自动退避）、`RSSCollector`，保留成功结果并明确部分失败 |
| 3 | 处理 | `Normalizer`、`KeywordFilter`、`Deduplicator`；资源身份与内容指纹分离，正文变更更新条目并重新分析 |
| 4 | LLM 分析 | `LLMClient`、`Classifier`、`Summarizer`（结构化分析）、`NoveltyScorer`、`RelevanceEvaluator`（四维加权评分） |
| 5 | 日报 | `DailyReportGenerator`：北京时间自然日窗口，概览 / Top / 四分组，标注模型、规则与历史未知的分析依据 |
| 6 | 周报+趋势 | `TrendDetector`（7/30/90 天频率，最近 7 天与前 23 天比较，数据不足时不判断涨跌）、`WeeklyReportGenerator` |
| 7 | Dashboard | Streamlit Web UI（Dashboard / Latest / Papers / GitHub / Trends / Search），`techradar dashboard` |
| 8 | 检索+问答 | `techradar search`（SQLite LIKE 检索）、`techradar ask`（RAG：中文问题 → LLM 查询扩展 → 检索 → LLM 回答+来源） |

GitHub / arXiv / RSS、SQLite、分类、分析、评分、日报和周报可以运行。检索问答属于轻量研究原型；独立工程机械采集器、GitHub tag/push/README 事件跟踪、通用网页正文补全和完整 PoC 决策流程仍待建设。启用未实现的信息源会明确报告不可用。

## 本次可靠性优化

- 采集逐仓库、查询和 feed 隔离失败，返回成功 / 部分完成 / 失败状态及失败资源；源不可用不再等同于“没有新情报”。GitHub 限流后停止本轮相关请求。
- GitHub 日常读取近 7 天 Release，默认每页 20 条、最多 2 页；历史模式默认每页 100 条、最多 5 页。分页预算不足会标记窗口不完整，尚不保证无限历史全量抓取。
- GitHub 与 arXiv 默认每天保留前 2 个高优先方向，其余关键词按北京时间日期轮询。GitHub 搜索和新仓发现默认合计最多 8 次查询。
- 同一 URL 的内容变更更新条目并进入分析队列，星数等观测变化不会单独触发付费分析。新颖度比较排除当前条目与其自身历史版本。
- 分析保存调用状态、尝试次数、模型与提示词版本；失败后按退避时间自动重试，最多 3 次。规则评分与模型成功单独统计，旧记录保留“历史未知”标记。
- 日报和周报使用北京时间自然日的开始、结束边界，历史报告不混入之后采集的条目；同一路径重复生成更新同一份报告记录。报告列出原文与分析依据，文本不足时提示仅作线索。
- 趋势比较最近 7 天与之前 23 天的日均频率，至少需要 14 天历史样本跨度和近 30 天 3 条样本；否则显示“数据不足”。历史样本跨度不等于信息源持续在线覆盖，回填内容也可能影响统计。

## LLM 配置（Phase 4）

分析默认走 **OpenCode Zen/Go** 的 OpenAI 兼容端点（已实测可用；DeepSeek V4 系列：`deepseek-v4-flash` / `deepseek-v4-pro` / `deepseek-v4.1-flash`）。

```bash
# 复制 .env.example 为 .env 并填写（.env 已被 gitignore）
LLM_API_KEY=oc_sk_xxx
LLM_BASE_URL=https://opencode.ai/zen/go/v1
LLM_MODEL=deepseek-v4-flash
```

- 成本控制（方案第 31 节）：先 taxonomy 关键词预分类，只有命中优先级词（或 watch 仓库）的条目才调用 LLM，其余仅存关键词得分
- 限流额度：`config/llm.yaml`（`max_items_per_run` / `llm_min_keyword_hits` 等）
- 每个条目输出 summary / problem / solution / innovation / maturity / recommended_action / relevance（内部项目 0-5 分）等结构化字段，评分 = 0.25×Keyword + 0.25×Source + 0.30×LLM + 0.20×Novelty

## 环境要求

- Python 3.11+
- pip
- （可选）GitHub Personal Access Token：设置 `GITHUB_TOKEN` 环境变量可显著提高 API 限额

## 快速开始

```bash
# 1. 创建虚拟环境
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

# 2. 安装（开发模式，含 pytest）
pip install -e ".[dev]"
```

## CLI

```bash
techradar --help            # 查看所有命令
techradar config            # 查看并校验 YAML 配置
techradar collect           # 采集 -> 归一化 -> 过滤 -> 去重 -> 入库（全部启用源）
techradar collect --source github --source arxiv   # 只采集指定源
techradar collect --limit 5 --no-store             # 调试：每源限 5 条、不入库
techradar analyze           # LLM 分析未处理条目并评分入库（Phase 4）
techradar analyze --limit 5 --dry-run              # 限量 / 只算不写库
techradar analyze --all --min-hits 2               # 分级全量：全部未分析扫一遍，仅对命中≥2 个优先词或 watch 仓库调 LLM（其余仅关键词评分）
techradar analyze --all --force                    # 补全模式：重跑所有 llm_status != 'llm' 的条目（仅关键词评分/旧数据/失败重试）
techradar daily             # 生成日报 reports/daily/YYYY-MM-DD.md（Phase 5）
techradar daily --days 7 --top 8 --date 2026-09-28 # 调窗口 / Top 数 / 指定日期
techradar weekly            # 生成趋势周报 reports/weekly/YYYY-Www.md（Phase 6）
techradar run               # 完整链路：collect -> analyze -> daily -> weekly
techradar backfill --days 730  # 历史回填近 N 天（90/730=两年）。collected_at=发布时间，不污染每日统计；--analyze 可选；arXiv 跨时间段分页采样
techradar radar             # 技术雷达状态表：评分 -> WATCH/INVESTIGATE/POC，按仓库/论文聚合沉淀到 radar 表
techradar search "real2sim" # 知识库关键词检索（Phase 8）
techradar ask "最近有哪些值得关注的物理引擎？"   # RAG 问答（中文自动查询扩展）
techradar dashboard         # 启动 Web Dashboard（Phase 7，Streamlit，默认 8501 端口）
techradar db init           # 初始化数据库（幂等，可重复执行）
techradar db status         # 查看数据库路径 / schema 版本 / 各表行数
```

## Web Dashboard（Phase 7）

```bash
pip install -e ".[dashboard]"   # 首次需安装 streamlit（可选 extra）
techradar dashboard            # 打开 http://127.0.0.1:8501
```

页面：**情报概览**（近 24 小时新线索、重点关注、待分析、优先阅读）、**技术雷达**（WATCH/INVESTIGATE/POC 状态表，可一键重新扫描）、**情报库**（统一搜索，来源/状态/方向/时间筛选，分页）、**技术趋势**（7/30/90 天频次）、**研究报告**（日报/周报阅读与下载）、**技术问答**（示例问题、回答与来源）。

情报卡片默认展示摘要、相关度与建议，展开后可查看技术方案、平台价值、内部项目相关度和原文。界面使用北京时间；模型成功、规则评分、失败待重试和历史未知分别展示。成功模型调用不代表结论已独立核验，判断仍应核对原文。浅色主题位于 `.streamlit/config.toml`，样式位于 `src/techradar/dashboard/style.css`。

首页默认展示最近一周的优先阅读内容，可切换“历史精选”和“最新入库”。侧栏的“刷新 · 同步最新情报”会在后台执行采集、分析、日报与周报生成，并显示阶段进度、新增数量和失败提示。手动同步沿用现有 LLM 额度配置，不修改每天 08:30 的自动任务。

手动同步和 `techradar run` 使用同一数据库对应的进程锁，避免重叠执行；手动重复点击会合并到正在执行的任务，CLI 定时运行遇到占用会等待。切换页面、关闭浏览器标签页不取消后台同步；关闭或重启 Web 服务会中断手动同步，下次打开可重新运行。最新同步状态保存在数据库旁的 `.sync.json` 文件中。

## Technology Radar（技术雷达状态）

- `techradar radar`：把近 90 天分析的评分沉淀为每项技术的持续状态——**WATCH（≥60）→ INVESTIGATE（≥75）→ POC（≥85）**；GitHub 按仓库聚合（版本系列合并为一条）、论文按去版本标题聚合，写入 `radar` 表（幂等 upsert，保留 first_seen）
- 例行版本更新（watch 仓库 + 版本号 + 与同源历史标题相似）自动降权（source 100→50）；主版本变化与 EA/RC/Preview 标记豁免——让具体开源工作、论文与新代码仓库占据雷达重点（配置：`scoring.yaml routine_update`）
- Dashboard「技术雷达」页展示状态分布与全表，可一键重新扫描

## 技术问答 / 检索（Phase 8）

- `techradar search "关键词"`：SQLite LIKE 快速检索（无需 LLM）
- `techradar ask "问题"`：检索 →（中文问题自动做 LLM 查询扩展）→ LLM 生成带来源的回答；资料不足时如实说明、不编造

## 定时任务（每日自动化）

```bash
# Linux（服务器使用北京时间，每天 08:30 跑完整链路）
30 8 * * * cd /path/to/simulation-tech-radar && .venv/bin/techradar run >> data/logs/cron.log 2>&1
```

```powershell
# Windows 计划任务：工作目录设置为项目目录，每天 08:30 执行
.venv\Scripts\techradar.exe run
```

现有每天北京时间 08:30 的自动任务使用 `.venv\Scripts\techradar.exe run`，执行采集、分析、日报和周报，并与网页手动同步使用同一进程锁。

也可用 `python -m techradar` 运行。

## 测试

```bash
pytest
# 或带覆盖率
pytest --cov=techradar
```

## 目录结构

```text
simulation-tech-radar/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── config/
│   ├── sources.yaml        # 信息源（GitHub watch 列表 / arXiv / RSS / 工程机械公司）
│   ├── keywords.yaml       # 优先级关键词 + 负向过滤词
│   ├── taxonomy.yaml       # 技术分类体系（10 个一级分类）
│   ├── projects.yaml       # 内部关注项目画像（FunLab / Real2Sim / ...）
│   └── scoring.yaml        # 相关性评分权重与档位
├── src/techradar/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py              # techradar CLI
│   ├── pipeline.py         # 流水线骨架（Phase 2 填充）
│   ├── config.py           # YAML 配置加载与校验
│   ├── collectors/
│   │   └── base.py         # BaseCollector 抽象基类
│   ├── processors/         # Phase 2：normalize/filter/deduplicate/enrich
│   ├── analyzers/          # Phase 2+：classifier/summarizer/relevance/novelty/trends
│   ├── llm/                # Phase 4：client/prompts/schemas
│   ├── storage/
│   │   ├── models.py       # TechItem 统一数据模型
│   │   ├── database.py     # SQLite 管理（幂等初始化）
│   │   └── repository.py   # ItemRepository 仓储（去重写入）
│   ├── reports/            # Phase 5+：daily/weekly + templates
│   └── utils/
│       ├── logging.py
│       ├── retry.py
│       └── time.py
├── data/                   # 运行产物（radar.db 等，运行时自动创建）
├── reports/                # 日报 / 周报输出目录
├── tests/
└── scripts/
    ├── run_daily.sh
    └── run_weekly.sh
```

## 配置说明

| 文件 | 内容 |
|---|---|
| `sources.yaml` | P0 仓库 watch 列表、GitHub 搜索关键词、arXiv 分类与关键词、RSS feeds、工程机械公司清单 |
| `keywords.yaml` | `priority_terms`（命中加分）、`negative_terms`（命中过滤） |
| `taxonomy.yaml` | 12 个一级分类及其关键词（含 real2sim / system_identification（系统辨识）/ embodied_ai（具身智能）等），所有内容统一映射到这套分类，LLM 重点方向也由此生成 |
| `projects.yaml` | 内部关注对象（FunLab / Real2Sim / Simulation Platform / Engineering Machinery / Robot Learning）及其兴趣词 |
| `scoring.yaml` | 评分权重（Keyword 0.25 + Source 0.25 + LLM 0.30 + Novelty 0.20）、档位、来源权重、关键词得分档 |
| `llm.yaml` | LLM 分析额度与参数（model/base_url/api_key 优先读环境变量） |

新增业务（叉车、装载机、遥操作…）只改 YAML，不改 Python。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `TECHRADAR_CONFIG_DIR` | `<项目根>/config` | YAML 配置目录 |
| `TECHRADAR_DATA_DIR` | `<项目根>/data` | 数据目录（radar.db 位于其下） |
| `TECHRADAR_LOG_LEVEL` | `INFO` | 日志级别 |
| `TECHRADAR_LOG_FILE` | 空（仅控制台） | 日志文件路径 |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | - | LLM 凭据（见上）；CLI 会自动加载项目根 `.env` |
| `GITHUB_TOKEN` | - | GitHub API 令牌（提高限速） |
| `TECHRADAR_SESSION_ID` | 随机 | 稳定会话 id（opencode 路由/缓存建议） |

## Roadmap

- **V1**：自动技术信息采集系统（GitHub / arXiv / Blog + SQLite + 日报）——主链路具备，持续补充内容质量与业务覆盖
- **V2**：技术情报系统（分类 / 趋势 / 周报 / Dashboard）——基础能力具备，持续改进趋势观测与预警
- **V3**：技术研究 Agent（RAG / 搜索 / Q&A）——轻量检索问答原型，知识图谱与自动 PoC 提案待增强
- **V4**：仿真技术决策支持（外部技术 → 能力 Gap → PoC 建议 → 平台 Roadmap）——下一步
