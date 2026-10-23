# CPA-Agent（职导星图）

面向大学生职业规划的智能决策系统：简历解析 → 四维画像 → 人岗匹配 → 职业报告，辅以多智能体对话与知识图谱检索。

## Language

### Configuration

**Repo Config（仓库配置）**:
与代码同版本发布的 YAML/模板默认值，是业务规则的权威来源。
_Avoid_: 硬编码常量散落在 Python 模块中、把密钥写进 YAML

**Workspace（工作区）**:
按部署环境或机构挂载的运行时覆盖层，只改白名单内的数值与文案，不改规则语义。
_Avoid_: 第二套完整配置、与 repo 无 schema 约束的随意覆盖

**Workspace Minimum Boundary（工作区最小边界）**:
当前真实运行面只承认 `overrides/rules.yaml` 的白名单规则参数覆盖，以及 `overrides/prompts/<prompt_id>/tone.md` 的语气层替换。
_Avoid_: 把尚未被业务链路消费的 seeds、branding 或 schema loader 当成已上线能力

**Self-Managed Account（平台自建账号）**:
正式学生和管理员身份由平台 `user_account` 登录产生，服务端从数据库重载 role/status 后注入 `Principal`；管理员只可信 CLI 开户。新注册学生 ID 独立生成，不自动认领历史画像。开发头身份不是管理员凭据。
_Avoid_: 把客户端 role、用户名或 `X-Dev-Student-Id` 当作正式管理员授权

**Workspace Deferred Capability（工作区暂缓能力）**:
已存在 loader 或示例文件，但暂不作为下一阶段主线接入真实业务链路的 workspace 能力，如 seeds 追加和报告 branding。
_Avoid_: 为未验证的多机构、多租户或演示运营需求继续扩展 workspace 目录结构

**Skill Self-Reflection（技能自述式自评）**:
学生按服务端版本化题目主动选择接触/实践情况，保存当时方向、题目、选项与回答快照；“暂时无法判断”是未知，不是零分。它不等于考试、岗位标准、能力认证或已确认简历证据，不自动改变画像、匹配或课程进度；与 Agent 的 Memory Reflection 无关。
_Avoid_: 用本地题目阈值给出竞争力/达标结论，或用当前题包重建旧答卷

**Business Rule（业务规则）**:
可计算的领域约束与参数，如四维权重、匹配阈值、竞争力档位、R-05~R-08 规则参数。
_Avoid_: 把 Prompt 话术、API 密钥也叫作「规则」

**Rule Semantics（规则语义）**:
规则的结构与含义（例如「竞争力 = D1×w1 + …」、R-08 硬约束逻辑），由代码实现并冻结，workspace 不可改写。
_Avoid_: 在 workspace 里用 YAML 替换公式或门禁逻辑

**Tunable Parameter（可调参数）**:
规则语义下的数值或枚举，可在 workspace 覆盖，例如 D3 权重从 0.20 调到 0.25。
_Avoid_: 允许 workspace 修改「是否启用 D1 硬约束」这类语义开关（除非显式列入白名单）

### Prompts

**Prompt Bundle（提示词包）**:
一个逻辑 Prompt 的完整组装单元，由若干固定层文件合并而成，对外只暴露一个 `prompt_id`。
_Avoid_: 在代码里散落多个字符串常量指同一套人设

**Prompt Base（基座层）**:
不可被 workspace 删改的约束与契约（工具说明、JSON schema 引用、安全禁令、阶段感知逻辑）。
_Avoid_: 把语气形容词写进基座层

**Prompt Tone（语气层）**:
可覆盖的表述风格与人设细节，默认在 repo，可在 workspace 替换。
_Avoid_: 在语气层改输出字段或工具列表

**Prompt Registry（提示词注册表）**:
单一索引文件，列出所有 `prompt_id`、各层文件路径、消费方模块；新人只查此表。
_Avoid_: 靠目录结构猜测该改哪个文件

**PromptSelector（提示词选择器）**:
运行时按 `engine + interaction_mode + content_kind + optional subagent` 从 Prompt Registry 的 `runtime_routes` 选择进入模型的 Prompt Bundle；只接收枚举化 key 和已预算变量，不读取黑板、文件、记忆或请求对象。
_Avoid_: 在 ModeRuntime、prefix、scoped_invoke 或 Lead 中各自拼 prompt id、裸捕获缺失 prompt 后静默 fallback

**Content Kind（内容类型）**:
Prompt runtime route 的模型调用语义维度，如 `text`、`attachment`、`router`、`lead`、`thinking`；它描述“这次调用要解决什么模型契约”，不是用户文件 MIME 类型。
_Avoid_: 用文件扩展名或关键词临时决定加载哪组系统提示词

**Operational Data（运营数据）**:
按部署环境变化的业务内容增量，如冷启动岗位包、技能增补、图谱补充、报告机构 branding。
_Avoid_: 用户运行时数据（简历、会话）、冻结 Schema DDL；在运行面未接线前声称 workspace operational data 已完成

**Career Pathway Reference（职业方向参考链）**:
版本化公共职业方向事实、独立参考薪资、学习技能与课程关联。MySQL 保存业务身份、原文来源和不可变版本；Neo4j/Milvus 是按同一摘要派生、可重建的公共关系/检索投影。首期后端开发链复用现有账号、课程章节和技能词表，不改写个人成果；岗位事实仅供探索，不自动获得正式岗位发布或评分资格。
_Avoid_: 三库各自生成业务真值、将旧图展示 ID 当课程 ID、用检索相似度作为人岗分数

**Salary Reference Estimate（参考薪资估算）**:
允许在缺少完整岗位统计样本时给出职业/城市/级别适用的粗略区间，显式保存依据、币种/周期、观察与估算日期、方法版本和不确定性；与招聘方原文报价、样本统计结果分开。启发式系数不是实测分位数，不等于当年实时行情。
_Avoid_: 用估算填充招聘方薪资、真实平均值/箱线分布或需求量；因缺少完整统计就禁止任何明确标注的参考估算

**Runtime Data（运行时数据）**:
用户产生的、存在 DB / 上传目录中的数据，随使用增长，不属于配置。
_Avoid_: 放进 workspace 或 repo YAML

**Frozen Schema（冻结契约）**:
不可被 workspace 修改的数据结构与输出格式定义，如画像 v2 JSON Schema、抽取字段契约。
_Avoid_: 与可调默认值混放在 `defaults/` 目录

**Config Defaults（配置默认值）**:
Repo 中 `config/defaults/` 下的权威默认 YAML，可被 workspace 白名单覆盖。
_Avoid_: 称整个 `config/` 都为默认值（`prompts/` 与 `schemas/` 除外）

**Config Stack（配置栈）**:
启动时按固定顺序加载并合并配置来源；后层不得越权改写前层冻结内容。
_Avoid_: 多个来源同等优先级、靠「最后写入」碰运气

**Config Contract Hardening（配置契约硬化）**:
把 CONTEXT、ADR、loader 实现、测试用例和 workspace 示例统一到同一套可执行约定，优先修正文档与代码分叉、路径歧义和缺失的负向测试。
_Avoid_: 在契约未稳定前继续新增第二套配置入口或隐式覆盖规则

**Runtime Wiring（运行面接线）**:
确认已迁出的配置、Prompt、seeds、branding 和 schemas 被真实 API、Pipeline、报告或启动流程消费，而不是只停留在 loader 单测。
_Avoid_: loader 能读但业务链路仍读旧硬编码、SQL 或默认文案

**Config Contract Calibration（配置契约校准）**:
基于真实代码重新校准 CONTEXT、ADR 和测试描述，把“已实现 loader”和“已接入运行面”明确分开。
_Avoid_: 用阶段完成叙述掩盖实际消费链路缺口

**Runnable Demo（可运行演示版）**:
当前开源硬化阶段的交付边界：本地启动、核心 API 路由可注册、配置/Prompt 契约可验证、黄金演示链路可稳定走通，前端效果以真实可操作页面为准。
_Avoid_: 把多租户运营、全量外部依赖部署、历史竞赛文档完备性混入当前完成标准

**Golden Demo Path（黄金演示链路）**:
用于验收“效果达标”的最短端到端路径，覆盖健康检查、Agent 对话入口、Pipeline 冒烟入口、Match explain / hybrid 召回、Career 页面/API 路由，以及 **cpa-xingtu** 黄金域真 API 页面（契约驱动按域切换；不以 vue-kecheng 为联调面）。
_Avoid_: 只跑 loader 单测或只看静态页面截图就宣布项目可运行；宣称「全面接真」但契约冒烟未绿

