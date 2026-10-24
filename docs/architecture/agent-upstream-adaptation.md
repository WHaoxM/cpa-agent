# Agent 通用能力：上游复用与薄适配记录

2026-10-23 页内探索输入也复用既有 `task_evidence_projection.project_exploration_context`：同一原文不再同时以事实封装、整段quote和完整fragment重复入窗；原owner/version证据库仍用于输出核验。不同本人纠正、否定和所有原文片段不合并，未注入的画像／解读明确列为待续读，受限 `career_data_read` 仍可读同源完整正文，不能说模型已看过。继续参考下述OpenCode有界读取与Codex MCP list/read边界，不增加runner、修复循环或token额度。工具帮助视图使用现有LangChain `convert_to_openai_tool`，仅缩短本能力的模型侧description；协议名、required、类型、枚举、边界和真正执行的原ToolNode不变。原JSON示例与当前问题choice不一致的冲突亦已统一；原一次纠正有native schema时不再重复自由JSON形状示例。离线用真实失败输入核对预检及保留原文，实际模型／HTTP结果见原报告；离线估算不等于整条已完成。

2026-10-23 当前正文／下游修复沿用 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `tool/tool.ts`（严格协议后消费）、`tool/question.ts`（typed选择与用户呈现分开），以及本机 Pydantic 2.10.6、既有LLM客户端、Anchor、持久QA和预算。报告内部what_is_clear／reading_notes映射原公开字段，单项经历不被拆成几条“优势”，起点引用优先原文明示的本人任务；完整原文保留，未新增事实分类器或NLI框架。合法wire／来源问题之后的正文纠正至多一次，同一原文、同一问题、同一预检，已知用量累加，未知不计零；不另写模型客户端、增加cap或通过删句发布。

当前报告契约20另修复已复现的误拒绝：同片段明确“我编写SQL分组查询”，不能把“学生用SQL编写了分组查询”一律判为新增职责。仅在校验视图中规范化人称、空白、语法“了”及工具／动词顺序，再证明任务动词和对象是该片段的同序前缀；不规范化能力、完成状态、独立性或跨动作关系，否认／计划／失败／未完成仍拒绝。原发布text从未改变，正反向及原文保持测试均保留。并非删掉无据句子后采用剩余部分。无来源技术标识从本项精确原文计算，已失败的词同步进入原一次纠正的原生Schema NOT条件；不是新造语义裁判或无限重试。 每项解释的范围检查仅使用该项精确引用，不借全库其他经历解除限制；报名任务不能借另一项目的签到文字推成到场统计。

个人解读第4版只生成一份正文投影；初次看起点时可复用同一owner／确认版本的现行报告问题，精确匹配本回合原文片段后才给模型有限choice。公开text／reason由该来源投影，不是用模板替换一条不合格模型提问；旧版／无效／已回答时不提供选项。未有本人否认就不提供not_done，防止把未知改标签。全部已识别的生成文字错误交给原一次纠正，不再以丢掉可选解释制造partial成功。历史v2／v3问题仍是讨论上下文，不变为当前v4成果。 当前报告已明确片段与待澄清片段分别进入bank：前者限定起点选择，后者只提供原问题；实际出现的同一活动判重范围推断不能通过。

