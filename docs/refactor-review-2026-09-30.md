# Simulation Tech Radar 重构与优化评估

评估日期：2026-09-30（北京时间）  
范围：当前工作区代码、配置、测试、现有数据库的只读统计，以及 9 月 29/30 日运行结果。

## 结论

项目已有可运行的采集—分析—报告—Web 闭环。当前最需要处理的是运行结果可信度、统一的数据口径、历史与状态管理；模块拆分应围绕这些问题进行。保留 Python + SQLite + Streamlit，先完成服务边界和数据契约，再考虑检索或部署增强。

本轮是代码与架构评估，没有修改业务代码、生产数据库或每天 08:30 的自动任务。新增此评估文档。没有执行真实联网采集或调用在线 LLM。

验证：
- 运行现有完整测试：126 passed in 19.70s。
- 在临时数据库或内存 mock 中复现新颖度、自比较导致例行更新降权、失败分析退出待处理队列、冷启动趋势、历史日报时间错配、作者字段丢失、旧相关度残留、人工雷达状态被覆盖、采集失败与结果丢失等问题。
- 本次读取时知识库为 521 条；分析状态为 llm 331、skipped 87、旧数据未标记 103。存在 simulation_engine / Simulation Engine、robot_learning / Robot Learning 等分类分裂。
- 最近定时运行状态文件显示 success、warnings=[]，而当次日志明确出现 GitHub 搜索限流和 Meta AI RSS 404。说明现有状态判定未贯穿采集器内部失败。
- 工作区当前没有可用 Git 仓库。大规模迁移前应建立版本基线，并用 SQLite backup API 建立一致性备份（当前启用了 WAL，不应只复制主 .db 文件）。

## 第一批：先修复影响结果的行为

### 1. 新颖度与版本降权排除自身，限定技术实体

证据：
- [pipeline.py](E:/ws/simulation-tech-radar/src/techradar/pipeline.py:237) 从已经入库的数据读取近期标题，然后把同一历史集送给新颖度及例行版本判断。
- [novelty.py](E:/ws/simulation-tech-radar/src/techradar/analyzers/novelty.py:36) 取最大标题相似度。
- [relevance.py](E:/ws/simulation-tech-radar/src/techradar/analyzers/relevance.py:94) 主版本历史没有限定同一仓库。

复现：当前条目处于近期 500 条列表时，新颖度为 0；首次 v3.0.0 Release 也可被判为 routine，来源分从 100 降为 50。历史已经存在的其他同标题记录不能一并删除，应该只排除当前 item ID。

调整：历史对象保留 item_id、entity_id、发布时间；评分排除自身，例行版本识别仅与同一仓库的较早版本比较。保留评分分量、规则版本、模型与 prompt 版本，使评分可解释、可复算。

验收：新技术、首次主版本升级、补丁升级与已有相似技术均有明确预期；分析入库前后得分一致。

### 2. 失败、规则评分、模型成功必须是不同状态

证据：
- [pipeline.py](E:/ws/simulation-tech-radar/src/techradar/pipeline.py:258) 失败写入 llm_failed，之后仍保存分析行。
- [repository.py](E:/ws/simulation-tech-radar/src/techradar/storage/repository.py:261) 待分析查询仅选择没有 analysis 行的条目。
- [data.py](E:/ws/simulation-tech-radar/src/techradar/dashboard/data.py:168) 和卡片统计同样按行存在判定已分析，读取时没有返回 llm_status。

复现：模型失败一次后，llm_errors=1、stored=1、pending=0；后续常规分析不会重试。“待分析为 0”不能证明模型分析都已成功。

调整：统一 pending / running / succeeded / skipped / failed / unknown 状态；为失败记录 attempts、next_retry_at、last_error_code，并提供有限重试。规则跳过在关注配置变化后可以重新分析。对 103 条未标记的旧数据保持 unknown，制定显式补处理策略，不自动宣称成功或全部重跑。

验收：失败后恢复服务可以补分析；失败、跳过、成功、旧数据分别统计；重试受次数、时间及 token 预算约束。

### 3. 修正历史报告、趋势与时间语义

证据：
- [daily.py](E:/ws/simulation-tech-radar/src/techradar/reports/daily.py:45)、[weekly.py](E:/ws/simulation-tech-radar/src/techradar/reports/weekly.py:47) 的日期参数影响标题和文件名，窗口仍依赖 utcnow()。
- [trends.py](E:/ws/simulation-tech-radar/src/techradar/analyzers/trends.py:87) 比较包含关系的 7/30 天日均。
- [pipeline.py](E:/ws/simulation-tech-radar/src/techradar/pipeline.py:165) 回填把 collected_at 改为发布时间，方便排除历史内容，但也混淆真实入库时间。

