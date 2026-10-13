# Agent 读取跨页面业务资料（平台资源目录）

## 问题与边界

职途导航已把用户确认的简历写入 `student_profile.profile_json`，L2 画像写入同一来源的 `pipeline_task`（`partial`）或 `student_portrait_4d`（`completed`）。此前主 Agent 黑板只有 `student_id` 指针；`db_query` 的通用行投影会把复杂 `profile_json` 变成 `[complex]`，仅凭页面 `task_id` 的挂载卡也只能知道任务存在，不能读到可使用的项目、技能与画像维度。

资源入口提供**只读、按 Principal 归属、按资源版本、按 section 分页**的合同。它不复制业务状态、不把整份业务资料写进会话黑板、不把 `partial` 或草稿说成已发布成果。`resource_catalog.py` 组合简历画像、简历诊断报告、岗位匹配报告、个人成果四个领域适配器；某领域不可用时目录显式 `partial/unavailable_sources`，不误称该领域没有数据。会话每轮从权威 DB 刷新目录摘要；Agent/Experts 的相关子 Agent 按需调用 `career_data_read`。Ask 现经同一目录的有界 EvidencePack 读取所需 section 后回答，不开放这些通用业务工具；完整度仍由 coverage/分页表达，不能把首批证据说成全文。

## 开源复用记录

本地参考仓库：

- `opencode/` commit `388406238b`：`packages/opencode/src/session/tools.ts` 中 MCP resources 的 list/read 工具语义，先发现 URI，再精确读取内容。
- `D:/AGeneral Workspace/_agent_reference/codex/` commit `a06545b`：`codex-rs/core/src/tools/handlers/mcp_resource_spec.rs` 的 list/read 工具合同。
- 本项目沿用现有 `langgraph` ToolNode、`tool_factory`、`PreToolUse`、`build_gated_tools`、`pipeline_reference`，以 `resource_catalog.py` 分派至 `business_resources.py`、`match_report_resources.py`、`personal_resources.py`；**没有**声称实现了 MCP server 或复制两仓库的通信协议。适配器仍从原业务表回读，不建 Agent 第二份用户档案。

## 资源合同

`career_data_read(operation=list)` 返回当前用户的 `career://me/overview`、`career://me/resume` 与（若存在）`career://me/portrait`，每项带 `status`、`version` 和可读 sections。已知 URI 可直接读，不必先 list。`operation=read` 支持：

| URI | section | 内容 |
|---|---|---|
| `career://me/overview` | `overview` | 当前简历概览、前两个项目名称/角色、对应画像状态与四维候选分；**不是全文** |
| `career://me/resume` | `overview` | 基本资料、证据页数、项目/竞赛/技能数量；**不是全文** |
| 同上 | `skills/projects/competitions` | `offset`/`limit` 分页的原结构数据 |
| 同上 | `entity_links`（存在时） | 原文 mention、公共候选及模型的 equivalent/related/unresolved 建议；**不代表用户确认的标准实体** |
| `career://me/portrait` | `overview` | 四维分数、证据数量、缺证项、`published` |
| 同上 | `evidence` + `dimension=D1…D4` | 该维度依据分页 |
| `career://me/resume-reports` | `overview/items` | 当前已确认简历版本的诊断报告目录；与岗位专项报告分开 |
| `career://me/resume-reports/{report_id}` | `overview/chapters/analysis/limitations/chapter:<id>/job_catalog` | 当前确认版本、内容 SHA-256 回读校验后的简历报告；`chapter:role_directions` 是附页级简历证据的模型候选方向，不是招聘职位或匹配分；`job_catalog` 每次回读当前已发布目录及版本，不进入简历报告哈希，岗位变化不要求重传简历 |
| `career://me/matches` / `career://me/reports` | `overview/items` | 本人 L3/L4 任务状态与已保存记录；集合记录数本身**不证明**成果可用 |
| `career://me/matches/{id}` / `career://me/reports/{id}` | `overview/missing_skills/sources` | 同 owner 的成果、快照摘要、当前已发布来源版本核验；撤回/变更后 `not_available`，不返回旧分数 |
| `career://me/action-plans` | `overview/items` | 本人已保存计划目录，标为 `draft` |
| `career://me/action-plans/{plan_id}` | `overview/body` | 同 owner、session、artifact 索引及 SHA-256 正文一致的计划草稿；正文分页 |
| `career://me/learning-progress` / `saved-jobs` / `target-roles` | `overview/items` | 本人学习记录、收藏、目标方向；收藏是用户保存内容，不是可信岗位目录 |