**Real Service Integration（真实服务联调）**:
在本机后端进程与 dev Docker 数据库依赖之间，用真实 HTTP 请求验证黄金链路所需 API 与数据读写。
_Avoid_: 把生产容器部署、HTTPS、备份、全量 LLM 效果或所有历史接口都纳入当前联调边界

**Workspace Path（工作区路径）**:
运行时加载 workspace 文件的根目录；默认 `workspace/{WORKSPACE_ID}/`，可由 `WORKSPACE_PATH` 指向外部挂载路径。
_Avoid_: 与 repo 内 `backend/config/` 混为同一目录

### Pipeline

**Resume Agent Trace（简历 Agent 协作轨迹）**:
真实模式的专用异步 L0 上传按文档读取、结构化、实体核验、证据整理四个后端职责串行交接；MySQL 记录 owner-scoped、无简历正文的阶段/模型元数据。页面百分比只等于四个已持久提交检查点的完成比例，候选仍要用户确认，既不是模型思考文本，也不是画像/匹配评分进度。
_Avoid_: 把普通时间线改名为多 Agent；前端计时插值、伪造并行协作；用轨迹完成代替候选核对

**L0 PDF Engine Registry（L0 PDF 引擎能力登记）**:
PDF 解析引擎按「能力」而非「包能 import」登记：`READY | DISABLED | FAILED`。模式升级后 **auto 阶梯 = Parse Sidecar → PyMuPDF**；进程内 MinerU **退出 auto**（仅 `CPA_L0_PDF_ENGINE=mineru` 显式启用）。Sidecar 经 `CPA_L0_PARSE_SIDECAR_URL` HTTP 取 Markdown/JSON，未配置或不可达为 `DISABLED`，不阻塞启动。`docker-compose.parse-sidecar.yml` 的 `parse-sidecar-contract` profile 只是明确返回 503 的契约桩，不是 OCR；真实本机服务仍由 `mkg` 中的 `parse_sidecar_mineru.py` 提供。策略：`CPA_L0_PDF_ENGINE=auto|sidecar|pymupdf|mineru`。
_Avoid_: 把整仓 RAGFlow 当业务核心；auto 仍加载进程内 MinerU；Sidecar 不可达时启动失败

**Durable Media Job（持久媒体任务）**:
PDF/DOCX ingest 与 OCR repair 经 Celery 5.3 + Redis AOF 的专用 `cpa.media` 队列投递；`agent_file` 磁盘/MySQL 记录是权威状态，Celery task id 只做可追踪投递。任务使用 late ack、worker-lost redelivery 和幂等完成边界，Flask 重启不得把持久排队任务改写成失败。`ingest_meta.events` 与同一权威记录原子提交 `queued → ingesting → ready|failed`，通过整数序号 cursor 重放；它只覆盖媒体解析，不是全平台事件总线或 AG-UI。通用摘要/Reflection `fork_jobs` 仍是进程内旁路，不能据此宣称所有后台任务均可恢复。
_Avoid_: Redis/Celery 未启动时回退到请求线程跑 OCR；把 Celery result backend 当业务完成真值；把 MinerU 3.0.7 进程内 `/tasks` 当作重启后持久队列

**Retrieve Not Score（检索不计分）**:
Hybrid 召回（Milvus 向量 + MySQL 关键词 RRF + Neo4j 能力 hop）只产候选与 `retrieve_score` / explain 证据，**不得**改写 `match_result.total_score`。公开岗位候选必须从当前已核验的 `job_catalog_publication` 快照导出；Milvus 命中只提供排序线索，原始索引标题/正文不能作为公开岗位事实。公共职业方向另标 `role`，不能伪装成可匹配岗位。
_Avoid_: 用 rerank/LLM 覆盖规则分；把未发布旧画像或向量元数据当岗位目录；把 GraphRAG 全量社区索引替换领域 Neo4j schema

**Portrait（画像）**:
学生或岗位的四维能力刻画（D1 基础 / D2 技能 / D3 素养 / D4 潜力）。输入须含 NS-LE LLM 产出的 **student_profile**（`POST /nsle/resume` → entities 适配）；职途导航链路：L0 文本 → **nsle/resume** → **extract/skills(use_llm)** → infer-role → portrait/student。
_Avoid_: 前端哈希伪造 GPA；`extract/skills` 长期 `use_llm=false`；用自造正则替代 NS-LE；空 profile 硬评七维

**Match（匹配）**:
人岗对齐结果，含总分、技能覆盖率、缺失技能与是否通过硬约束；分数由 rules/代码确定性计算。Explain：`GET /api/match/explain/...` 提供 evidence、关键词覆盖、图谱路径（LLM 仅可润色文案，不可改分）。
_Avoid_: 画像本身、职业报告（后者是 **Career Report**）；LLM 改写总分

**Source-Only Job（仅供来源查看的岗位）**:
固定官方来源原文与摘要经正式管理员核对后独立公开的岗位记录；即使模型审查因资格条件超出当前评分器而 blocked，也可只展示原文。`source_csb_` ID 与可评分发布仓库分离，`match_eligible=false`，L3/L4 不建任务、不出分数或岗位报告；来源截止时间后隐藏，不保证核验后一直仍在招聘。
_Avoid_: 把来源核对当成资格评分完成；把模型候选、旧画像行或 source-only 条目自动升级为可评分发布

**Agent Topology Freeze（Agent 拓扑冻结）**:
运行时允许的 subagent / 专家集合冻结；模式升级期只硬化门禁、budget、trace/eval，禁止新增角色。
_Avoid_: CrewAI 式扩 agent；用多 agent 头数代替工具门禁

**Interest Explore（兴趣探索）**:
短兴趣关键词 → 职业方向候选（`GET /api/career/explore`）。输入是用户兴趣短语，不是简历。
_Avoid_: 把简历原文/联系方式/长片段塞进 `q`；用 explore 代替技能→方向推断

**Role Inference（方向推断）**:
从 **Extracted Skills**（及可选 preferred_role）推断目录岗位名（如 `RAG工程师`），`POST /api/career/infer-role`。主信号：Neo4j `(CareerNode)-[:REQUIRES]->(Skill)` 覆盖率 + hub-skill IDF；次信号：`career_insights.core_skills` 覆盖（含 missing）；taxonomy `related_roles` 仅作 OOV/稀疏图三级。职途导航：parse → extract/skills → infer-role → Portrait；FE 保留目录角色名，五域 `CareerRole` 仅用于本地装饰图。
_Avoid_: 用简历原文调 explore；taxonomy 投票当唯一 scorer；把 BE 目录角色压成「前端/后端/…」五枚举再当预测结果

**Career Report（职业报告）**:
面向学生的可保存**岗位专项**规划输出，整合可信岗位来源、画像、匹配、技能差距和学习路径建议；没有已发布岗位与可验证匹配时保持 `partial`，不得填入模拟市场结论。
_Avoid_: Pipeline 任务状态、单次 Agent 回复、未保存的页面临时分析；把仅有简历的诊断报告伪装为岗位专项报告

**Resume Diagnostic Report（简历诊断报告）**:
仅依赖当前由用户确认的 L0 简历及页块证据，独立于 L2 画像分数和 L3/L4 岗位目录；结构化呈现所有非空业务事实，并把模型语义综合单独标记为待核验解读。生成链路 `completed` 只在简历范围内成立，引用合法不等于建议事实性签审。
_Avoid_: 因可信岗位目录为空就不出简历报告；给简历范围报告填岗位匹配分、市场薪资或录用概率

**User Self-Report（用户自述补充）**:
已确认 L0 简历之后，学生可以另行填写沟通、协作、压力应对和责任担当的等级与具体经历。它绑定当前简历确认任务并带修订号，来源始终标记为 `user_self_report`，不是简历原文证据；修改后旧画像失效，L2 可用它补足 D3 候选。学校档位、专业类别、缺失页面和简历诊断报告的原文事实不因此被补齐或改写。
_Avoid_: 把自述伪装为引用原文、用自述绕过缺页门禁、修改后继续展示旧画像或匹配结果

**Skill Exploration Supplement（技能方向探索自述）**:
学生还可在当前已确认简历之外补充技术技能名称，独立保存在 `_skill_exploration`，与 D3 用户自述采用不同修订号；仅以 `source_kind=user_self_report`、`scoring_eligible=false` 的服务端快照参与单独标记的职业方向探索。它不改写简历技能、L2 四维/综合分、个人技能图谱或简历诊断；更新它不使原画像失效。新简历确认替换并清除旧探索自述。若要进入评分，须补充原简历并重新解析确认。
_Avoid_: 把自述技能混入已确认技能请求、把方向排名当胜任力分、仅因自述变化重算或发布画像