复现：生成日期 2000-01-01 的日报仍包含当前条目；仅 1 条新记录时趋势返回 up / +328.6%。正常包含窗口下 c7>0 而 c30=0 无法出现。

调整：
- 定义 published_at、ingested_at、analysis_at，并显式标记 backfill；报告说明统计按哪一种时间计算。
- 每个报告和趋势查询接受明确 as_of，按北京时间将日期转换为 UTC 半开区间 [start, end)，避免只有下界而包含窗口之外的数据。
- 趋势对比最近 7 天与之前 23 天，增加最小样本及观测覆盖，输出 insufficient_data；新方向由旧窗口为 0 且近期样本达标判定。
- 趋势在数据库全量聚合，不按高分抽取 5000 条后计数。

验收：同一历史截止日期重生成结果稳定；冷启动不宣称行业上涨；回填、跨日、未来日期及大量样本均有明确口径。

### 4. 修复数据写入完整性与分析原子性

证据：
- [repository.py](E:/ws/simulation-tech-radar/src/techradar/storage/repository.py:13) INSERT 字段遗漏 authors。
- [pipeline.py](E:/ws/simulation-tech-radar/src/techradar/pipeline.py:273) 分两个事务写 analysis 与 relevance。
- [repository.py](E:/ws/simulation-tech-radar/src/techradar/storage/repository.py:232) relevance 只 upsert，不删除本轮不存在的项目键。

复现：authors 入库后变为空列表；先写 funlab=5 / simulation_platform=4，再写 funlab=2，旧 simulation_platform=4 仍残留。第二个写入异常时，也可能出现新分析与旧相关度混用。

调整：save_analysis_result() 在一个事务内更新分析、替换相关度集合及关联分类，保留作者；约束失败不应全部误判为重复。为旧资料的作者缺失标记数据补全需求，不伪造恢复信息。

验收：所有模型字段往返保真；相关度空集合、删除项目、中途异常均正确；异常整体回滚。

## 第二批：统一执行与采集服务

### 5. 采集结果必须携带失败和完整性信息

[base.py](E:/ws/simulation-tech-radar/src/techradar/collectors/base.py:32) 当前仅返回条目列表；RSS/arXiv/搜索分支内部失败只写日志，[sync.py](E:/ws/simulation-tech-radar/src/techradar/sync.py:123) 无法识别这些失败。GitHub Watch 分支遇到超时还可能使之前成功抓取的内容一起丢失。

建议定义 CollectorResult：
- items、attempted/succeeded 查询数、failures（来源、错误码、是否可重试）、cursor、coverage_complete。
- 单仓库、单 feed、单查询失败隔离，保留已取得条目。
- 限流与 Retry-After/恢复时刻成为可报告状态。
- 来源全部失败应判定该阶段 failed；报告仍能生成不等于本次采集成功。

验收：唯一 RSS feed 404 时显示失败；第二仓库超时不丢第一仓库结果；UI、CLI、报告对同一运行展示一致状态。运行错误详情进入脱敏日志，用户界面可看到具体来源和可操作原因。

### 6. 同步任务从 Web 进程中独立出来

[ sync.py ](E:/ws/simulation-tech-radar/src/techradar/sync.py:169) 的 daemon thread 可以跨页面继续，但 Web 服务重启会终止；当前 .sync.json 只保存最后一次运行。[sync.py](E:/ws/simulation-tech-radar/src/techradar/sync.py:29) 等锁没有超时，其他 collect/analyze/backfill 命令及旧脚本也没有使用同一互斥边界。

目标：Web/CLI 提交任务 → 持久化运行记录 → 独立 worker → 统一执行服务 → 仓储与报告。
- SQLite 先承载 runs、run_steps、source_runs、analysis_attempts，保存 planned_at、started_at、finished_at、触发方式、配置版本、分阶段统计。
- 延续已实现的互斥；增加等待上限、任务租约/心跳和可恢复状态。
- 所有会改数据的入口统一经过执行服务；Pipeline.run() 目前只执行 collect，应明确改名或真正编排完整流程。
- scripts/run_daily.ps1 / .sh 调用统一 run 入口；Windows 脚本显式检查原生命令退出码。run_weekly.sh 当前仍是骨架，应接入现有 weekly 命令。
- 手动触发不改变每日自动规则。最近两次实际启动时间为约 08:47、08:51，应记录计划与实际启动时间并检查延迟原因；仅有 08:30 配置不能证明准点执行。

