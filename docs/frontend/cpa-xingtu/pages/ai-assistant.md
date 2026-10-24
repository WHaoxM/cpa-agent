# AI 助手

## 任务中的纠正与续读（2026-10-23）

同一任务的附件仍在，不代表分析模型已经收到此前纠正。简历分支现在从本人、同一会话的不可变 run 输入读取学生原话（包括失败回合中的本人补充），按时间顺序作为真实 user 消息送入现有模型调用；不重放旧助手断言，不把自述升级为已确认经历，不把文件正文重复当作聊天历史。旧稿与较新的明确纠正冲突时，新纠正优先；本次问题只携带一次。跨页和重新登录仍读取同一个服务端任务，不从另一个账号或“最新简历”拼接事实。

答复发布前只检查可明确识别的交付／信任边界：空答复、将学生提交升级为已核验、终态仍只承诺稍后读取。至多一次整理与再次核对，全部沿用同一 run 账本；模型预算或用量中断不进入自动整理。实验中的泛化模型语义审核会误判“问题尚未解决”等合法表述，已撤掉，不作为硬门禁或事实核验器。最新问题、历史补充和本次只读资料分开传递；较新的问题可以改变篇幅，旧提问不再次执行。内容质量仍需真实案例核对。

文档读取位置独立保存在现有任务上下文，绑定文件文本版本，仅随成功成果事务提交；其他阶段答复和失败回合不覆盖它。主 Agent 用结构化 `document_action` 区分讨论、接着读、明确从头重读；普通追问不推进位置，读到末尾不自动绕回第一页。尾段回读仍标为部分依据，不把本次读到末尾说成模型已在同一次请求中审阅全文。原侧栏、正文、资料入口和页面设计不变。

**尚不是长期自动压缩**：目前是保留原话的有界接续，超过 256 条历史记录会明确暂停，不静默截掉最早纠正；这不是模型窗口或 30,000 tokens 调用额度耗尽。已撤掉实验审核带来的 16,000 字历史限制，真实请求仍受现有模型窗口与整轮用量预检。该保护边界尚无自动压缩／超限后原任务恢复能力，不能宣称达到 Codex/OpenCode 的持续压缩体验。文件分窗续读与真实模型调用额度不足须分别验收。执行证据与失败保留在[原统一任务回放](../../../reports/2026-10-22-unified-task-orchestration-replay.md)。

## 未发送附件恢复（2026-10-23）

统一任务开关启用时，沿用原附件外观、上传转圈、工作台及侧栏，不另做一套真实模式页面。

- 上传携带 `session_id` 与 `task_context_version`；服务端验证会话/owner/版本，文件成功入库后调用现有 handoff，把引用保存为当前任务 `composer_files`，提交后才返回包含 `task_context` 的回执。解析排队不等于已读；上传/关联失败不得显示成已选成功。
- `GET /session/<id>/context` 返回本人明确待发送的附件引用。页面恢复时逐份读取现有 Files 元数据和事件，不扫描“账号最新附件”、不从共享简历缓存或演示记录找替代品。排队、解析失败及读取失败保持真实状态；ready 后停止转圈。
- 发送的既有 run admission 在同一事务中再次核对附件 owner、原文件版本和 ready 状态，更新分析 focus 并清空待发送选择。未就绪/版本不符返回冲突，不能回退成旧确认简历。已分析资料仍留在任务 focus，可继续追问，但不恢复成新一轮未发送附件。
- 移除复用 `POST /session/<id>/handoffs`，传 `purpose: composer` 和剩余文件引用（允许空数组），先保存再移除界面条目；冲突时保留条目并提示重新核对，不删除原文件。旧 handoff 不带 purpose 的行为保持兼容。
- 上传、移除、恢复均绑定账号与任务代次，迟到回执不更新新账号/另一任务。失败附件在发送前检查，不让一次自动恢复先抹掉失败条目再分析旧简历。
- 完整助手页沿用 `session_id` 查询参数保存当前选择；先恢复来访地址，再反映新选择。即使默认最近任务还在运行，也不得覆盖明确直达的旧任务。地址是导航，不是权限凭据。