**Student Workbench Experience Target（学生私人工作台体验目标）**:
已确认的产品目标见 `docs/specs/student-workbench-experience.md`：学生主任务区、可逐条展开的只读 Agent 过程、简历报告与画像的独立状态、基于证据的部分结果及版本区分。画像目标完成语义允许未知维度留空，不以四维都有分作为唯一门槛；这与当前 L2 运行门禁可能不同，尚须后端契约迁移和真实验收。
当前已完成 P0 只读投影、P2 确认后自动后台首切片及 P1 新画像快照首切片：`GET /api/profile/student/workbench` 按当前 Principal 分别回读 L0、`analysis_run`、简历诊断和 L2 画像；简历确认与用户自述修订在 MySQL 事务中留下后续意图，worker/Beat 无需页面保持打开即可处理或重派。新生成的 L2 `partial/completed` 与独立 `student_portrait_snapshot` 同事务保存，保留可为空的四维结果和稳定 `snapshot_id`；旧 `student_portrait_4d` 仍是正式评分/匹配门禁。owner 可只读查看历史版本，但旧版不作为当前分数。历史遗留画像的有证据迁移、诊断报告历史、P3 逐条真实工具记录及 P2 故障/预算全矩阵验收尚未完成。
_Avoid_: 把产品目标写成当前已实现能力；以任务检查点 100% 冒充两个成果都完成；为完成画像而给未知维度造分

**Entity Linking Proposal（实体对齐建议）**:
保留简历原文 mention 和逐字引句，在服务端可验证的公开实体候选中由模型判 `equivalent/related/unresolved`；这是建议而非归一化真值，不自动改写用户技能或发布公共图谱。候选检索溢出、模型截断或证据不足必须显式 unresolved。
_Avoid_: 把相似度直接视为等价、把 related 误写为同一实体、把模型提议当作用户已确认事实

**Honest Career Stance（诚实职评立场）**:
Agent 的评价身份：像懂校园招聘的资深 HR / 职业教练——结合人才市场筛选逻辑、真实招聘心理、学生认知负荷，而不是「简历关键词打高分机器」。
- **先抓决定项**：院校层级与岗位赛道的现实门槛、可验证项目深度、岗位稀缺度与竞争密度，优于堆砌证书数量或空泛「潜力很大」。
- **双轨表述**：一边说清概率与摩擦（如二本进算法/Agent 研发赛道很难），一边保留可执行跃迁路径与「证据齐了则皆有可能」的自信，不鸡汤、不恐吓。
- **证据优先**：能引用 JD 门槛、缺口技能、同类反馈时优先引用；无实时舆情源时明确「基于常见招聘筛选经验」，禁止伪造论坛/内推评价。
- **重点 > 全面**：每轮评价只突出 2～4 个最影响录用决策的点，禁止面面俱到的虚高赞美。
_Avoid_: 为鼓励而抬高匹配分；用「很不错」掩盖赛道不匹配；把学校标签当唯一命运判决

### Agent Runtime

**Principal / RequestScope（可信请求作用域）**:
用户资源的服务端身份契约。Demo 模式固定为 `CPA_DEMO_STUDENT_ID`；strict 模式必须由认证适配器提供 Principal，否则返回 401。Session、Memory、文件、挂载、语音、Profile、收藏和消息都以 `RequestScope.student_id` 做 owner 校验。
_Avoid_: 从 body/query/header 里的 `student_id`、`user_id`、`session_id` 直接决定资源归属；缺 owner 时“先放行”

**Supervisor（编排器）**:
始终面对用户的编排 Agent；通过 clarify / call_subagent / 白名单 plan 调度子 Agent，自身不持有业务工具。
_Avoid_: 把子 Agent 对话权 handoff 给用户、或让 Supervisor 直接调用 pipeline_run

**Blackboard（黑板）**:
会话级业务态分区存储（白名单 key + 单写者 ACL），存 id/摘要指针而非全量 JSON；子 Agent 按声明 key 读写。
_Avoid_: 扁平 `student_context` 整包注入、多角色覆写同一业务字段

**TaskPacket（任务包）**:
Supervisor 调用子 Agent 的契约（goal、read_keys/write_keys、可选 user_utterance）；plan 步骤使用瘦包。
_Avoid_: 把完整对话历史或大段业务 JSON 内联进子 Agent

**ContextAssembler（上下文组装器）**:
独立负责“哪些字节进入哪个模型调用”的运行时模块，输出 `system_head`、`system_tail`、`messages`、`router_context`、`memory_digest` 和 `attachment_context`；不负责身份、owner 或工具权限判断（这些仍由 `RequestScope` / mode budget / PreToolUse 管）。
_Avoid_: 在 ModeRuntime、prefix、SessionManager 或 scoped_invoke 里各自拼可变上下文；把访问控制和 prompt 字节组装混在一起

**Context Slot Budget（上下文槽位预算）**:
按 `mode × slot` 固定字符上限：summary、history、blackboard card、memory digest、attachment card/document pack 分槽裁剪；默认 estimator 是字符近似，可替换为 tokenizer，但一个槽不得挤占 Prompt head、安全规则或其它槽。Document Access 的 `complete/partial` 语义不得由通用 assembler 静默改写。
_Avoid_: 用一个全局 max_chars 截断整段 prompt；附件正文被 head-slice 后仍标 `complete=true`

**Memory Store（记忆库）**:
按 `student_id` 分库的跨会话记忆（path 语义：`USER.md` / `MEMORY.md` / `memory/session-*.md`），带版本与 rollback；仅挂载进 Supervisor。
_Avoid_: 把记忆全文注入每个子 Agent、或用 SessionManager.context 冒充跨会话 LTM

**STM 单通道注入**:
每个模型调用的会话短期记忆只走一个显式通道：Ask 最多最近 K=8 轮消息；Agent / Experts Lead 最多最近 4 条角色标记历史消息，并可配短摘要。禁止再叠加 structured_context.recent_messages 或 checkpoint 聊天记录。
_Avoid_: checkpointer 全量回放聊天 + Session 历史再注入一遍

Agent / Experts 的 Lead 不接收完整聊天记录；最近历史在独立 history 槽内注入，并按角色序列化。历史/摘要仅作意图续接与纠正线索，不是事实、业务状态或授权；历史助手的完成/取消声明必须从服务端回读核验。超出字符预算时显式裁剪并将本轮标为上下文不完整；计划子 Agent 仍不接收 STM。
_Avoid_: 为修复近期历史缺失而将完整会话塞入系统提示或 TaskPacket；把旧助手声明当成果真值

**Conversation Thread（对话线程）**:
对齐 Claude / Qoder 的多会话：每个线程绑定独立 `session_id`（STM：history / blackboard / summary）；前端消息胶带按线程隔离，新建对话必须 `create_session` 而非复用旧 id。学生级 Memory Store 仍是跨会话 LTM，不属于聊天 STM。
_Avoid_: 全局单一 `aiMessages` 胶带跨会话复用；UI 历史与后端 `session_id` 脱节

**STM / LTM 注入边界（模式预算，不是关键词嗅探）**:
对齐 Claude / Qoder：隔离靠 **mode budget + session_id**，不靠正则判断「这是不是寒暄」。
- **Ask**：默认 `allow_memory_read=False`，只看本会话 STM（history / summary / session blackboard）；学生 LTM 仅在请求显式 `use_memory=true` 时挂载
- **Agent / Experts**：预算允许挂载 `USER.md`/`MEMORY.md`
- 任何模式都不得把简历概况伪造成用户原话写入 STM（禁止 `[用户概况]…| 询问：`）
_Avoid_: 用关键词表决定是否挂记忆；Ask 默认吃跨会话 MEMORY

**model_view / ui_view（出口层）**:
对齐 Claude / Qoder：内部手册、工具名、黑板 key 只存在于 Agent/Experts 的 model_view；Ask 用户通道只加载产品语义契约；最终 `reply` / `thinking_steps` 经 egress 再出站。
_Avoid_: 把 Work Manual 整包塞进 Ask system prompt；靠聊天里「过滤几个词」代替通道隔离