验收：重启 Web 后任务仍执行；中断后可以恢复/重试；手动与定时重叠只执行一个任务并保留定时请求；可以查看历史运行、失败来源、真实开始时间和产物。

### 7. 资源身份、变化事件、内容版本分开

[models.py](E:/ws/simulation-tech-radar/src/techradar/storage/models.py:59) 的 content_hash 实际只基于 source_type + URL。同 URL 的正文、描述或元数据变化被永久当作重复，因此资料与分析会陈旧。

建议分开：
- technology/entity：仓库或技术实体。
- item/event：论文、Release、重要变化事件。
- content_document/version：实际正文、payload_hash、获取时间和来源。
- analysis：分析对应的内容版本。

不要将 stars 的小幅变化都转成情报事件；明确触发分析的实质内容变化。

验收：同内容重复抓取幂等；同 URL 内容变化产生新版本；分析关联确定内容版本；历史资料仍可追溯。

### 8. 把覆盖和增量采集做实

[registry.py](E:/ws/simulation-tech-radar/src/techradar/collectors/registry.py:13) 只有 GitHub/arXiv/RSS；engineering_machinery.enabled=true 目前没有对应消费逻辑。GitHub 和 arXiv 固定查前 5 个关键词，后续方向长期没有对应查询覆盖。每日 GitHub 只读 latest Release，配置中的 tag/push/readme 没有实现；分页和游标不足会在中断恢复或高频更新时漏采。

建议：
- 加载配置时验证启用来源是否有实现，界面显示实际支持能力。
- 高优先级方向每日采集，其余按请求预算轮询，并保留每个方向的最近成功时间。
- 保存每源增量游标，重叠窗口去重，分页至已知边界；失败保留续跑位置。
- 工程机械先采用明确的公司新闻/RSS/页面白名单与人工维护，避免通用爬虫范围失控。

验收：连续多轮覆盖计划方向；一日多次 Release 均入库；恢复后补齐间断期；来源可用性与无新增明确区分。

## 第三批：分析与检索质量

### 9. 证据补全先于更复杂的 Agent

[enrich.py](E:/ws/simulation-tech-radar/src/techradar/processors/enrich.py:6) 为空壳；GitHub 搜索主要只有 description；[prompts.py](E:/ws/simulation-tech-radar/src/techradar/llm/prompts.py:69) 只使用 abstract，即使 raw_text 有正文也不读取。

建议先补 README、Release 正文和可靠页面正文。内容保存 source_url、fetched_at、content_hash、长度和获取状态。资料不足时输出 unknown 和有限的线索判断，不能仅凭标题推断成熟度、创新、开源或 PoC。关键结论关联证据摘录和推测标记。

验收：raw_text 有正文时进入模型上下文；标题级线索明确显示资料不足；报告 Top 条目也有原文链接。

### 10. 严格结构化契约和分类 ID

[schemas.py](E:/ws/simulation-tech-radar/src/techradar/llm/schemas.py:79) 只有类型转换，分类、成熟度、行动和项目键未做允许值校验；{} 也可能被当作成功结果。当前真实数据库已出现中英文/大小写分类分裂。

建议定义统一 AnalysisResult、AnalysisStatus、Action、Maturity；taxonomy 使用唯一 ID，展示名与别名分开。持久化 model_id、prompt_version、schema_version、scoring_version、内容版本和评分分量；旧类别映射必须可审计。配置增加来源/分类/项目/评分/额度的结构校验。

验收：非法分类、未知项目键和无实质内容的 JSON 不作为分析成功；分类筛选与趋势不分裂；可解释得分由哪些分量产生。

### 11. 检索相关性与业务评分解耦

现有 ask 逐词检索，达到 top 就停止，宽泛词容易占满上下文；业务相关度低于 40 的条目会被过滤，但项目相关度不等于对问题的证据价值。上下文只取摘要前 600 字，引用 URL 无效时也可能返回看似成功的答案。

建议先用 SQLite FTS5 检索与 BM25 排名，合并不同关键词候选后排序；正文切片并按问题找证据。向量检索作为有评估数据后再做的可选增强。引用只允许上下文 evidence ID，显示回答依据及资料不足。