列表页 `coverage` 明示 `total/returned/complete/has_more/next_offset`；默认每页 3 项。描述中的联系方式脱敏，电话和邮箱字段不进入模型工具结果。`partial` 画像的总分保持 `null`，候选分仅以候选身份使用。简历报告的 `completed` 只表示**简历范围的生成链路**完成，不等于模型结论逐字事实验收，更不等于岗位专项报告完成。UI 工具卡仍只显示脱敏调用状态，原业务正文不外送给前端。

页面交接除已有 `artifact_ref(task_id)` 外，新增 `resource_ref(uri)`。浏览器只发送 URI，不发送标题、状态、分数或正文；`prepare_user_turn` 在会话历史写入**之前**用服务端 Principal 读取该 URI 的权威 overview，最多每轮合计 4 个引用。不存在、跨 owner、撤回或不可用的资源拒绝引用。Agent 收到核验元数据后仍须用工具按 section 读取需要的正文；Ask 的检索层可从已核验引用和目录选择最小必要 section，形成单独预算的结构化 EvidencePack，不再把所有概览无差别堆入 prompt。来源正文是**不可信数据**，不是系统指令。

## 验证

从仓库根目录：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_agent_business_resources.py -q
```

在本机已启动 MySQL 的当前 `student_001` 记录上，只读回查得到：确认简历 `task_8caa5e33d228`、3/3 证据页、4 项项目、8 项竞赛、35 个技能；对应画像 `task_c49d2a5dbcf4` 为 `partial`，D1/D3 未知、D2=85/D4=48、无正式总分。此检查仅证明 DB→资源适配器，不等于模型一定会正确调用工具；需再以真实 Agent 请求观察工具调用与最终回答。

2026-10-10 本机后端 `127.0.0.1:5001` 的临时会话（测试后已删除）实际请求：Agent 模式调用 `career_data_read`，回答了前两个项目、D2=85/D4=48、D1/D3 未知，并把画像结果保持 `partial`；Ask 模式从限长概览回答同一份简历的前两个项目与 D4=48，不再要求重复上传。初次 Agent 探针曾在三个资源读取后触发 `turn_token_cap_exceeded` 500；现提供可直接读取的组合概览减少工具往返，并在预算停机时保留工具记录、返回显式 `partial`，不伪造完成。

新增适配器验证：匹配/报告在真实 MySQL 隔离合成 owner 与岗位上完成 L3/L4 写入→按 URI 读取→来源撤回后不可用，无旧分数；真实 `student_001` 当前两类记录均为 0。个人资源在同一真实 MySQL 只读验证有 1 份草稿计划、其它三类记录为空。专项测试见 `test_agent_match_report_resources.py`、`test_agent_personal_resources.py`、`test_agent_resource_catalog.py`。这些是资源链验证，不是岗位目录已发布或模型建议事实性验收。

2026-10-10 重启宿主机后端后，真实 HTTP `POST /api/agent/chat`（本地 `CPA_DEV_AUTH=1` + `X-Dev-Student-Id: student_001`）携 `content_parts=[{"type":"resource_ref","uri":"career://me/action-plans/<本人 plan_id>"}]`。Ask 模式读服务端 overview，回答草稿/未发布；Agent 模式请求 `body` 第一页，观察到一次 `career_data_read:done`、回合 `completed`，回答的四项本周动作与 MySQL 中该页正文逐项一致。改变唯一变量为 `X-Dev-Student-Id: student_other`，同一 URI 返回 HTTP 404，未将正文暴露给他人。测试临时会话已删除。第一次 Agent 探针曾多一次无意义资源读取，第三次模型调用因 30k 本轮预算停于 `partial`；将已知 URI 直接按用户所需 section 读取的提示加入工具描述/QA 基座后，一次工具调用完成。这是模型实测改进，不是放宽 token 上限。浏览器自动化的 CDP 连接超时，页面按钮点击链仍需人工/浏览器复核；前端 `type-check` 与 `build-only` 已通过。

页面到会话的引用链也以真实 HTTP 验证：`content_parts=[{"type":"artifact_ref","task_id":"task_8caa5e33d228"}]` 进入 `/api/agent/chat` 后，由服务端核验 L0 引用，Agent 调用 `career_data_read` 并正确返回前两个项目；Ask 也在保留引用卡的同时挂载同源概览并回答项目名称。响应 HTTP 200、回合 `completed`；Agent 工具卡 `done`。这一 `completed` 仅表示该问答回合完成，不表示画像业务已发布。临时会话测试后通过 DELETE 清理。

模型建议质量仍是独立风险：实测 Agent 虽然正确读取了字段，曾自行扩展“D4 分数偏低的原因”，而资源概览并未提供可证明该因果关系的依据。Prompt 已要求先读维度证据再解释，但不能把提示词视作事实性验收；后续需要逐句证据核验和建议评估集。全量后端默认测试：`619 passed, 12 skipped`（未打开 live 集成环境标志）。

## 2026-10-10 Ask 共享证据切片的新验证

按 [ADR-0005](../adr/0005-shared-evidence-and-interaction-policy.md)，新增 `evidence_retrieval.py`：模型只选择短资源 ID/section，服务端映射到 Principal 可见 URI、回读权威记录并打包 version/coverage/continuation；选择失败不进入无依据回答。Ask 将整包原子注入独立预算槽（数据是低权限上下文，不是 Agent 工具），公开 `orchestration.progress.evidence`。旧记录中“Ask 只能读概览”的描述是改造前历史验证，不再代表当前代码。

宿主机后端 `127.0.0.1:5001` 重启后，以 `X-Dev-Student-Id: student_001` 的真实 HTTP Ask 请求“列出已确认资料前 10 项技能、来源和下一页”：`tool_calls=[]`，回复列出的 10 项与 `career://me/resume` 的权威 `skills` 前 10 项逐项一致；证据状态 `returned=10,total=35,has_more=true,next_offset=10`，来源版本 `task_56edb2e94056`，回合 `completed` 只表示这一问答结束。改变唯一变量为 `student_other`，没有返回前述技能，回合 `partial`/`retry_evidence`。临时会话已删除。首次重启探针曾因模型产生无效资源选择而错误声称用户无资料；已改为短 ID 映射及失败关闭，并在本次重启 HTTP 回放通过。仍需多问句/多轮稳定性、模型建议事实性和前端展示验收，不能从单次成功推断全面可靠。