**Agent 上下文工程（最小权限）**:
| 角色 | 可进模型 | 坚决不进模型 |
|------|----------|--------------|
| Ask | 产品契约、本会话摘要/进度卡、最近 K=8 轮 + 当前轮；显式 `use_memory=true` 才挂 LTM 摘要；附件正文只经 Document Access 完整性规则进入 user 通道 | Work Manual、MEMORY 默认、重复 recent_messages、电话/邮箱/`*_json`、把附件 excerpt 当全文 |
| Agent 路由器 | 已填充进度卡、短摘要、最多最近 4 条角色标记历史、当前轮、可选记忆摘要（≤800字）、附件挂载摘要 | 空黑板枚举、完整历史、约束全文、附件正文、子 Agent 工具表全文 |
| Experts Lead | 已填充进度卡、短摘要、最多最近 4 条角色标记历史、当前轮、可选记忆摘要（≤800字） | 完整历史、附件正文、LTM 下发到计划子 Agent |
| 子 Agent | 角色提示 + system 内瘦 TaskPacket + **投影后**黑板指针切片 + user 内任务正文 + 工具 model_view | USER/MEMORY、完整对话史、`constraints.user.text`、原始 student_id、未声明 key、长正文复制进 TaskPacket JSON |
_Avoid_: 把 Supervisor 前缀原样灌进子 Agent；`attachment_read` 无上限倾倒简历；Ask+附件仍加载 `agent.manual`

**Experts 计划门禁**:
Experts 是协作预算，不是「一进模式就强制 end_to_end」。Lead 输出意图包（对齐 DeepSeek 先重述再行动 / Kimi 自包含 brief）：`user_job`、`goal_summary`、`missing_slots`、`plan_sketch`、`handoff_brief`，再决策 `explain` | `clarify` | `run_plan`。模板白名单与关键词只作 hint；`user_job=meta` 与缺 `resume` 等软槽位可确定性覆盖为澄清。步骤 `ok` = 实质业务载荷，不是 `present: true`。
_Avoid_: 关键词主闸；再叠一层自由文本 Intent Router；前端 `plan_mode=experts` 恒真；空跑三步标「完成」

**Interaction Mode（交互模式）**:
一等公民运行时模式，对齐 Qoder Ask / Agent / Experts：
- **Ask**：默认轻量问答；零业务写工具；强任务意图返回 `upgrade_hint`，不自动偷升
- **Agent**：单 TaskPacket → 单个子 Agent
- **Experts**：Lead + 白名单多步计划与步骤 commit
模式由前端切换或请求体 `interaction_mode` 指定；会话可持久化。
_Avoid_: 用关键词/寒暄正则冒充模式架构；Ask 下假装已执行 pipeline

**Ask 内加速**:
Ask 模式下对纯寒暄可用模板直出；从属于 Ask 预算，不是独立架构层。
_Avoid_: 把正则快路径当成全部路由系统

**Fork Jobs（旁路任务）**:
摘要 rollup、Reflection 等在主回复返回后异步执行，不阻塞 TTFT。
_Avoid_: 在 `/chat` 请求线程内同步跑 Memory Reflection / 摘要 LLM

**Permission Mode（权限模式）**:
与 Interaction Mode 绑定的工具许可层：Ask=`dont_ask`（未预授权即 deny，写工具对模型不可见）、Agent=`default`、Experts=`plan_allow`（仅计划步骤内预授权工具）。HTTP 无交互时 `ask` 决策按 deny 处理，并预留 `pending_tool`。
_Avoid_: 仅靠模型自觉或出口打码充当权限控制

**PreToolUse（工具执行前闸门）**:
每次工具调用前强制：可见性检查 → 权限评估 → 会话 `student_id`/`session_id` 作用域绑定（覆盖模型传入的他人 id）→ 危险/缺参拒绝。统一经 gated tools，禁止绕过。闸门注入的作用域参数必须经 `tool.func(**args)` 下传；禁止只走 `StructuredTool.invoke`（会按 args_schema 丢掉注入字段）。`tool_audit_event` 由 runtime schema ensure 幂等建表。
_Avoid_: 模型随意填写 `student_id`；Ask 下仍绑定 pipeline/写工具；审计表缺失时刷 ERROR 而不建表

**Tool Result Dual Channel（工具结果双通道）**:
同一次执行拆成 `model_view`（写入 ToolMessage 供下一轮 LLM）与 `ui_view`（进入 API `tool_calls`）；审计 payload 旁路落库，不进聊天。
_Avoid_: 把原始 ToolMessage 原文同时塞给模型和 UI

**Turn Event（回合事件）**:
Agent 单轮执行对外可观察的类型化事件，如 `turn_start`、`progress`、`tool_done`、`artifact_committed`、`reply_delta`、`turn_done`；同步 JSON 与 SSE 必须从同一份 PublicTurn/事件折叠结果生成，不输出原始思维链。
_Avoid_: SSE 和同步接口各维护一套字段；把模型 chain-of-thought 当事件流

**Message Part（消息块）**:
聊天历史中可持久化、可回放的消息组成单元，首批为 `text`、`progress`、`tool_call`、`attachment`、`artifact_ref`、`upgrade_hint`、`notice`。前端只按类型与 schema 渲染可信组件。
_Avoid_: 刷新后只恢复 `role/content`；从 Markdown 或工具摘要里正则提取业务字段

**Domain Artifact（领域成果物）**:
画像、匹配、职业报告、行动计划、文件或管线任务等可再次打开的业务成果引用；聊天里只保存 `ArtifactRef` 和安全 preview，完整数据仍在权威业务存储中。
_Avoid_: 复制完整画像/报告 JSON 到聊天消息；让模型生成任意 HTML/React 作为成果物

**Artifact Commit（成果物签发）**:
工具结果中的候选成果物，或服务端基于本轮 planner 正文生成的行动计划，必须先有同 owner 的权威领域记录，再按 `student_id/session_id/turn_id` 写入 `agent_artifact` 索引，才能成为公开 `artifact_ref`。行动计划正文在 `agent_action_plan`，索引与正文同事务提交；Artifact 查询必须复用 owner guard。
_Avoid_: LLM 自称“已生成”就展示完成卡；opaque artifact_id 绕过学生归属校验

**Business Completion（业务完成）**:
黄金链每一步完成以“权威业务存储或任务记录已提交 + artifact 已签发”为准；黑板只保存 pointer/摘要，不把 `present=true` 或自然语言回复当完成。
_Avoid_: 仅凭工具跑过、回复里说完成、或 heuristic blackboard stub 判定画像/匹配/报告完成

**Field Policy（字段策略）**:
字段分级 `never` / `model_ok` / `public`；投影后再进 model_view / ui_view。`db_query` 等格式化与此对齐。
_Avoid_: 把 phone/email/`*_json` 当 model_ok 明文下发

**Tool Egress（工具出口边界）**:
最后一道 UI 保险：对已是 ui_view 的 `tool_calls` 再跑脱敏；另对 `reply` 做轻量 PII 掩码防模型复述。不再作为唯一防线。
_Avoid_: 把 egress 当成权限与作用域的主控

**PublicTurn（公开回合）**:
Agent runtime 结果离开服务端前的唯一公开投影，包含 reply、parts/events、tool cards、thinking、permission_denials 与最小 orchestration；不包含 raw blackboard、supervisor_view、plan_results、文件 id、原始工具参数或 provider 异常。
_Avoid_: JSON、SSE、history 各自二次清洗导致字段漂移

**EgressPipeline（出站管线）**:
把内部 runtime result 投影为 PublicTurn 的单点模块，复用 reply/thinking/tool egress 与字段策略；HTTP JSON、SSE 事件、session history/context 都从同一公开投影派生。
_Avoid_: 在 API handler 中直接返回 `session.context.blackboard`、消息 metadata 内部 orchestration 或 raw tool result

**Agent File / Attachment Artifact（会话文件与产物）**:
对齐 Claude Files + Qoder Files：上传 HTTP 只做字节入库 → `file_id` + `status=ingesting`（秒级返回）；ingest 经 fork 异步：先轻量（pypdf/docx），空则在 **worker 内**升级 L0/MinerU → `ready|failed`。禁止在请求线程跑 MinerU。前端轮询至终态；超时不得假标 ready。`CPA_CHAT_INGEST_HEAVY=0` 可关闭聊天重型升级。必须绑定 `student_id`。原文不进聊天 JSON。
_Avoid_: 前端全文塞 `message`；无归属上传；`/api/agent/files` 同步跑 MinerU；轮询超时仍当 ready 发送

**Document Access（文档访问层）**:
四层，切块只是兜底不是产品：(1) **complete pack**——全文≤Ask 预算（约 48k，长上下文整文注入）且标 `complete=true`；(2) **catalog + sections**——带 `offset` 的章节 TOC（导航索引，不是 profile 抽取）；(3) **find / section**——按问题或 `section_id` 跳读，`coverage=partial`，永不可因命中标 `complete=true`；(4) **sequential read**——`offset`+`has_more`/`next_offset` 穷尽阅读。Ask 走 `materialize_for_ask`：服务端整文物化，薄 PDF 轻量抽取（有技能无「教育经历」等）须升级 L0/MinerU，禁止把「请用户重传」当默认策略。挂载卡不得塞 excerpt 正文（文末教育会被误判为未读）。禁止静默 head-slice 冒充全文。
_Avoid_: 调大 max_chars 当唯一修复；把顺序分页当唯一策略；Ask 注入「摘录」却不声明未读完；凭 find 局部命中或挂载卡 excerpt 断言「只看到开头」；通用 tool egress 切窗却保留 `complete=true`