验收：用 20–30 个真实技术问题评估候选覆盖、正确来源和无答案问题；不以“能返回字符串”作为问答验收。暂无足够规模或质量证据要求立刻引入向量数据库。

## 第四批：仓储、UI 与雷达决策

### 12. 分页与聚合进入数据查询层

[data.py](E:/ws/simulation-tech-radar/src/techradar/dashboard/data.py:64) 先读取最多 10000 条全文，每条额外查询 relevance，然后 Python 聚合、排序、分页。一个 12 条卡片页面可能执行约 10000 次额外查询；超出上限后总数、页数和版本计数不完整。当前 521 条库尚不足以证明严重延迟，这是明确的增长风险。

建议在数据库按 entity 聚合/选择代表版本，分页并 COUNT；对当前页批量查询相关度与版本。保留现有项目聚合体验，在精确计数与代表版本选择上实现统一规则。UI 数量明确区分原始条目数与聚合技术数。

验收：超过 10000 条数据时计数完整；同仓库版本跨页不重复；查询次数不随全库条目数线性增长。

### 13. 自动建议不能覆盖人工技术决策

[radar.py](E:/ws/simulation-tech-radar/src/techradar/radar.py:101) 每次扫描覆盖 status/reason/action；85/75 阈值硬编码且不读配置。人工 ADOPT 状态在扫描后已被复现为 POC。

建议分开 suggested_status 与 decision_status，保存操作者、决策时间、负责人和历史；自动扫描只更新建议。采用/归档状态不能因旧版本最高分再次被撤销。后续增加调研/PoC 记录、验证指标及内部能力 Gap，才能形成真正的决策闭环。

验收：ADOPT、ARCHIVE 和人工理由保留；自动建议变化可追踪；阈值调整与全库扫描结果一致。

### 14. 迁移、报告版本与模块边界

- [database.py](E:/ws/simulation-tech-radar/src/techradar/storage/database.py:124) 每次写 schema_version=1，再捕获所有 ALTER TABLE OperationalError。改为递增 migration，先迁移、校验、再更新版本；真正失败必须回滚/报错。
- reports 每次生成都 INSERT，同一天重跑已有重复记录；同周文件持续覆盖。为日报日期/周报 ISO 周定义唯一业务键，按需另存版本，明确更新同一份还是保存快照；文件写入使用临时文件替换。
- app.py 负责页面、格式化、同步交互和雷达扫描；data.py 与 repository.py 重复数据查询。拆成 pages、components、presenters，以及 application services；查询规则统一在仓储/查询服务。
- CLI 的命令分发与业务处理拆开，共用同一服务；脚本只负责启动、退出码与日志。
- 项目根目录当前依赖 __file__ 向上推导，适合源码开发，普通安装时配置/数据路径可能落到 site-packages。明确源码运行和安装部署的路径策略，并验证 wheel 中资源和配置入口。
- 有价值的回归测试应覆盖失败恢复、旧库迁移、数据往返、历史时间、真实分类、采集分页和多入口并发。现有趋势测试把单条记录 +328.6% 当作预期，应先修正业务断言。

验收：从旧 schema 真实升级且数据保留；重复报告生成有明确版本规则；Web、CLI、定时调用同样服务；普通安装与源码运行路径均可配置。

## 推荐实施顺序

| 批次 | 实施内容 | 验收重点 |
|---|---|---|
| 1：可信度修复 | 自比较、失败分析重试、历史日期与趋势、作者字段/相关度事务、采集错误透传 | 结果可信、失败可见、统计准确 |
| 2：执行服务与迁移 | 严格结果模型、数据库迁移、运行历史、独立 worker、统一写入口与时间模型 | 任务可恢复，手动与定时并发可靠，旧库可升级 |
| 3：采集与证据 | 资源/事件/内容版本、增量游标、覆盖轮询、正文补全 | 不漏采，来源明确，结论有证据 |
| 4：查询与决策 | SQL 聚合分页、FTS 检索、问答引用、人工雷达决策、UI 模块拆分 | 检索相关、页面轻量、决策留痕 |

各批次保留现有 CLI、Web 入口及每日自动任务的使用方式，迁移逐步进行。先建立 Git 基线、数据库备份和失败场景回归，再改写入路径。不能用“全部测试通过”代替情报质量验收；修复后应选择可验证的现有资料，核查排序、报告日期、来源引用及行动建议。