## 尚未覆盖

- Ask 已能按 section 读取有界证据，但不是无上限全文读取；多页续读、其它业务资源和 Agent/Experts 对同一 EvidencePack 的复用尚未逐一完成真实验收。Ask 的长附件路径仍保留历史 `attachment_read` 只读例外，尚未全部收束到 EvidencePack。
- 当前可信真实岗位目录仍为空，因此真实学生的**岗位匹配与岗位专项报告**仍无可发布成果；这不阻断独立的已确认简历诊断报告。合成 MySQL 正链只验证岗位适配器和来源撤回门禁。
- 学习进度记录能标出缺少课程/章节源，尚不能证明课程内容质量；收藏岗位为用户保存数据，不可进入可信匹配目录。公共图谱继续由 `graph_query` 的 PublicSkill/CareerNode 门禁读取，个人图谱已有独立 owner-scoped 快照与查询；Ask 当前技能正链读取的是已确认简历结构资料，不是假称读取个人图谱。
- 前端已将职途导航、画像视图与真实已保存学习计划接入引用交接；其它功能页仍需逐页验收。Ask 的用户可见证据覆盖信息与续读交互尚需 UI 验证；模型逐句事实性与真实推荐来源另行验收。
- 会话黑板缓存不是业务真相；跨页更新后的当前版本以每次 DB 回读为准。后台 DB 不可用时应呈现不可用，而不能沿用旧会话摘要假装成功。