**Resume Structure Truth（简历结构真源）**:
职途导航评分的唯一结构输入是 LLM canonical `student_profile`（全文单次抽取，超长则窗口抽取 + LLM merge），再经 `structure_validation` 字段齐备校验。Agent 附件 `full_text` 不等于职途导航已结构化；两者必须显式共享同一 profile，禁止用简单正则从原文「修补」院校/专业/奖项。校验失败 → 拒评，不得把空结构打成「解析完成」零分画像。
_Avoid_: 静默截断上传文本；用正则猜 school/major；FE 把缺失子分与真实 0 分混为一谈后继续评分

**Session Resource（会话挂载）**:
对齐 Qoder resources：`file_id` → `session_id`（磁盘+MySQL）；聊天用 `attachment_ids` / `content_parts`；STM 在归一化后落库（含 `[附件:]`/`[语音]` 标记）。
_Avoid_: 未挂载却 `attachment_read`；挂载失败静默继续

**Content Parts（内容块）**:
`text` / `document` / `image` / `voice_transcript`；Supervisor 挂本回合摘要卡；正文经 Document Access / `attachment_read(catalog|find|section|read)`（须已挂载+student 匹配）。Ask 在有挂载附件时仅开放 `attachment_read`（超预算时）。
_Avoid_: 巨型 prompt 冒充多模态；Ask 挂载卡却写「请用 attachment_read」同时零工具

**Voice Turn（语音回合）**:
`capturing → transcribing → ready|failed`；浏览器 STT 是适配器；磁盘 catalog + MySQL；与 message 去重；编辑输入后清除 sticky `voice_turn_id`。
_Avoid_: 仅填输入框无 turn；旧 turn_id 粘到后续纯打字消息

**Thinking Egress（思考出口）**:
两条通道不可混用：(1) **Operator playbook**——给模型/运行时的操作剧本（「先确认…再决定…」「轻量模式下可…」），只进 prompt/日志；(2) **UI thinking**——第一人称、基于本轮事实的推演（「我看到用户挂了《简历》…」）。出站闸门丢弃剧本口吻，并清洗内部 key/工具名。Ask 附件路径优先解析 `<<<THINKING>>>`，缺省时用事实合成，禁止把 `ask_fallback` 剧本原样塞进思考区。
_Avoid_: 把系统指令/操作清单当思考独白展示；用正则改几个词冒充产品思考

### Competition Document Revision

**网挑技术文档第七版**:
当前论文式技术文档基线，后续所有章节修订都应从 `网挑文档第七版.docx` 或最近一次确认过的章节修订稿继续。
_Avoid_: 从旧版第六版、临时脚本输出或未确认的中间稿继续改

**章节级修订**:
按第一章、第二章、第三章的顺序逐章修改、逐章审核，每次只交付当前章节已处理的新稿。
_Avoid_: 一次性脚本重写整篇文档、跨章节批量改导致难以审核

**文字性内容调整**:
在保留原有图片、图注、表格和章节结构的前提下，重写、压缩、补充或统一正文表达。
_Avoid_: 删除已有图注、替换已有截图、擅自插入新图片

**图片占位符**:
仅用于缺少图注且确实需要新增软件工程图的位置，说明图类型、图题和应展示内容。
_Avoid_: 已有图片和图注的位置重复写占位符

**软件工程图**:
用于解释系统结构、业务流程、顺序交互、E-R 数据关系、状态流转或智能体协作的工程图。
_Avoid_: 纯展示截图、装饰图、没有工程解释价值的图片

**UML顺序图插入策略**:
第三章优先放面向学生阶段的业务顺序图，第四章优先放面向核心技术链路的系统交互顺序图；图片由用户后续插入时，文档中先保留引导文字、待插入图位和正式图注。
_Avoid_: 按现有图片文件名机械决定插图位置，或把实现交互顺序图放到第五章测试章节

**图注**:
图片下方的正式论文说明文字，格式为“图X-X 标题”，已有图注即视为该位置已有图。
_Avoid_: 把正文里的“如图所示”当成图注

**服创问题迁移**:
把服创答辩问题中已沉淀的系统边界、安全、RAG 可信、Agent 防环和画像防抖内容写入网挑文档。
_Avoid_: 把 Q&A 原文整段粘贴到论文中

**适用边界**:
系统主要适合具备自学意愿、愿意持续补充信息并根据反馈调整计划的大学生，不能泛化为适合所有学生和全部职业决策；也不是替代校招 HR 或给出录用承诺。
_Avoid_: 声称系统能替代就业指导教师或覆盖所有学生情况；把「皆有可能」说成「很容易」

**画像防抖机制**:
防止短时间探索多个岗位导致长期画像频繁变化的更新控制机制。
_Avoid_: 用户问一次岗位就立即永久改写画像

**Agent 防环机制**:
通过最大调用深度、重复调用检测、动作状态约束和 trace 记录防止智能体循环调用。
_Avoid_: 让 Agent 之间无限互相调用或无终止条件

**RAG 可信度评分**:
结合语义相关性、来源可信度、交叉验证一致性、数据新鲜度和画像一致性评估检索证据。
_Avoid_: 只凭向量相似度或模型口吻判断回答可信

**字段中文化**:
论文正文和表格优先使用中文业务名，英文表名、字段名或类名只在首次出现时括号说明。
_Avoid_: 在正文密集堆叠 `student_profile`、`match_result`、`Planner Agent` 等代码式名称

**表格单元格无缩进**:
Word 表格单元格内的段落文字不做首行缩进，保持左对齐或沿用原表格单元格段落格式。
_Avoid_: 把正文首行缩进样式套到表格单元格里

**标题格式沿用**:
章节标题、小节标题和图表题名必须沿用原 Word 文档已有标题样式与层级格式，不自行新建或改造标题样式。
_Avoid_: 自己重新定义标题字体、字号、编号或样式名

**网挑作品正式题目**:
职导星图——基于复杂简历解析与GraphRAG的大学生职业规划多智能体决策系统。
_Avoid_: 继续使用“大学生职业规划智能体系统”“高校就业能力导航系统”等临时题名

**合理构造测试数据**:
第五章系统测试允许使用与项目功能、性能和安全边界一致的合理测试样本、统计值和缺陷记录，用于形成完整可审阅的测试章节。
_Avoid_: 把构造测试数据写成线上真实运营数据，或写出无法由现有系统能力支撑的夸大结果

## Relationships