生成约束兼容性按实际API验证：负向lookahead正则请求被当前服务400拒绝，未采用；正向pattern专项也不能作为语义通过。随后仅改变已保存失败请求的Schema NOT条件做一次差分，实际provider接受并产生无示例数字的候选，仍单独人工审阅语言。采用[当前厂商结构化输出文档](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output)列明的原生not／pattern组合，只作用于生成解释文字，不作用于原文、ID或数值来源；仍走本项目validator与真实内容评审，不把Schema当语义裁判。没有引入自写正则编译器或解码服务。当前内部reader_gain只引导学生／听者的具体所得，公开兼容reason。原始失败、实际请求、最终回放与限制归[原页面修复报告](../reports/2026-10-22-real-page-experience-repair.md#正文职责分离与下游纠正2026-10-23)。

同轮助手续读另有已复现断点：精确简历解读引用把可选范围锁在报告，且D3资源只读aggregate evidence，漏掉实际存于trait.evidence的本人补充。沿用上述MCP list/read薄适配，先分别由原owner-scoped reader核对报告确认版本与当前画像source_resume_version，再允许模型选择同源D3；分析型追问同时读取原报告analysis，纯章节计数不强加这一步。D3仅投影当前revision、明确user_self_report的文字和provenance，不从分数反推经历，不改源资料。原分页／预算／版本检查照常，读取失败或来源不一致仍显式不可用。

原Ask证据卡把“计划实际执行须有practice_record”写成所有经历都须有记录，导致本人补充也被排除；现将该限制明确限于计划执行，其他来源按原文或自述讨论。简历阅读中已出现的能力拔高／假设例子复用既有`_ask_answer_or_repair`的**同一个一次纠正额度**，只在实际pack含简历analysis时启用已知错误校验与原生NOT；不改其他Ask／附件路径、不新增语义裁判或重试循环。正文不做删词替换，第二稿无效不冒充完成。实际回放与首次失败仍归同一报告，不能据窄规则宣称所有自然语言已证明。

随后冻结实证再次证明：provider接受JSON Schema请求，不代表自由正文一定遵循NOT；实际第二稿仍写例子，被本地校验拒绝，不能宣传“原生约束已经保证内容”。因此对“回读原解读／实际已读补充”不再让模型另写一份。直接复用既有`section_count_answer`的**语义选择→权威读取→有限投影**模式：同一个选择调用可返回内部source_readback，只有实际pack含现行owner-bound报告analysis才由原资料投影；新增判断／技术解答／新建议仍用analysis。文字来自本轮实际读取的原模型解读和自述，不是固定成功报告或模板事实。旧版、混合报告版本不进入该投影；不完整读取明确partial，附件路径不短路。原reply／evidence_summary／历史保存通路不变，无新HTTP入口或持久框架。此为避免已持久化内容被无必要地重新推断，不是为通过而用常量替换失败模型答案。

续聊实证又收窄两处遗漏：带section的引用仍须核验同源关系；没有重复携带引用的新一轮，选择了报告其他章节也必须补读同一报告analysis。现在选定source_readback后统一通过原reader绑定确认版本与当前画像，缺少必需资料明确未完成，不能跌回自由建议。资料选择不只看目录标题，当前报告preview从实际已验证解读提取；planner看见已有内容后再区分回读与新推断，pack仍重新按owner／版本读取。当前显示名通过原消息部件白名单，HTTP／历史不再降回“业务资料”。原已读完整内容和其他经历reading_notes均保留，未增加模型调用或新消息类型。

以下v9及更早段落是当时的适配／失败记录；现行行为以上节及页面owner为准。

2026-10-23 原简历解读内容收尾：继续复用本机Pydantic 2.10.6／既有LLM客户端和精确Anchor，复查同一OpenCode commit `388406238bd5ca15564a762840a2362c3a45bd9c` 的`tool/question.ts`（typed输入与用户问题呈现分开）及`tool/tool.ts`（校验后消费）。不移植其交互内核：报告域从实际原文和原有保守资格约束准备有限问题候选，用Pydantic `create_model`／`Literal`把本次choice写入实际Schema；模型不任意组合已知片段与分工问题。已有明确职责不重问，同片段其他动作不随之扩张；用途不当成新任务，有项目时不逐工具考问。模型选择尚未明确的一处，服务端生成开放提问，不是在模型输出技术考题后删句替换。经历解读与建议消费同一草稿，建议服务变纯校验／投影，去掉一次重复模型调用；不新造Agent、通用问题DSL或NLI裁判。summary／strengths仍由当次真实模型生成，原文另存`source_quotes`，既有全文、24k输入门禁及客户端整体上下文预检保留。v9是现行新生成契约：自由正文失败保持`analysis=null`／partial，但不丢掉独立核验的来源问题；既有旧GET不改正文或触发生成。各中间版本真实失败、逐句评审与完整回访均归原修复报告，有限选项不等于普遍模型质量通过。

同轮从真实`79b02a05f71b`复现：学生已说清职责／规则且询问下一步用途，原一次纠错仍输出`clarify_experience`。继续复用已有`answered_duplicate_check_question`、`positioning_output_schema()`及既有`model_json`／账本，只在该保守识别为真的原一次纠错请求里投影同一Schema，`next_action.kind`限定practice、question／proposal／review为null；动作文字与理由仍由模型从原bank生成，不设置默认答案。相反情况不提供该格式，原18k资料门禁、30k账本、所有引用／内容校验及一次上限不变，不自动再开回合或用固定结果。此为把已有业务校验同步到生成端，不是新阶段决策或放宽事实。实际专项虽Schema通过，仍有无据资源用途／跨活动判重例子，内容失败，原根因未因此闭合；不能宣称类型约束解决了语义正确性。

2026-10-23 原简历解读职责收紧：核对本项目 `career_positioning._Anchor` 的逐片段原文协议和 `check_plain_interpretation`，以及 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` `packages/opencode/src/tool/tool.ts` 的严格schema／先校验后消费。直接复用已安装Pydantic 2.10.6、现有Anchor类型和解释边界；报告域的窄解析适配仅将片段绑定到原有summary／strengths／questions。不能直接调用整个攀岩定位校验器，因为它还要求方向和优先行动，伪造这些字段反而破坏报告边界。没有复制上游执行内核、新建NLI判断器、模型客户端、预算／压缩框架或重试循环。

服务端引用原文，模型不改写个人事实；仍需人工逐句评审解释是否真正得到支持，引用合法不等于语义已证明。输入保留完整事实，超出原24000字符门禁显式未生成，不提高上限、不截断。旧报告GET仅追加legacy呈现标记，不改持久正文或触发模型。实际候选、原始失败和回访归原页面修复报告。

同轮表达建议沿用Pydantic `create_model`，为服务端事实ID各生成一个必需、可空的建议位置，替代允许同ID多条的数组；JSON重复键显式拒绝而非采用最后一条。此为有限报告事实的结构约束，不是新的通用工具协议。理由继续调用现有 `check_plain_interpretation`；失败不再替换成固定“可读性”理由。具体已证实的缺口伪造、能力评价、无据字段举例及独立完成考核另由报告域窄规则拒绝，仍不声称规则证明了所有自然语言语义。真实模型只走原一次建议调用，失败保留partial，不加重试或固定建议。

2026-10-23 职责澄清输出修复：复查现有 `career_positioning._AnchoredPositioning`、`validate_analysis` 和 `scoped_invoke`，核对 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` `packages/opencode/src/tool/tool.ts` 的schema及字段错误反馈、`session/message-v2.ts` 的结构化消息来源。直接复用本机Pydantic **2.10.6** `model_json_schema()` 提取同一实际数量限制，首轮／原有至多一次纠错共用；`ValidationError.errors(include_input=False)` 保留完整字段路径，不回显整份无效候选。首次完整Schema冗余触发原30k预算后，只保留不重复的约束，合并重复协议说明；完整捕获输入预检保留，未提高预算、截掉事实或增设压缩框架。来源、工具权限、持久状态机及2项起点引用上限不变。

同轮内容复核再确认“核对”被无据写成人工核对，新增拒绝实现方式断言的窄回归，仍允许提问／建议及服务端逐字引用真实人工经历，不关键词替换正文。修复原历史投影：从已保存v2 `next_action` 恢复实际追问及理由，仍只写入既有“此前讨论、非事实”字段；学生原话才进入事实bank，不新增记忆库。个人起点产出只在 `next_action.text` 提问，外层question保持null，与原公开投影一致；不放宽事实检查。最新学生职责／判定规则已在原始失败bank，不把模型未利用信息归咎于学生没填。此为现有领域协议薄适配，不移植上游内核；原失败、实际模型和页面回放统一归原页面验证报告。

同一已保存失败还证明只恢复上下文仍不够：模型收到已答分工及判定规则后继续重问。沿用领域validator增加一个保守的已回答项门禁，仅识别本次原话中共同归属查询／核对的编写、明确规则、尚未讲解统计结论及询问用途的组合，拒绝再以实现方式问卷作为优先动作；不返回预写练习、不提升为通用能力判定。否认编写、仅查询归本人、规则未知或已经讲解的反向测试保持不触发。首版条件误吞“没有写过”的红回归亦保留，收紧为明确肯定短语后再回放；不为通过而扩大职责或生成另一份事实来源。

再以真实候选复现“张三用两个手机号报名”的虚构例子，补入个人解释的具体经历示例拒绝项，合法的操作顺序示例仍可用。下一步文字、理由及已回答项的已知错误一并反馈给原来的一次纠错，不能只修数字后遗漏同一候选的重复追问；仍无效即失败，不增设第二次纠错或替换正文。

2026-10-23 未发送附件恢复：再次核对 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `session/message-v2.ts`（持久 typed parts 与资源引用）和 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 `tools/handlers/mcp_resource/read_mcp_resource.rs`（服务端校验资源范围）。不复制其会话内核：沿用本项目 conversation JSON/CAS、handoff、Files 状态事件及 run admission 事务，仅增加 `composer_files` 待发送选择。上传成功回执须在文件引用交接提交后返回；分析入场才核验 owner/原文件版本/ready 并原子消费选择。刷新只恢复明确交接的引用，不枚举账号最新文件、不把文件正文或权限存入浏览器缓存。这里恢复的是任务资料选择，不是通用上下文压缩；证据归入[原统一任务报告](../reports/2026-10-22-unified-task-orchestration-replay.md#未发送附件恢复2026-10-23)。

2026-10-23 重登起点恢复的连接状态修复：先核对现有 `AgentRunRepository._set_utc`、MySQLClient QueuePool，以及本机 SQLAlchemy **2.0.25** 的 `engine/base.py`（`Engine.begin` / `Connection.begin`）和 `pool/base.py`（默认 reset-on-return 只 rollback）。同时检查 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/storage/storage.ts`；其文件存储不提供 MySQL session-setting 生命周期，不能直接套用。沿用已安装 SQLAlchemy 的连接与事务上下文，在既有 MySQL client 中增加窄 `utc_transaction` 工具函数，由 run repository、派发／租约及对话幂等共同复用：记录借入连接原时区，业务提交或回滚后恢复，恢复失败则 invalidate 而不归还未知连接。不重写连接池、任务状态机、时钟或探索排序，不复制上游内核；SQLite 分支仍复用原事务。真实 MySQL 单连接回归已复现后建探索早 8 小时、列表错选旧记录，亦分别证实派发、租约读取、Principal绑定读取和对话准入的同类泄漏；完整结果、浏览器证据及边界见原页面验证报告的本轮补记。

2026-10-23 统一任务阻断修复：沿用已核对的 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` `tool/tool.ts` 的先校验后消费、`session/compaction.ts` 的减少旧工具输出，以及 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的有界上下文／预算边界。`task_evidence_projection.py` 只作为现有统一任务的模型输入投影：本人原文、片段 ID、学生纠正和实际练习／附件状态保留；同一事实不重复携带原始封装与引用目录，旧助手回复不作为事实。投影在既有领域输入门禁之前执行，原 owner/version-bound 证据仍在服务端参与同一输出校验；未知输入键拒绝，不静默丢资料。复盘阶段复用同一 QA 工具循环，但只要求现有计划的复盘协议，不再重做个人定位；未注入的简历、目录和解释只保留资料目录，需要时实际读取，不能把系统未读写成学生能力缺口。

结构修复复用原 `model_json`、同一 validator 和同一账本，最多一次；复盘只有个别字段无效时，只整理这些字段并保留有效内容。不合法的 owner、引用或版本不能通过重试放宽。修复仍无效直接失败，不触发第二轮纠错、提高 cap 或另建任务；用量不可核对时不自动重试。已有 `session_compaction.py` 未接入本轮：其候选硬截断缺少本业务所需的纠正／权威引用保留契约，不能当作成熟的长期记忆。没有移植上游内核、增加模型客户端或通用压缩器；不是无限上下文，也没有宣称实现 DeepSeek Harness 的通用恢复机制。实际请求、原始失败、最终账本和限制见[同一统一任务报告补记](../reports/2026-10-22-unified-task-orchestration-replay.md#统一任务阻断修复与后半段回放2026-10-23)。

简历学历来源校验扩展既有 `_ground_semantic_facts` 原文边界；候选字段用已定义学生字段投影；上传材料答复在正式确认前拒绝已复现的“已核验／已确认经历”升级措辞，并保留提交来源。该窄校验不是通用事实证明或模型质量评分，不能将通过用例扩写为任意自述均已可靠核实。

2026-10-22 职业攀岩个人起点解读：先核对本机 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/tool/tool.ts`（Schema 解码、`InvalidArgumentsError` 的结构反馈）与本项目 `career_exploration_analysis.py`、`scoped_invoke.py` 既有持久 QA。继续复用现有 runner/outbox、来源绑定、18,000 字符输入边界和一次有界结构整理，不复制上游内核、不新增 Agent 或预算框架。新增的 `career_positioning.py` 仅是业务投影：Pydantic 严格检查结构，引用只能选取实际读取的本人经历／自述／方向目录；“尚未做过”须引用本人表述，引用合法不证明语义正确。同方向比较条目只合并形状，保留全部不同文本与原有长度边界，不增加可比较方向；结构整理接收具体校验错误，不用多轮重试碰运气。真实输入、工具读取、模型候选、失败及最终结果从[原职业探索回放的攀岩补记](../reports/2026-10-22-career-exploration-practice-replay.md#职途导航接回职业攀岩路径)接续。

结构整理的纠错输入仅保留实际读取的原始资料、协议及具体失败原因，不把上一份无据候选重新当成事实喂回。业务语义校验仍不是通用事实证明器：针对已保存反例识别的无据实现判断投影为 `needs_clarification`，保留原始经历与有依据的下一步，正文复用同份投影；无法绑定引用、owner 或来源版本仍拒绝。公开自述引用只展示本人文字，不输出内部任务／修订字段。真实模型对照仍存在措辞过重、跨动作扩大职责等未解决项，见同一回放，不宣称靠关键词规则完成普遍语义验收。

## 硬规则与边界

2026-10-23 个人解读职责收紧：继续参考上节已核对的 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` `tool/tool.ts` 的先解码／校验后消费边界，并复用本项目 Pydantic、现有资料provider、QA/outbox、18,000字符预算及最多一次结构纠错。仅在职业业务投影中增加逐项原文片段校验、原文事实与方向解释分离、内容版本标记，不移植上游执行拓扑、不新增语义验证Agent。精确片段匹配证明引用未篡改，不能证明任意推论；范围与实用性仍由保存反例及真实模型逐句评审验收，不把关键词检查作为完整事实证明器。

真实请求发现让模型重抄完整片段会带回标点、跨片段文字及无关角色头，导致明明已读却无法发布；改为模型只选择本项已给出的 `fragment_id`，服务端回填同项原文。没有为了通过放宽跨记录匹配或提高重试次数。主输出JSON示例与附加约定必须一致；第2版将 `positioning` 纳入主示例，防止只按旧五字段输出绕过依据。每项片段不证明解释语义正确，业务内容门禁和人工逐句评审仍保留。

2026-10-22 统一任务适配：复核 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`（MIT）的 `tool/task.ts`、`tool/read.ts`、`session/overflow.ts`、`session/compaction.ts`、`session/message-v2.ts`，以及 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`（Apache-2.0）的 `compact_token_budget.rs` 与嵌套资源读取 handler。借鉴任务隔离、明确读取位置、保留结构化部件及调用前预算契约；没有复制上游内核或新增依赖。现有 LangGraph / Celery / MySQL run journal 已满足执行与恢复需求，因此只在 `student_task.py` 增加三项业务委派，在现有 `conversation` 增加版本化上下文。主规划与业务执行在同一个 run step、同一个用量账本中串行运行，委派深度固定为一，不重置额度。

完整附件已进入旧 QA 提示时不再同时开放重复全文读取；部分附件仅保留 `attachment_read`。统一任务简历读取复用 `document_access.read_window`，每轮总量约 4,000 字符，保存文本版本与下一位置；600/1,800 字符摘要槽不是全文上限。讨论仅携带有界最近摘要，旧工具正文不反复入窗；这不是完整的语义压缩或无限历史记忆。实测、失败和未验收项见[任务编排回放](../reports/2026-10-22-unified-task-orchestration-replay.md)。

2026-10-22 原页内职业探索陪伴：实施前复核本项目持久 QA 执行器、run/outbox/event 事务与 `career_data_read`，以及 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `tool/read.ts`、`session/message-v2.ts`，Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 `tools/handlers/mcp_resource/read_mcp_resource.rs`。薄适配只增加探索回合与只读能力绑定，继续使用既有 LangGraph ToolNode、预算、模型路由、资料/附件 provider 和 MySQL/Celery 恢复机制，不新增 runner/客户端/通用 DSL。资料读取事件在实际读取前后写入既有 run journal；LangGraph 同步工具线程显式传递已绑定的读取范围，不能借模型参数扩成其他资料。生成结果只是候选，业务确认继续由原计划事务完成；真实回放与未验收项由原探索报告承载。

先遵守仓库根目录 `AGENTS.md` 的“Agent 基础能力开源优先”规则。通用数据流、文件读取、上下文窗口、工具结果与异步状态先比对成熟实现，再决定复用库、协议或状态机。职业规划的证据判断、问题澄清与推荐策略才是本项目需要保留的业务层；不得把编码 Agent 的整套执行拓扑直接搬进 Flask 后端。

此表是本轮**可核对的参考记录**，不是宣称 OpenCode 或 Codex 已成为运行时依赖。上游代码目前均未直接复制到项目源文件。
表中的本机参考快照不随 CPA-Agent 仓库分发；在其他环境可按记录的上游 commit 获取对应源码。

2026-10-21 首次简历旅程的年级来源修正：先检查现有 `resume_structure.py` 的 Pydantic schema 与 `_ground_semantic_facts` 原文引用校验，以及 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/tool/tool.ts`（解码校验先于执行）。继续复用现有抽取与 map/reduce，不移植 Effect/另建解析器。通用 schema 只能核对类型，不能证明“大二”出现于这份简历；该业务约束由现有 grounding 边界薄适配：未在原文明确出现的年级不进入候选事实，并保留可见来源警告。不按入学日期推算、不替用户更正年级；不声称校验了所有简历字段或真实模型整体质量。单次与合并路径用失败回归及真实 PDF→HTTP→worker→页面验证。

MinerU 3.0.7 的异步 `/tasks` 与 AGPLv3 声明见[该版本发布页](https://pypi.org/project/mineru/3.0.7/)；当前最新版许可证不同，不能拿最新仓库首页反推本机旧版本的许可。

| 上游快照 / 许可证 | 参考模块 | 本项目适配与验收 |
| --- | --- | --- |
| OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`，MIT；本地 `opencode/` | `packages/opencode/src/tool/read.ts`：`offset`、截断标记；`src/tool/truncate.ts` | `backend/app/services/agent_runtime/media/document_access.py` 的 `read_window`、`complete`/`has_more` 与 `attachment_read`，旧文本不再被头部窗口冒充全文。测试第 41 页、80,000 字以后及多附件分页。 |
| 同一 OpenCode 快照 | `packages/opencode/src/session/overflow.ts`、`session/compaction.ts`：按模型窗口、输出保留额与实际 usage 判断溢出 | `backend/app/services/agent_runtime/budget.py` 的按 provider/model 能力判定，`llm_client.py` 的每次 failover 重算及 usage 回传。仅文字请求有保守估算；不能称为与上游完全同等的自动压缩。 |
| Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`，Apache-2.0；本地 `D:/AGeneral Workspace/_agent_reference/codex/` | `codex-rs/core/src/tools/handlers/get_context_remaining.rs`、`codex-rs/core/src/compact.rs` | 借鉴“剩余窗口是显式状态、截断必须标明不完整”；不移植 Rust 会话内核。当前附件报告暴露模型窗口判定，未知模型不伪称全文已内联。 |
| OpenCode 同一快照 | `packages/opencode/src/skill/index.ts`：显式 frontmatter/skill 加载服务；`src/skill/discovery.ts`：Skill 发现与受限支持文件 | 仅参照显式 Skill 选择、版本与支持文件边界；CPA-Agent v2 用 `skill_registry.load_skill/load_skill_reference` 的固定 allowlist，计划发布时 pin 实际 Skill/reference 字节 hash，步骤前后重算 release；只允许 `terms` 与 `evidence-scoping` 两个固定 reference，不采用动态全局发现或任意路径读取。内部结构化招聘条件属于模型调用协议，不写进 Skill 正文。 |
| Codex 同一快照 | `codex-rs/core/src/skills.rs`：Skill 的显式/隐式调用与使用记录 | 只借鉴来源/调用状态可追溯的思路，不移植 Rust 会话状态机。v2 将受 Skill 指导的教育条件分析放在同一步专用 JSON Object/Pydantic 子步骤里；引用/条件状态由服务端校验来源、Owner 与 release pin，Skill/reference 只教推理方法，不提供招聘事实。 |
| OpenCode 同一快照 | `packages/opencode/src/skill/index.ts`、`packages/opencode/src/skill/discovery.ts`；Codex 同一快照 `codex-rs/core/src/skills.rs` | v4 继续薄复用本项目固定 Skill/reference allowlist 和既有 durable QA step/usage ledger，不引入第二套 runner。新增 ID `education-screening-structured-v4` 单独 pin Skill/reference 内容、结构化 QA/finalizer/gate contract、模型 route 与结论策略；服务端投影生成带来源、范围和状态的 `allowed_conclusions`，自由模型文本只能在 conclusion ID 与证据 gate 约束下表达。结论说明 hash 仅固定声明文本；另以 v4 模块和其直接 helper closure（v1/v2/v3 education screening、`quality_gate`、`reply_egress`、`skill_registry`）部署源 SHA-256 与显式实现版本 pin 投影行为，必要源不可用或 hash 漂移即 fail closed；此不是整个应用/Provider 的签名 build attestation。v4 no-model 契约测试通过；另有一次合成 A-only Qwen 回放，但模型写入“岗位明确要求”及无原文的例外表达，事实范围验收未通过；HTTP 未验收。无明确岗位/简历 scope 的只读回执不升为资格依据。 |
| OpenCode 同一快照 | `packages/opencode/src/skill/index.ts`、`packages/opencode/src/skill/discovery.ts`；Codex 同一快照 `codex-rs/core/src/skills.rs` | v5 新增独立 opt-in `education-screening-structured-v5`，继续复用 v4 typed extractor/source validator 与 durable step/ledger，但不运行通用 QA 自由文本，也不调用会生成自然答复的 v4 finalizer。最终 selector 只能返回 1–3 个服务端 conclusion IDs、受限 action ID 和 style；Pydantic 严格拒绝多余字段，服务器端依据 status/action 用固定来源限定词渲染 2–4 个自然短点。v5 pin 完整嵌套 v4 release/helper closure、自身 selector/schema/prompt/renderer，以及预算、LLM client 与 router helper source；`skill_registry` 不改，v5 仅以受限 v4 registry scope alias 复用同一 Skills，旧 v1–v4 ID/release 不热切。无模型 A–E、注入、非法选择、漂移及兼容测试通过；真实模型与 HTTP 尚未验收。 |
| OpenCode 同一快照 | `packages/opencode/src/tool/task.ts`、`src/util/queue.ts`：task 作为 session 工作单元与进程内队列边界 | 本次 Agent dispatcher 不复用这类进程内队列作为持久状态；按项目既有 `backend/app/services/pipeline_match_jobs.py` 薄复用 MySQL intent、claim token/lease、稳定 task id 与 Beat replay，再通过专用 `cpa.agent` Celery 队列只运输 `run_id`。publisher/worker token 与 run/event/outbox 状态仍以 MySQL 为准，Celery result backend 不启用。适配边界：执行器目前只开放服务端校验的 QA-only 持久 run；一次隔离真实模型链路成功，但该次没有触发 Skill/工具，也未验证页面和写角色。旧 `waiting_executor` 只属于迁移前 transport worker 状态。 |
| Mammoth 1.12.0，BSD-2-Clause；本机 `mkg` 环境 | DOCX `extract_raw_text`，含 `w:txbxContent`/`v:textbox` 内容 | `backend/app/utils/docx_text.py` 读取正文/表格/文本框，再用 `python-docx` 补页眉页脚；合成文本框回归测试。复杂形状、图片字与真实 DOCX 样本仍待校对。 |
| MinerU 3.0.7（本机安装包声明 AGPL-3.0；不从网络自动下载模型） | 现成 PDF OCR/版面 `content_list.json`；该版本上游另提供 `/tasks` 异步接口 | `backend/scripts/parse_sidecar_mineru.py` 暂作薄 HTTP 适配；保留页码块、显式健康能力与隔离临时文件。两页扫描简历实测 68 个文本块；这不等于 OCR 字符准确率。部署前应按实际采用的 MinerU 版本核对许可证和模型许可。 |

## 为什么不整套嵌入上游

OpenCode 核心为 TypeScript，Codex 核心为 Rust；本项目当前权威服务是 Python/Flask/LangGraph，直接嵌入二者完整会话执行内核会引入跨进程协议、权限模型及部署复杂度。本轮选择复用已验证的 DOCX/PDF 解析库，同时对照上游的**可测契约**改造现有 Python 边界。若后续要采用其完整运行时，需先以独立进程协议和实际端到端测试证明收益，不能只因为名字成熟而平移代码。

## 当前尚未达到上游能力的部分

2026-10-24 工具结果与异步审计派发的状态分离：核对 Codex 本地快照 `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 `codex-rs/core/src/tools/tool_dispatch_trace.rs`（执行完成/失败事件显式配对）及 `executed_tool_calls.rs`（观察结果不负责执行工具）。CPA 保留 LangGraph `ToolNode` 与现有 `gated_tools._wrap_one`，只修正适配边界：业务工具已经返回后，异步 `tool_audit` 入队异常不能反向变成工具失败；卡片另标 `audit_dispatch=enqueue_failed`，且不把队列接受称为数据库审计持久化。未移植 Codex Rust dispatch/runtime，也未改变 owner/session 权限或业务工具结果合同。离线链路回归见 `test_gated_tool_audit_failure.py`。

2026-10-23 持续上下文修正复查同一 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `session/message-v2.ts`、`session/compaction.ts`，及同一 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 `codex-rs/core/src/compact.rs`。本次只薄适配其来源分层和可恢复边界：既有不可变本人输入变成明确 user 消息，原附件不重复注入讨论；现有 conversation JSON 的文档位置与最近答复分离，成功事务才推进。TaskPacket 的 server-bound 简历讨论字段不能由公开 run 请求伪造；模型调用、预算、权限和 Celery 仍使用原实现。实际委派前后核对所选探索版本，未选择的定位代码不再使纯简历读取作废，根任务的共享协议版本仍受校验。没有移植 TypeScript/Rust 内核，没有接通未经验收的 `session_compaction.py`，没有把原话回放或更大的窗口称为压缩。自动保留纠正的长期摘要、历史超限后原任务恢复和累计跨窗综合仍是未完成边界；实验模型语义审核出现过度拦截，已从最终运行通路撤掉，只保留具体交付边界的一次有界整理。证据归[现有统一任务报告](../reports/2026-10-22-unified-task-orchestration-replay.md)。

2026-10-22 职业方向探索沿用既有资源与计划能力。实现前复查本机 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/tool/read.ts`、`session/message-v2.ts`（有界读取、typed parts），以及工作区外 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 MCP resource list/read handlers。适配只新增 owner-scoped exploration URI provider、历史轮次精确关联和现有 EvidencePack 输入；直接复用 `personal_resources` 的附件读取、`mode_runtime` 的依据部件、LLMClient 预算与行动计划事务。不复制或另造 Agent 引擎、模型客户端、分块、队列、权限或通用流程 DSL。真实模型中曾出现把计划要求误当执行记录的偏差，已将任务背景与学生提交分开，实际记录由服务端原文投影；这不是模型事实质量普遍保证。实际请求与读取证据见[业务回放](../reports/2026-10-22-career-exploration-practice-replay.md)。

2026-10-22 简历制作的智能填充只新增业务预览适配：先检查现有 `resume_structure.extract_canonical_profile` 的 Pydantic 类型、原文年级约束、map/reduce、partial 元数据和 `_structure_call` usage，以及本机 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/tool/tool.ts` 解码校验边界。继续调用现有 LLMClient 与 canonical extractor，`link_entities=False`；不复制工具运行时、不新造分块/预算/异步队列。预览不触碰正式档案，错误/partial 显示给学生。富文本输出复用前端已有 `@tiptap/core` 3.21.0 的 `generateJSON/generateHTML` 与同一 StarterKit/Underline schema，避免直接信任持久或模型 HTML；未新增第三方包。具体真实链路证据由[制作页契约](../frontend/cpa-xingtu/pages/resume-builder.md)链接的旅程报告维护，不在这里重复通过数字。

- 摘要/Reflection 的 `fork_jobs` 仍是进程内后台线程。媒体 ingest/repair 已单独迁到本机已有 Celery 5.3.6 + Redis：`cpa.media` 队列使用 late ack、worker-lost redelivery、单预取和确定性 task id，磁盘/MySQL `agent_file` 仍是权威状态。安装态 MinerU 3.0.7 的 `AsyncTaskManager` 在服务进程内保存 `/tasks` 状态，服务重启后不可作为恢复来源，因此只把 MinerU 当解析引擎，不把其任务表当持久队列。见 `docs/guides/durable-media-jobs.md`。
- Agent run outbox/claim 使用同一运行时的 Celery 5.3.6 + Redis，但与业务任务隔离到 `cpa.agent` 队列；MySQL 保存 publish/claim lease、尝试次数、重放时点与连续事件，Beat 只从 MySQL 查询待处理 run。当前 worker 已接 QA-only planner/executor 的窄纵切，隔离真实模型 run 的 completed 状态由 MySQL 事件与最终回复佐证；`published`/`claimed` 本身仍不是业务“执行中”或完成证据。新执行器直接 fail closed 使用 PyMySQLSaver，不复用 `get_checkpointer()` 的 MemorySaver fallback；checkpoint 与 MySQL owner-bound run 分开管理。写角色、运行中取消/重启、skill/tool实读与页面仍未验收。
- `48_000` 字符只是附件分配预算，不等于最终请求的精确 token。模型能力已按候选模型检查，但非 OpenAI tokenizer 目前是保守 UTF-8 字节估计，真实 provider `usage` 才是权威值。
- 未知模型窗口、非文本多模态消息、复杂 DOCX 结构不能被误标为“已完整送入模型”。
- 提示词链路有 mock 契约测试，不代表真实主模型职业规划判断质量已经通过人工评测。
- `education-screening-structured@0.1.0` 当前只是未进入用户目录/默认流的 opt-in 实验；显式 capability ID 已注册、HTTP 调用方代码上可选择。一次 direct Qwen/Qwen-Plus 合成 A–E step probe 完成且 usage 均可观察，但答案/分支解析未达到体验门槛；真实应用 HTTP/MySQL/Celery/owner/D1 回放尚未验收。Lead 规划调用也不在该 QA step token ledger 内。计划 release pin 与模型 gate 都不等于真实模型回答正确。
- `education-screening-structured-v4@0.1.0` 是新增、独立的显式 opt-in capability；它不替换 v1/v2/v3，也不在默认流中，但持有 exact capability ID 的调用方可以选择。v4 no-model 合同/投影回归通过，并做过一次合成 A-only Qwen 回复；该回复有来源权威与“例外”范围越界，因此 v4 的公开自然回复未达到事实边界门槛。真实 JD receipt、HTTP/MySQL/Celery owner/D1 或默认目录体验仍未验收；不能称为已发布可用能力。
- `education-screening-structured-v5@0.1.0` 是服务端措辞的独立显式 opt-in capability；注册后持有 exact ID 的调用方可请求，但未进入默认流/目录，也未完成真实模型质量或应用 HTTP/owner/D1 验收。v5 no-model renderer/selector 测试不是教育筛选事实能力证明；仅允许结论与行动 ID 决定公开答复文字，QA candidate 仍不等于公告或简历已核验。
