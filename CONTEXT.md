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

**Workspace Deferred Capability（工作区暂缓能力）**:
已存在 loader 或示例文件，但暂不作为下一阶段主线接入真实业务链路的 workspace 能力，如 seeds 追加和报告 branding。
_Avoid_: 为未验证的多机构、多租户或演示运营需求继续扩展 workspace 目录结构

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

**Operational Data（运营数据）**:
按部署环境变化的业务内容增量，如冷启动岗位包、技能增补、图谱补充、报告机构 branding。
_Avoid_: 用户运行时数据（简历、会话）、冻结 Schema DDL；在运行面未接线前声称 workspace operational data 已完成

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
用于验收“效果达标”的最短端到端路径，覆盖健康检查、Agent 对话入口、Pipeline 冒烟入口、Career 页面/API 路由和前端主要演示页面。
_Avoid_: 只跑 loader 单测或只看静态页面截图就宣布项目可运行

**Real Service Integration（真实服务联调）**:
在本机后端进程与 dev Docker 数据库依赖之间，用真实 HTTP 请求验证黄金链路所需 API 与数据读写。
_Avoid_: 把生产容器部署、HTTPS、备份、全量 LLM 效果或所有历史接口都纳入当前联调边界

**Workspace Path（工作区路径）**:
运行时加载 workspace 文件的根目录；默认 `workspace/{WORKSPACE_ID}/`，可由 `WORKSPACE_PATH` 指向外部挂载路径。
_Avoid_: 与 repo 内 `backend/config/` 混为同一目录

### Pipeline

**Portrait（画像）**:
学生或岗位的四维能力刻画（D1 基础 / D2 技能 / D3 素养 / D4 潜力）。
_Avoid_: 简历原文、匹配结果（后者是 **Match**）

**Match（匹配）**:
人岗对齐结果，含总分、技能覆盖率、缺失技能与是否通过硬约束。
_Avoid_: 画像本身、职业报告（后者是 **Career Report**）

**Career Report（职业报告）**:
面向学生的可保存职业规划输出，整合画像、匹配、技能差距和学习路径建议。
_Avoid_: Pipeline 任务状态、单次 Agent 回复、未保存的页面临时分析

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
系统主要适合具备自学意愿、愿意持续补充信息并根据反馈调整计划的大学生，不能泛化为适合所有学生和全部职业决策。
_Avoid_: 声称系统能替代就业指导教师或覆盖所有学生情况

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
- 每个 **Prompt Bundle** 由 **Prompt Base** + **Prompt Tone** 组装；**Prompt Registry** 是指引层与层的唯一入口
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

- **实现阶段六（2026-06）**: 开源可运行硬化与效果达标。**执行中（2026-06-18）**:
  - 冻结 workspace 扩张，当前真实运行承诺只覆盖 rules 可调参数与 prompt tone。
  - 修正 CONTEXT / ADR / tests 中把 seeds、branding 描述为“运行面已完成”的表述，明确其只是 loader/样例能力。
  - 以 Runnable Demo / Golden Demo Path 为当前完成标准：先保证应用可启动、核心路由注册、配置/Prompt 测试稳定、前端主要页面效果可验收，不把 workspace seeds / branding 接线作为主线。

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

- 「配置」一词有时指 `.env` 密钥、有时指业务 YAML —  resolved: 密钥仅 `.env`；业务参数走 Repo Config + Workspace。
- 「真实联调」可能被误解为生产部署或全量外部服务验收 — resolved: 当前指 **Real Service Integration**，即本机后端 + dev Docker 数据库依赖 + 真实 HTTP 验证黄金链路。
- 「占位」曾同时指已有图片位置的提示文字和新增工程图位置的待补图说明 — resolved: 已有图注的位置不写占位；只有新增软件工程图且缺图注时才写图片占位符。
- 「第七版」曾可能指多个中间稿 — resolved: 未特别说明时指根目录 `网挑文档第七版.docx`；后续从用户确认过的最新章节修订稿顺延。