- **Repo Config** 提供 **Business Rule** 的默认值；**Workspace** 仅覆盖其中的 **Tunable Parameter**
- **Rule Semantics** 由后端代码实现；**Workspace** 不得改变 **Rule Semantics**
- **Portrait** 使用 **Business Rule** 中的权重与评分表生成；**Match** 在 **Portrait** 与岗位需求之上计算
- **Career Report** 应在 **Portrait** 与 **Match** 之后生成，是可保存、可回看的学生成果物
- 画像 / 匹配 / 报告 / Ask 的自然语言解读必须遵守 **Honest Career Stance**；数值规则仍由 **Business Rule** 计算，话术不得反向粉饰分数含义
- **Supervisor** 编排 **TaskPacket** 调用子 Agent；业务态写入 **Blackboard**；跨会话偏好与结论进入 **Memory Store**（只挂 Supervisor）
- 默认 Agent 运行时为 Supervisor；`mode=langgraph_legacy` / `react` 默认关闭，仅 demo 开发开关可启用，不作为主路径
- 每个 **Prompt Bundle** 由 **Prompt Base** + **Prompt Tone** 组装；**Prompt Registry** 是指引层与层的唯一入口；共享契约在 `agent.manual`
- **PromptSelector** 消费 **Prompt Registry** 的 `runtime_routes`；**Content Kind** 固定同一交互模式下不同模型调用的 Prompt 契约；workspace 仍只能覆盖 tone，不能覆盖 route 语义
- **Workspace** 只能覆盖 **Prompt Tone**，不能覆盖 **Prompt Base**
- **Workspace Minimum Boundary** 是当前真实运行承诺；**Workspace Deferred Capability** 只表示 loader/样例存在，不表示业务链路已消费
- **Workspace** 可在未来挂载 **Operational Data** 增量；当前 seeds / branding 属于 **Workspace Deferred Capability**；**Runtime Data** 永不进入 workspace
- **Frozen Schema** 与 **Config Defaults** 分目录存放；workspace 不得覆盖 **Frozen Schema**
- **Config Stack** 顺序：`schemas`（只读）→ `defaults` → `workspace`（白名单）→ `.env`（仅基础设施）
- **Runnable Demo** 以 **Golden Demo Path** 验收；未进入黄金链路的能力只作为后续硬化项，不阻塞当前开源可运行目标
- **Real Service Integration** 是 **Golden Demo Path** 的运行时验证层：先验证 dev 数据库依赖与本机后端 HTTP，再决定是否推进生产容器或完整外部服务联调
- **章节级修订** 以 **网挑技术文档第七版** 为基线；每章完成后产生一个可审核的新稿
- **文字性内容调整** 必须保留已有 **图注**、图片和表格；只有新增 **软件工程图** 且无图注时才写 **图片占位符**
- **UML顺序图插入策略** 当前采用平衡方案：第三章插入大一画像建立与大三岗位匹配两张学生阶段顺序图；第四章插入GraphRAG证据检索与AI助手多工具协同两张技术链路顺序图
- **服创问题迁移** 应转化为论文式论述，分别落到需求、安全、核心技术、测试和应用边界章节
- **适用边界** 写入第一章问题分析和第六章应用边界；**画像防抖机制** 写入画像动态更新与测试；**Agent 防环机制** 写入多智能体协同与异常处理；**RAG 可信度评分** 写入 RAG 检索增强与验证
- **字段中文化** 适用于论文正文、表格和图题；代码名只作为首次说明，不作为主要叙述语言
- **表格单元格无缩进** 是后续 DOCX 脚本的表格写入约束；替换表格文字时必须清除正文首行缩进或继承原单元格无缩进格式
- **标题格式沿用** 优先级高于新写作习惯；章节修订只能复用原文档标题段落样式，不能自建标题格式
- **网挑作品正式题目** 是后续封面、摘要、页眉、正文首次介绍和图表题名中涉及作品名称的统一依据；页眉需要同步为该正式题目或原文档页眉允许的题名缩写形式
- **合理构造测试数据** 只用于第五章测试论证和答辩前的可替换记录；定稿前若获得真实日志、截图或压测报告，应优先替换构造值
- **EgressPipeline** 生成 **PublicTurn**；JSON、SSE、session history 和 session context 必须从 PublicTurn / public context 投影派生，不得绕过出站管线返回内部 runtime result

## Example dialogue

> **Dev:** 「学校想把 D4 权重调高，要改 `matcher.py` 吗？」
> **Domain expert:** 「不用。改 `workspace/school_a/overrides/rules.yaml` 里的可调参数；repo 里的 `config/defaults/rules/matching.yaml` 仍是全校默认。公式还是四维加权，语义不变。」

> **Dev:** 「Agent 话术太生硬，改哪个 Prompt 文件？」
> **Domain expert:** 「先打开 `config/prompts/registry.yaml` 找 `agent.chat.system`；只改 workspace 里对应的 `tone.md`，不要动 repo 的 `base.md`。」

> **Dev:** 「竞赛要换一批演示岗位，要改 seed SQL 吗？」
> **Domain expert:** 「改 `workspace/demo/seeds/career_insights.yaml` 增量包；repo 里的 `backend/scripts/seed_cold_start_*.sql` 仍是默认全集。用户上传的简历走 DB，不进 workspace。」

> **Doc writer:** 「第一章已有图1-1和图1-2，还要写图片占位符吗？」
> **Domain expert:** 「不要。已有图片和图注的位置只改正文；只有新增加的软件工程图、并且当前没有图注的位置，才写占位符。」

> **Doc writer:** 「服创答辩里讲过学生画像数据安全，可以直接把 Q&A 放进网挑吗？」
> **Domain expert:** 「不直接粘贴。把它改写成论文语言：第二章写安全与隐私需求，第五章写数据删除、脱敏、恶意工具返回等测试用例，第六章写治理边界。」

> **Doc writer:** 「我重写表格内容时要不要跟正文一样首行缩进？」
> **Domain expert:** 「不要。表格单元格内文字不缩进，保持表格阅读的整齐性。」

> **Doc writer:** 「第三章标题字体不太统一，我能重新设一套标题样式吗？」
> **Domain expert:** 「不能。标题使用原文档已有标题格式和层级，只替换文字内容。」

> **Doc writer:** 「页眉和封面里的作品名要用哪个？」
> **Domain expert:** 「统一使用：职导星图——基于复杂简历解析与GraphRAG的大学生职业规划多智能体决策系统。」

## Resolved decisions

- **平台级 AI Native 范围（2026-10-09，用户纠偏）**：同时覆盖 AI 助手会话与画像、匹配、简历、图谱、报告、成长分析、推荐等功能页内的 Agent/LLM 能力；不以给每页增加聊天框代替 AI Native。两种表面应共享身份、能力调用、证据、任务状态、成果物与预算，领域确定性工作流仍是评分/持久化权威。平台级工作方案见 `docs/architecture/platform-agent-capability-architecture.md`，尚非全部实现。
- **图谱是同一主 Agent 的领域能力（2026-10-09，用户确认）**：知识图谱通过当前主 Agent 的受控工具接口提供有来源及归属的证据，不新增独立 graph subagent 或第二套会话。旧共享 `Skill`/`JobPortrait` 混有用户简历/JD，仍不得当公共图读；与旧标签隔离的审核 `PublicSkill` 读面已在本机真实 Neo4j 发布 11 节点/4 边。已确认技能可经 owner-private 个人图快照读取；新的人审 `accept + equivalent` 还可由同一 `graph_query` 读取独立 revision 的私有实体关系，`related/reject/defer` 不提升为等价。未发布或版本不符返回 unavailable，不回退旧图；岗位关联仍未恢复。详见 `docs/runbooks/public-skill-partition.md`、`docs/guides/reviewed-entity-graph-projection.md`。“只读 Cypher”不等于可跨用户读取。遵守既有 Agent 拓扑冻结与 Retrieve Not Score。
- **Agent 通用能力开源优先（2026-10-09）**：Agent 数据流、工具、文件/多模态解析、异步状态、上下文与 token 预算优先复用成熟开源实现及库，以一个主模型和薄适配层接入职业规划业务；不再并行自造通用轮子。参考实现须固定仓库/commit/模块并用运行链路验证；业务规则、许可和本项目边界仍由项目自身决定。执行硬规则见 `AGENTS.md`。
- **ContextAssembler 收束（2026-07）**: 上下文注入从 ModeRuntime / prefix / SessionManager / scoped_invoke 的分散拼装收束到 `ContextAssembler` + `ContextBudget`；`RequestScope` 只判 owner，ContextAssembler 只判字节进入哪个模型调用。Ask 使用摘要 + 最近 K=8 轮 + 当前轮；Agent Router / Experts Lead 使用最小 router_context 和 ≤800 字 memory digest；子 Agent 不继承完整历史或 LTM，长任务正文走 user 通道。
- **业务规则存放策略（2026-06）**: 采用双层 — repo `config/defaults/` 为权威默认，workspace 仅白名单覆盖可调参数，不可改规则语义。选项 C。
- **Prompt 存放策略（2026-06）**: 采用双层 — repo 放基座 + 默认语气；workspace 仅覆盖语气层。选项 C。
- **Prompt 导航策略（2026-06）**: 注册表 + 约定目录 — `registry.yaml` 为唯一索引；每个 `prompt_id` 一个文件夹，层文件固定命名为 `base.md` / `tone.md`；workspace 只允许出现可覆盖层（如 `tone.md`）。选项 C。
- **Workspace 职责（2026-06，已校准）**: 当前最小边界收缩为 rules 可调参数与 prompt tone 覆盖；seeds / branding 保留为暂缓能力，不作为下一阶段主线接入；主体词典、DDL、冻结 schema 与用户运行时数据不进 workspace。
- **Repo 配置目录（2026-06）**: 领域 + 默认值 + 契约分离 — `config/defaults/{rules,dictionaries,integrations}/` + `config/prompts/` + `config/schemas/`；约束矩阵类逻辑留 Python。选项 C。
- **配置加载优先级（2026-06，已校准）**: 严格四层 — schemas 只读 → defaults → workspace 白名单 deep merge / prompt tone 替换 → `.env` 仅密钥与连接；seeds / branding 仅保留 loader 能力，未接入真实运行面；未授权 workspace 键启动报错。详见 [ADR-0001](docs/adr/0001-config-and-workspace-layers.md)。
- **Workspace 路径（2026-06）**: repo 根 `workspace/{WORKSPACE_ID}/` 开箱默认（`WORKSPACE_ID=default`）；生产可用 `WORKSPACE_PATH` 指向外部挂载目录。选项 A+C。
- **实现阶段一（2026-06）**: 先迁规则与阈值（`config/defaults/rules/` + ConfigLoader + portrait/matcher/normalizer），再迁 Prompt registry。选项 A。**已完成（2026-06-18）**: ConfigLoader 修复空 override bug + YAML 段落包裹；portrait_generator/matcher/entity_normalizer 全部接入 ConfigLoader；Prompt Registry (`config/prompts/registry.yaml` + `agent.chat.system/{base,tone}.md`) 落地，react_agent 已消费。