上传中断/关联 CAS 冲突可能留下本人已入库但未选中的文件；不伪称整个文件写入与交接是跨存储原子事务，也不自动重传未知结果。实际正常页面回放、失败及运行边界见[原统一任务记录补记](../../../reports/2026-10-22-unified-task-orchestration-replay.md#未发送附件恢复2026-10-23)。

> 物料 / 推荐形态契约见 [contracts/ai-assistant-materials.md](../contracts/ai-assistant-materials.md)。mock 回复、工具卡、成果物卡必须遵守该契约。

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/ai-assistant` |
| name | `student-ai-assistant` |
| roles | student |
| immersive | true |
| public | false |

## 页面职责

### 学生助手正文、过程、附件与终态（2026-10-24）

- 发出消息时，当前请求携带的就绪文件只从输入框草稿中移出；已发送用户消息仍保留附件名称，上传文件和会话资源引用不删除。这样长回复期间不会在输入框再次显示同一附件。
- 会话挂载或回答失败时，仅在原会话仍处于前台、输入框没有更新文本时恢复本次文本和附件草稿。恢复按文件 ID 合并，不清空之后添加的附件，也不让一次发送使并行上传失效。
- 统一任务模式以服务端接受的 run 和 `/context` 回执为准，只消费该 run 携带的文件选择；失败/不确定结果保留原草稿和原幂等请求，刷新后按服务端待发送引用恢复，不通过删除上传资源来清理输入框。
- 学生 HTTP 进度卡将生成阶段标题与消息状态、权威 `TurnOutcome` 一起呈现。终态显示已完成、需要回应、部分完成或未完成；仍在执行的 run 保持运行提示。普通业务标题不被重写，也不从阶段文案推断工具实际完成。

从职途导航的当前简历解读继续咨询时，精确报告引用不再把同源补充排除在外：服务端核对报告确认版本与当前画像来源，模型才可选择D3自述；读取的是revision一致的本人文字，不从分数推断行为，也不把自述变成核验事实。原报告问题与新补充分开，不能说“没读到”就是不存在，或要求先写进简历才能讨论。纯章节计数保持原行为。

该资料范围内的答复以一个实际问题为重点，不把校园练习变成工程资格考核，不编人名、字段、人数、使用者或业务效果作为例子。已知正文违规使用原同一次输出纠正，不追加语义裁判和生成循环；仍不合格须明确未完成，不删坏句伪装成功。其他Ask和附件路径不启用此窄展示约束。读取状态仍由原evidence_summary与历史通路呈现，原页面设计未改。真实模型失败、同源读取及回访证据归[同一修复报告](../../../reports/2026-10-22-real-page-experience-repair.md#正文职责分离与下游纠正2026-10-23)。

仅要求回读“原解读已讲清什么／原问题是什么”或“这次读到了哪些补充”的问题，由既有资料选择模型给出source_readback意图后，呈现实际当前记录，不另让助手生成一套教练评价。本人补充单列自述，读取限制放原依据组件；现有报告文字不再自由改写成例子或能力判断。需要新分析／新行动建议时仍由模型回答。不是固定演示答案；每次重读当前owner／版本，旧版或混合版本不能短路，不完整读取保持partial。

同一会话续问不必再手工选一次报告。无引用或带章节引用时，已选定的报告都须补读其现行解读；没有读到不能改为新生成建议。资料选择先看到当前解读预览，而非仅凭目录标题猜测。原内容、独有经历解读和四项本人补充不被删减；依据部件使用“简历解读”“沟通与协作补充”等友好名称，与历史回读一致。

HTTP 助手显式使用 `presentation="student"`：只把相邻工具／进度部件呈现为较轻连续步骤，正文、追问、引用、成果与确认操作的原顺序不变。已返回的有效步骤、材料与待补信息常驻，不用数量截断或新折叠减负；处理设想与已执行步骤明确区分。新消息、流式正文与历史仍使用同一消息数据和渲染通路，不改模型文字。

本轮参考资料的名称、读取状态及无法读取简历、部分附件、本人提交未核验等限制直接可见，不受 `compactEvidence` 折叠影响。进行中、失败、需要回答与部分完成分别投影；未知工具状态不默认成功，部分结果按实际 `next_action` 说明。学生不显示运行编号、内部模式／缺失槽位及预算术语；只对已知系统生成文案做精确适配，具体业务理由保留。模式沿用“直接回答／自动处理／协作处理”，未知模式不提供假按钮。

共享组件的新呈现为可选属性，全局任务侧栏和其他调用方默认保持原行为；这不代表已验收其全部旧界面。本轮没有改协议、权限、任务执行或成果跳转，证据集中在[原验证报告](../../../reports/2026-10-22-real-page-experience-repair.md#三个主场景常驻内容与阅读层次2026-10-23)。

整合回放补齐恢复边界：HTTP首次进入及手动切换会话时，目标历史就绪前显示“正在恢复对话”，暂禁编辑、发送、附件与语音，避免先接受输入再被迟到的初始化选择清空。目标会话恢复后回到原输入器；不新建对话来规避恢复，也不改变真实发送策略。旧版的非默认会话早输清空已有受控真实HTTP回放，新版同形状验证仍需与完整旅程证据分开记录。

### 统一任务陪伴（2026-10-22，显式启用）

- HTTP 且 `VITE_TASK_COMPANION=true` 时，自动处理使用既有 `/api/agent/runs` 与 owner-scoped 事件查询；完整助手和全局侧栏共享发送、附件、任务与草稿，不复制网络循环。显式专家与旧 chat 保持兼容。页面进入和资料交接本身不调用模型。
- `POST /api/agent/session/<id>/handoffs` 只绑定引用、预期修订和幂等键；`GET .../context` 返回公开任务状态。正式简历操作另由 `POST .../resume-submissions` 读取本人已保存原文件，复用既有 L0 持久意图；不会要求重复上传，也不自动确认候选。
- 原页面视觉与业务操作保留。桌面为可收起侧栏，窄屏为抽屉；收起不是取消执行。正文不套过程卡片，依据默认轻量折叠；方案确认和成果入口才独立呈现。上传／解析转圈来自实际文件状态，完成后消失；历史消息不恢复转圈。
- 同账号恢复原任务；退出／切号清理 controller 并拒收迟到响应。事件使用服务端 `cursor` 续读；每条历史答复保留自己的依据，不套用最近一次来源。附件读取范围与后续位置由服务端保存，不靠用户重新粘贴资料。
- 初次恢复没有显式选择时，按与侧栏相同的最近更新时间顺序选任务，不按`upsertMeta`反向插入后的第一条；已有明确选择则保留该任务。多任务刷新不能静默落到最旧任务，或借开新任务掩盖恢复失败。
- 上传材料的 `evidence_summary` 在新消息和历史回读保留 `student_submitted_unverified`，说明它是本人提交材料，不是已经核验的经历或能力；正文也不能使用“已确认的实践经历”等升级证据等级的说法。进入正式简历仍需页面本人确认，读取本身不完成确认。侧栏首次打开等待任务读取时显示实际忙碌状态，防止无反馈的重复点击。
- 复盘答复在同一任务正文中给出已校验的困难、未知和下一步，而不是只显示摘要、要求学生回原页找余下内容；原页面仍使用同一结构化复盘。依据留在可折叠来源组件，不把内部 ID、URI 或字段名塞进正文。复盘读取边界是系统边界，不得说成学生没读资料或缺少能力。
- 失败说明按已保存原因区分窗口、单轮调用额度、用量未知、服务更新和答复校验；历史原始成果先核验摘要，只做公开说明投影，不回写成成功。失败时“取回这次问题，编辑后再发送”仅恢复输入，不能自动调用模型、覆盖新草稿或清掉材料。一个任务仍在执行或结果待核对时不重复发送。已知可恢复的结构错误在服务端同轮最多整理一次，不以无界重试代替校验。
- 新能力依赖迁移 `0019`、对应后端与 worker；正常开发库本轮不自动升级，开关默认关闭。已执行路径、截图、内容缺陷及完整闭环未验收项见[回放报告](../../../reports/2026-10-22-unified-task-orchestration-replay.md)。

2026-10-23 后续经用户授权：正常现场已为0020，只重载必要服务并恢复5174，本机gitignored配置显式打开任务入口，仓库缺省仍关闭。正常合成账号已验证新附件、跨页只读追问及刷新后恢复最新任务；冷重载前未发送附件焦点丢失的一次失败单独保留，不宣称刷新前草稿／附件选择已有完整恢复能力。证据仍归同一报告。

多轮职业咨询对话；会话栏、任务启动区与 Composer（工具卡 / Files 挂载 / Voice Turn）。

### 前端窗口复用边界（2026-10-09）

- `cpa-xingtu/src/views/student/AIAssistant.vue` 复用 `vue-element-plus-x@2.0.3`（MIT）的 `Conversations`、`Welcome`、`Prompts`、`Thinking`、`Attachments`、`XSender` 与 `Bubble`。组件负责会话列表、空态、快捷任务、思考展示、附件卡和富文本输入等**窗口交互**，业务状态仍走原 store/composable；`Bubble #content` 仍使用本项目 `MessagePartsRenderer.vue`，工具、引用、成果物和状态契约不换。库的 `--elx-*` 及 XSender 内部 `--chat-*` 均在 `src/assets/styles/theme.css` 的 `.ai-page` 映射现有语义 token，后续换主题不需逐组件重做。原创 `AgentMark.vue` 标识用于工作台、助手头像及移动端菜单，静态 SVG/PNG 位于 `public/agent-mark*`。
- 单窗口前端流程是「新任务空态 → 专家目录 → 专家详情 → 以该视角起草任务」。目录包含精选场景、专家/协作组切换、分类、搜索、排序和卡片；Composer 的 `+` 菜单可添加文件、切换处理方式、搜索并选择专家。数据在 `src/components/agent/expertCatalog.ts`，是前端任务视角，不是后端角色 ID，也不是 WorkBuddy 官方组件。
- 原有 `ask / agent / experts` 请求值保留，但主画面不再用三段技术模式占据输入区：默认 `agent` 对应「自动处理」，其他方式收在 Composer 的处理方式菜单。选中专家会把可编辑的视角说明/示例写入**可见输入框**，不覆盖已有草稿或附件，不自动发送，也不换掉当前会话；发送仍走现有链路，不请求 `/api/roles/chat`。独立专家角色接口尚未接通，不把 UI 选择冒充真实角色调用。新任务、切会话和发送完成会清理未发送的专家标记。
- mock 成果物经 `artifact_id + session_id` 进入平台已有的画像、职业分析、学习中心、简历制作、职业报告页面；目标页使用原有图表、表单与课程组件读取**同一份结构化 mock 数据**，不再用独立的摘要页或目标页另一套默认数据冒充本次结果。计划勾选、简历编辑等交互会同步回原会话产物引用；缺字段时在原功能页明确提示，不补造无关分数。mock 数据是演示素材，不代表前端组件本身是 mock。
- HTTP 模式的 `GET /api/agent/artifacts/<artifact_id>` 目前仅返回 owner-scoped 索引，不能作为完整业务正文；此模式不会把索引预览灌进模拟功能页。已有 `task_id` 接收契约的简历确认/管线任务继续走 `student-career-navigation?task_id=...`。正式业务记录跨页读取需另行接对应后端资源契约。
- 上游取证：公开 [Codex 仓库](https://github.com/openai/codex) 快照 `a06545b3`（Apache-2.0）不含 Desktop Web UI 源码；[WorkBuddy 产品页](https://cloud.tencent.com/product/workbuddy)提供客户端但没有开源窗口组件。参考其交互形态，不复制私有打包产物。可导入组件来自 [Element-Plus-X](https://github.com/element-plus-x/Element-Plus-X)（MIT）。官方 [DSH 仓库](https://github.com/deepseek-ai/deepseek-harness) 快照 `d7432673`（MIT）的 `packages/client/ui-chat`、`ui-conversation`、`ui-theme` 可作为交互与 token 分层参考；组件实际依赖 React 18、Cordis 插件和 Lexical 编辑器，不能只改 token 就移植到 Vue，故未引入其运行时。

- **http 模式**（`VITE_API_MODE=http`）：`POST /api/agent/chat` 真引擎 + session；附件走 `POST /api/agent/files` → session resources；语音走 Voice Turn；聊天只传 `attachment_ids` / `voice_turn_id`，**禁止**把全文塞进 `message`。
- **mock 模式**：本地回复 + 思考独白 + 模拟工具卡；每次创建任务覆盖 fixture 的固定 `session_id`，生成新的本地会话 ID，避免不同任务混用同一转录；消息卡与现有功能页共用本次模拟产物状态。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 发送消息（demo） | 发送 | `POST /api/agent/chat` | `mode:demo` | reply/tools | `agent/chat.demo.json` | backend-ready |
| 发送消息（真推理） | 发送 | `POST /api/agent/chat` | `mode:langgraph` + `session_id` | reply/tool_calls | — | backend-ready |
| 创建会话 | 新任务 | `POST /api/agent/session/create` | `{ student_id }` | session_id | `agent/session.create.json` | backend-ready |
| 拉历史 | （预留） | `GET /api/agent/session/<id>/history` | `limit` | messages | `agent/session.history.json` | backend-ready |
| 上传附件 | 回形针 | `POST /api/agent/files` | multipart `file` + student/session | `file_id` + excerpt | — | backend-ready |
| 挂载到会话 | 发送前 | `POST /api/agent/session/<id>/resources` | `{ resources:[{file_id}] }` | resources | — | backend-ready |
| 发送（带附件） | 发送 | `POST /api/agent/chat` | `message` + `attachment_ids` | reply/tool_calls | — | backend-ready |
| 读取成果物索引 | 成果物详情 | `GET /api/agent/artifacts/<artifact_id>` | owner scope | status/preview/resource pointer | 对话 mock 引用 | backend-ready（完整 payload 仍在业务库） |
| 任务状态 | 工具卡自动轮询 | `GET /api/pipeline/status/<task_id>` | — | status | — | backend-ready |
| 语音回合开始 | 麦克风 | `POST /api/agent/voice/turns` | session/student/lang | `turn_id` | — | backend-ready |
| 语音回合结束 | 识别完成 | `PATCH/POST .../voice/turns/<id>` | transcript + is_final | ready turn | — | backend-ready |
| 浏览器 STT | 麦克风 | Web Speech（适配器） | — | interim/final → VoiceTurn | — | local adapter |
| 职业建议列表 | 侧栏建议 | `POST /api/agent/career-suggestions` | 见下 | string[] | — | backend-ready |

### Request / Response 展开

#### 发送消息（真推理）

**Request**

```http
POST /api/agent/chat
Content-Type: application/json
```

```json
{
  "message": "哪些岗位需要 Python？",
  "student_id": "student_001",
  "session_id": "sess_001",
  "mode": "langgraph"
}
```

**Response（节选）**

```json
{
  "success": true,
  "data": {
    "reply": "…",
    "tool_calls": [
      {
        "tool_name": "graph_query",
        "parameters": { "question": "Python 相关岗位" },
        "result_preview": "…"
      }
    ],
    "session_id": "sess_001",
    "elapsed_seconds": 2.4,
    "engine": "supervisor",
    "active_subagent": "qa_subagent"
  }
}
```

**Client：** `api.agent.chat` + `mapAgentToolCalls`（`tool_name` → `name`，`result_preview` → `summary`）

#### 创建会话

**Fixture:** `cpa-xingtu/public/fixtures/agent/session.create.json`  
**Client：** `api.agent.createSession(studentId)`；新对话时重置并重建 session。

## 前端行为说明

| 行为 | 说明 |
|------|------|
| Session | 模块级 `agentSessionId`；每次 http 发送携带；「新对话」调用 `createSession` |
| 思考区 | mock：Claude 式独白流；http：处理中计时 + 完成后「思考了 Ns」（subagent/耗时） |
| 本轮进度 | 仅当公共 `progress` 含计划步骤、待补信息或附件状态等用户可读内容时渲染；原生 `<details>` 可用鼠标/键盘展开。仅有 `path`、`allow_tools` 等路由元数据时不显示空卡。`partial/failed` 用用户语言说明未完成，不暗示存在断点续跑。 |
| 工具卡 | **零字段渲染**：只显示产品化摘要 + 状态（已完成/运行中/失败）；不显示工具函数名、参数、`task_id`（见物料契约 §4）；`pipeline_run` 任务轮询时 status=running |
| 附件 | Upload → `file_id` → session mount；旧入口由摘要与 `attachment_read` 续读，完整内联不重复开放全文工具；统一任务由服务端 `read_window` 实际读取、记录依据与续读位置 |
| 语音 | Voice Turn 生命周期；浏览器 Web Speech 为 STT 适配器；支持 interim；可扩展服务端 STT |
| 快捷提问 | 一律走同一 `sendMessage` 路径（不再本地写死 reply） |
| 打字机 | mock 可做展示模拟；http 目前返回一次性 JSON，界面仅显示处理中与最终原文，不伪装真实增量流 |

## Demo / 联调

- mock：`VITE_API_MODE=mock`，快捷问 + 附件占位可用
- http：本地后端 + `LLM_API_KEY`；验证多轮 `session_id` 不变、工具卡出现
- 身份默认 `student_001`

## 本轮明确不做

- Supervisor SSE 实时 `tool_call` 事件
- 服务端 STT 厂商接入（接口与 VoiceTurn 已预留 `stt_provider` / `audio_file_id`）
- 完整前端「批准读附件」弹窗（走既有 PreToolUse）

### Agent 持久任务控制面板（2026-10-23）

- HTTP 模式且显式启用 `VITE_TASK_COMPANION=true` 后，Agent 页面在聊天消息区之外回读最近任务快照；Ask/Agent 切换不清理 run 状态，回到 Agent 时恢复控制面板。页面刷新后从 owner-bound TaskContext 解析 run，再读取 `/api/agent/runs/<id>`，不根据聊天正文重建任务状态。
- 控制、恢复、保存和历史计划回读都绑定 run ID 与契约/计划版本。目标或约束修订使用会话级稳定幂等键；保存请求只在保存确认、计划摘要、owner 回读成果三者一致后显示为已完成。读取失败时提供重新读取；旧会话、旧 run 或旧查询返回不能覆盖当前页面。
- 需要 `0021_agent_run_task_contract_control` 迁移。控制、恢复、保存和历史计划回读的 owner / run / 版本约束沿用原契约。普通自然语言续接与控制面板分开见下节。

### 当前任务的自然语言续接（2026-10-23）

- 自动处理模式存在当前 run 时，普通文字通过 `POST /api/agent/runs/<run_id>/followup` 续接，正文原样绑定 `{task_id, expected_revision, message}`，并生成一个稳定 `Idempotency-Key`；没有当前任务时才走普通新任务提交。
- `202 waiting_safe_point / classifying` 会以相同 body 和 key 轮询；未决记录在同一浏览器标签刷新后从 `sessionStorage` 恢复，并只用原 body/key 重试。409 标记为旧版本，不继续执行；网络中断或 `needs_reconciliation` 可显式用原请求继续核对，不生成新 key。迟到响应受 owner、session 与 run 保护。
- 只有服务端已完成分类并返回 `result.action=create_new_task`，页面才提交新 run。`ask_clarification` 显示分类器返回的澄清问题；进度、暂停、取消和约束修订更新原 run，不把它们转成新任务。`confirm_save` 只显示确切契约版本、计划版本及显式确认按钮；确认前不保存，保存回执再按原摘要核验。
- 本实现的续接输入暂不支持附加新文件；有附件时会提示并阻止新建 run。续接原话与展示答复写入当前标签的 `sessionStorage`，便于刷新恢复，但不是服务端聊天历史；关闭浏览器标签后这份本地展示记录不保证保留。后端可按幂等键读取单次续接状态，但前端尚未接 GET 回读列表。
- 验证记录：`cd cpa-xingtu && node --test tools/test-agent-task.mjs tools/test-agent-run-control-api.mjs tools/test-agent-run-control-panel.mjs`（36 passed）；`cd cpa-xingtu && npm run type-check` 通过。均为 mock API 的自动化前端测试，不等于真实浏览器、真实模型或安全测试账号验收。当前本地页面虽可访问 `127.0.0.1:5174`，`cpa_agent` 尚未确认是隔离 QA 数据库；真实页面场景、保存回放与截图仍待安全合成账号。

### Agent 终态取消显示与 QA 复测（2026-10-24）

- 控制面板的取消请求提示仅在 run 非终态且取消仍待处理时显示。持久快照即使保留 `cancel_requested=true`，`completed`、`failed`、`cancelled` 终态仍优先，避免已结束任务继续显示“正在等待取消安全点”。
- 回归命令：`cd cpa-xingtu && node tools/test-agent-run-control-panel.mjs`（10 passed）；`cd cpa-xingtu && npm run type-check` 通过。用例覆盖终态残留取消标志、非终态安全点等待、以及刷新后较新契约版本的目标呈现。
- 隔离 QA 真实页面：合成 run `run_e6662b2411ce4102a418f9bfbe06889d` 创建、目标修订至第 2 版、取消及全页刷新恢复均可见；刷新后显示“已取消”和第 2 版目标，等待安全点提示消失。没有 worker 或模型调用；Qwen 0/10、Tavily 未测，不能据此宣称 AI 结果验收通过。截图为本任务内嵌浏览器证据，没有本地导出文件。

### Agent 对话锚定执行时间线（2026-10-24）

- 本节更新较早的“当前任务自然语言续接”实现说明：服务端 run/plan/event 是当前任务状态的权威来源；本地展示缓存可辅助恢复消息，但不重建 run，也不覆盖较新的 revision。
- 持久 run 现在锚定在对应对话消息；执行事件按稳定序号分页读取并去重，历史回放只展示服务端已持久化事件，不伪造思考文本或进度。最终答复不再重复展示整份目标和计划；当前计划版本与已保存 artifact 版本分开显示，旧快照不能覆盖较新的 run 状态。
- follow-up 的确定性补充、目标修订和短控制指令在预算预留前分类，只有符合严格当前 run/版本和意图条件时才走无模型路径；混合或含糊意图仍进入分类。刷新后若客户端丢失幂等键，owner/current-run/current-revision 绑定的恢复接口只处理已经持久化且满足无调用条件的操作，不接受新消息或触发模型重调；save 与新任务不自动恢复。
- 验证：`node --test tools/test-agent-run-control-api.mjs tools/test-agent-run-control-panel.mjs tools/test-agent-task.mjs tools/test-agent-run-event-model.mjs`（合计结果见[当日 QA 报告](../../../reports/2026-10-24-qa-ask-agent-ui-replay.md)）；真实 QA 桌面及 433 px 窄屏只读检查通过，完整前端套件、trace 与生产构建结果也见该报告。真实 UI 暂停/恢复完整场景和所有事件/卡片 schema 仍未全部覆盖；截图只有内嵌浏览器图像，没有本地可交付文件。