- **实现阶段二（2026-06，已校准）**: dictionaries / integrations / schemas 已迁移；workspace seeds+branding 仅完成 loader 与示例，不视为运行面完成。**已完成（2026-06-18）**: `config/defaults/dictionaries/`（YAML 化 + DictionaryLoader）、`config/defaults/integrations/`（YAML 化 + IntegrationLoader）、`config/schemas/`（JSON 化 + SchemaLoader）、`WorkspaceSeedsLoader`、`WorkspaceBrandingLoader`、`config/__init__.py` 统一导出全部 loader。

- **实现阶段三（2026-06）**: Prompt registry 扩展 — 将所有散落在代码中的硬编码 prompt 全部迁移到 `config/prompts/` 注册表。**已完成（2026-06-18）**:
  - 3A: entity_extractor（5 resume + 4 jd，9 prompt）
  - 3B: tool.llm_reason.system + entity_normalizer（award + generic，3 prompt）
  - 3C: text_cyber 三段（intent / generate / rewrite，3 prompt）
  - 3D: l4_report_generator 摘要（1 prompt）
  - 合计新增 16 个 prompt，加上阶段一的 `agent.chat.system` 共 17 个 prompt 全部注册到 `config/prompts/registry.yaml`。
  - 消费方改造：`resume_prompts.py` / `jd_prompts.py` / `llm_reason.py` / `entity_normalizer.py` / `text_cyber.py` / `l4_report_generator.py` 全部改为 `load_prompt()` 调用，无残留硬编码 prompt 字符串。
  - 关键技术点：JSON 模板中的花括号全部用 `{{...}}` 转义，避免与 `str.format()` 变量插值冲突。

- **实现阶段四（2026-06）**: Prompt 收尾 — 补完 3C 漏改 + 迁移剩余硬编码 prompt。**已完成（2026-06-18）**:
  - 4A: 补完 3C 漏改的 `cypher.intent.user` + `cypher.rewrite.user` 改为 `load_prompt`（之前 3C 阶段 f-string 残留修正）
  - 4B: `tool.graph_query.system` — graph_query tool 携带动态 Neo4j schema 的 Cypher 生成 system prompt
  - 4C: `agent.langgraph.system` — LangGraph 备用 agent system prompt（含用户阶段感知）
  - 4D: `entity.skill_extractor.user` — LLM 技能抽取（硬技能+软技能+掌握程度）
  - 合计新增 3 个 prompt；总 prompt 数从 17 → 20。
  - 消费方改造：`text_cyber.py`（3 处全改完）、`graph_query.py`、`langgraph_agent.py`、`llm_skill_extractor.py` 全部消费 `load_prompt`，无残留硬编码 prompt 字符串。
  - 设计要点：`agent.langgraph.system` 单独建 prompt ID（与 `agent.chat.system` 平行），因为 langgraph 版有人设 + 阶段感知，但无 ReAct 工具调用工作流；保持简单、不抽公共部分。

- **实现阶段五（2026-06）**: scripts/ 冷启动脚本 prompt 收尾。**已完成（2026-06-18）**:
  - 5A: 迁移 `scripts/milvus/generate_vectors.py` 两个 prompt（`script.vector.skill_enrich.user`、`script.vector.jd_chunk.user`）+ `scripts/milvus/seed_skill_vectors.py` 一个 prompt（`script.vector.skill_seed_enrich.user`）
  - 合计新增 3 个 prompt；总 prompt 数 20 → 23。
  - 消费方改造：`scripts/milvus/generate_vectors.py`、`scripts/milvus/seed_skill_vectors.py` 全部消费 `load_prompt`，无残留 `SKILL_ENRICH_PROMPT` / `JD_CHUNK_PROMPT` / `ENRICH_PROMPT` 常量。
  - 命名规则：scripts 下 prompt 统一以 `script.` 前缀 + 子域（`vector`）+ 用途 + `user` 结尾，与 `entity.*` / `tool.*` / `cypher.*` / `agent.*` 风格一致。

- **实现阶段六（2026-06）**: 开源可运行硬化与效果达标。**执行中（2026-06-18）**；**黄金链路验收（2026-07-18）**:
  - 冻结 workspace 扩张，当前真实运行承诺只覆盖 rules 可调参数与 prompt tone。
  - 修正 CONTEXT / ADR / tests 中把 seeds、branding 描述为“运行面已完成”的表述，明确其只是 loader/样例能力。
  - 以 Runnable Demo / Golden Demo Path 为当前完成标准：先保证应用可启动、核心路由注册、配置/Prompt 测试稳定、前端主要页面效果可验收，不把 workspace seeds / branding 接线作为主线。
  - **验收结果（2026-07-18）**: `python -B -m pytest backend/tests` → **113 passed**；黄金路由 6 条全部注册；demo 端点（agent/pipeline/career/report/admin-kb）无外部服务可测通；前端 `npm run type-check` 通过。验收中修复：`jd_aggregator` 残留 `DIMENSION_WEIGHTS` 导入；冒烟测试补齐 `/api/report/generate`。
  - **P2 前端效果联调**仍属阶段六未闭合项：页面路由齐全、类型检查通过，但大量页面仍 Mock，与真实后端黄金链路的端到端联调未作为本次验收通过条件。

- **前端契约真库实测（2026-07-18，grill 已决）**:
  - 重型不测：L0–L4 真管线 / 向量重建 / 真 LLM；清单=前端页面契约；推进=测已接通+缺口只记文档；通过=真库；缺口范围=主路径完整+其余页状态；身份=`student_001`（登录仍 mock）。
  - 文档：`docs/specs/frontend-api-contract-matrix.md`；脚本：`backend/scripts/smoke_frontend_contracts.py`。
  - **结果**：`CPA_DEMO_MODE=false` 下冒烟见 `smoke_frontend_contracts.py`（含 `role-intro` / `messages` list+read+delete）；SKIP：`report.generate`（会触 L4）、真 pipeline.trigger、langgraph chat。
  - **关键缺口**：auth 仍 mock；能力图谱富图/课程体系图/role-intro/messages/profile PATCH/AI 专用路由已补后端（Neo4j 包 + 真 LLM）。AI 冒烟依赖 `LLM_API_KEY` 可用余额。

- **Agent Runtime 隔离（2026-07-21）**: 用户资源身份收束到服务端 `Principal` / `RequestScope`。Demo 模式固定 demo student 并忽略客户端伪造身份；strict 模式无认证适配器时用户资源 API 401；会话、Memory、文件/挂载、文档访问、语音、Profile、收藏和消息必须 owner guard；旧 `langgraph_legacy` / `react` 引擎默认关闭。详见 [ADR-0003](docs/adr/0003-request-scope-runtime-isolation.md)。

- **PromptSelector / EgressPipeline 收束（2026-07-21）**: Prompt 路由从散落 `load_prompt` 调用收束到 `PromptSelector` + registry `runtime_routes`；Ask 附件问答使用不含内部工具名的产品语义 bundle。Agent HTTP 出口从 runtime result 投影为 `PublicTurn`，JSON/SSE/history/context 共享公开 reply、parts/events、thinking、tool cards 与最小 orchestration 白名单。

- **cpa-xingtu 前端设计文档（2026-07-18）**:
  - 产品视角契约与操作级样例落在 `docs/frontend/cpa-xingtu/`（全路由入册、理想 FE 契约四态标签、fixtures/adapter 约定）。
  - **已落地**：`cpa-xingtu/public/fixtures/` 静态 JSON；`cpa-xingtu/src/api/` 下 `mockAdapter` / `httpAdapter` + 域模块；`VITE_API_MODE=mock|http`。
  - **已接线页**：登录、收藏、我的报告、AI demo chat、管理 dataset/KB、职业分析（landscape 灌气泡）、画像（4d 覆盖分数）、职途导航（http：`upload|paste` → **`nsle/resume`（LLM）** → `extract/skills(use_llm)` → `infer-role` → `portrait/student`；`explore` 仅短兴趣关键词；mock 仍本地动画）、学习中心（courses/progress）、技能自评（assessment 提交）、个人中心（4d 读）、能力图谱/角色简介（http `role-intro` + 图仍 fixture/fallback）、课程体系图（探测 API/fixture 后 fallback）、消息（http inbox）、简历编辑器（parse 智能填充）。其余 backend-gap 端点在 http 失败时回退本地/fixture。
  - 真库冒烟快照仍以 `docs/specs/frontend-api-contract-matrix.md` 为准；两者冲突时以 `docs/frontend/cpa-xingtu` + 当前后端路由为准。

- **2026-10 简历确认金路径切片（进行中，覆盖上述旧 http 职途导航链路）**：真实模式职途导航改为 `/api/profile/student/upload` 的服务端任务候选 → 逐页证据和结构字段人工确认 → `/api/profile/student/confirm` 权威回读。已确认简历可独立生成、保存和重新读取 Resume Diagnostic Report；L2 画像即使因分数证据不足仍为 `partial`，也不阻断简历报告。真实页面保留左侧进度与全屏画像，并可切换简历诊断；同一成果通过 owner/version 约束的资源 URI 供主 Agent 读取。L3/L4 岗位专项仍依赖可信岗位发布，本机岗位目录为空，未有真实匹配和岗位专项报告。实体对齐仅为 L0 新候选的可审阅提议，旧已确认简历未自动重解析；完整反馈、可扩展 RAG 索引、模型建议事实性及 Agent 生产 checkpoint/interrupt 恢复仍未验收，不能称完整循环。以当前代码与 `docs/architecture/platform-agent-capability-architecture.md` 的本轮状态表为准。

- **2026-10 L3 专用持久派发切片（进行中）**：严格模式 `POST /api/pipeline/l3/run-match` 在服务端校验已确认简历、当前 L2 画像来源和人工发布岗位后，原子提交 MySQL 任务与派发意图；Celery worker 按同一 task ID、租约与 claim token 消费，领域匹配和任务终态同事务提交。Broker/worker 故障由 Celery Beat 每 60 秒触发有界重放扫描，需同时运行独立 Beat、worker 与 Redis；手动回放仍可运维使用。既有 MySQL volume 须迁移。L0 有独立的 `/api/profile/student/upload?async=1` 持久入口与 Agent trace；L0/L1/L2/L4 的**旧通用异步启动入口**仍不可用。本机正式发布岗位 0 条，不能把合成验收当成真实用户已有匹配结果。运行与回放见 `docs/guides/pipeline-dispatch-boundary.md`、`docs/guides/resume-agent-team-trace.md`。

- **2026-10 公共内容供给切片（进行中）**：管理员 dataset/KB 上传现能私有暂存原文与摘要；单条 `ds_` 记录可经服务端文件/记录摘要绑定进入正式管理员人工预检与发布，但上传本身不建图、不公开、不自动证明官方来源，本机真实发布目录仍为空。来源已审核而评分器不支持的官方原文可独立 `source_only` 展示，始终禁止评分与匹配。公开读取只使用最多 5 分钟的后台来源核验缓存；故障隐藏这层并保留经验证的正式发布候选，标 `partial`。技能向量已从真 MySQL `skill_taxonomy` 的 99 条结构字段用本地 BGE-M3 重建，并经公共 HTTP 查询首命中 Python；管理员旧浏览器正文写入已禁，公共 skill 命中还须精确绑定当前 taxonomy 结构/摘要，污染行过滤并标部分结果。不等于岗位供给或完整语义质量。**学校评分档位的人工修正仅可使用可核验的学校官方或教育主管部门材料**，不得以学生自报、模型推测或一般网站代替；首期实现正式管理员审核/撤回→L2 引用→旧画像和 L3/L4 拒用，仅支持 985/211/双一流及精确登记官方主机；当前 live 库未迁 0005，不能称该功能已在当前用户数据上可用。普通本科和缺招生年份/省份的批次档位仍 partial。详见 `docs/specs/admin-source-staging.md`、`docs/specs/admin-dataset-publication-binding.md`、`docs/specs/source-only-job-catalog.md`、`docs/specs/school-tier-review.md`、`docs/guides/skill-vector-index.md`、`docs/guides/domain-semantic-evidence.md`。

- **2026-10 模型调用预算切片（进行中）**：复用现有 LangChain/LangGraph 模型调用边界和 `ContextWindowStatus`；最终消息与工具 schema 在 dispatch 前预检，未知/超窗拒发；同步 LLMClient/LangGraph 每次已完成的 provider 调用进入 request-local usage 账本。缺失 provider usage、失败请求的未知成本、fork 后台任务、绕过这两个客户端的直连 SDK 尚未闭合，不宣称 Agent/Experts 全链预算已验收。详见 `docs/guides/agent-call-budget-ledger.md`。

- **2026-10 Ask/Agent 共享证据边界（分阶段实施）**：Ask 不开放 `graph_query`/`career_data_read` 等通用 Agent 业务工具；已改为服务端 Principal 约束的 EvidencePack 检索→回答，复用权威资源 URI/section/版本/覆盖/分页合同。Agent/Experts 仍按现有工具权限执行，尚未把全部模式和附件长文路径收束到同一证据入口。`completed` 回合不等于证据完整或业务成果已发布；检索计划失败必须显式 `partial`，不能推断用户没有资料。决策见 [ADR-0005](docs/adr/0005-shared-evidence-and-interaction-policy.md)。

- **网挑第七版章节修订规则（2026-06）**:
  - 从 `网挑文档第七版.docx` 或用户确认过的最新章节稿继续，不从旧版或未确认中间稿继续。
  - 逐章修订、逐章审核；每次只处理当前章节，避免整篇一键重写。
  - 已有图片和图注的位置只保留并配合文字，不新增“占位”字样；只有新增软件工程图且无图注的位置才写图片占位符。
  - 当前总体页数目标为 120–130 页，优先压缩第五章测试冗余、第四章代码式实现细节和第六章重复价值论述。
  - 第一章修订原则：只做文字性内容调整，保留原有图1-1、表1-1、表1-2、图1-2；补入“适用边界”，清理“结合第六版”“与上一版相比”等过程性表述。
  - 服创问题迁移原则：安全 Q20/Q21/Q27 写入安全需求与测试；RAG 可信度评分和交叉验证写入 RAG 技术与验证；Agent 防环写入多智能体协同与异常处理；画像防抖写入画像动态更新与测试。
  - 论文正文和表格采用中文业务名，代码字段名只在首次出现时括号说明。
  - 表格单元格内文字不做首行缩进，避免把正文段落缩进带入表格。
  - 标题格式沿用原 Word 文档已有样式和层级，不自行定义标题字体、字号或编号样式。
  - 作品正式题目敲定为：职导星图——基于复杂简历解析与GraphRAG的大学生职业规划多智能体决策系统；封面、摘要、页眉和正文首次介绍需统一该题名。
  - 第五章测试数据可采用合理构造值，但应围绕真实系统能力、测试环境、功能用例、评价指标和缺陷闭环展开，避免写成无法复现的夸大结论。
  - UML顺序图不按图片文件名决定位置，而按论文论证作用决定：第三章体现大学生分阶段使用场景，第四章体现GraphRAG、多Agent和工具调用等核心系统交互。

## Flagged ambiguities

- **职业方向探索 / 尝试轮次（2026-10-22）**：探索保存本人比较和意图；每轮固定所选方向、验证问题、时间及当时资料，关联原有行动计划和练习。目录结构只作未核验线索，不是招聘或适合度。复盘不等于能力认证；继续、调整、换方向必须由学生明确提交，模型不能代选。资料或成果变化不重写旧轮次，不自动修改简历、画像或匹配。页面及接口约定见[职业分析](docs/frontend/cpa-xingtu/pages/career-analysis.md)。

- 「配置」一词有时指 `.env` 密钥、有时指业务 YAML —  resolved: 密钥仅 `.env`；业务参数走 Repo Config + Workspace。
- 「真实联调」可能被误解为生产部署或全量外部服务验收 — resolved: 当前指 **Real Service Integration**，即本机后端 + dev Docker 数据库依赖 + 真实 HTTP 验证黄金链路。
- 「占位」曾同时指已有图片位置的提示文字和新增工程图位置的待补图说明 — resolved: 已有图注的位置不写占位；只有新增软件工程图且缺图注时才写图片占位符。
- 「第七版」曾可能指多个中间稿 — resolved: 未特别说明时指根目录 `网挑文档第七版.docx`；后续从用户确认过的最新章节修订稿顺延。
