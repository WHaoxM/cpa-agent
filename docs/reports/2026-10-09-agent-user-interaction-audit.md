# Agent—用户交互契约审计（2026-10-09）

## 范围与证据级别

审计当前 Flask `/api/agent`、`turn_contract`/`egress_pipeline`、权限门禁、附件 API 与 Vue Agent 组件。基线以本地代码及本轮 Flask test client 验证为准；前端默认生产 `mock`，不能把 demo UI 行为等同于真实后端。上游对照快照：仓库内 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c9`；本地 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`。借鉴契约，不声称移植执行内核。

## 本轮已实现并验证：引用公共出口

高优先级 bug：`turn_contract.normalize_message_parts` 原先原样透传显式 `citation`（包括持久化的 `parts_v1`），`_normalize_citation` 也未限制 URL。前端 `CitationPart.vue` 将 `citation.url` 直接绑定到 `<a href>`。因此一个显式引用可把 `javascript:` 等非网页 scheme 与任意内部字段带到 JSON/历史；SSE 又没有相应 `citation` 事件。此处是**不可信引用到用户可点击链接的边界缺口**，并非已经证明浏览器代码执行。OWASP 对动态 `href` 要求 URL 校验，Python 也明确 `urllib.parse` 是解析器而非验证器：[OWASP XSS Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html)、[Python urllib.parse](https://docs.python.org/3/library/urllib.parse.html)。

窄修复：显式引用、编排引用、历史 `parts_v1` 都投影为固定公共字段 `type/source/quote/page/section/url/relevance`；兼容旧 `label→source`、`snippet→quote`；仅绝对 `http(s)`、有 hostname、无控制字符/反斜杠/用户信息/异常端口的 URL 保留，其他 URL 置 `null`，**引用文本不丢失**。`build_turn_events` 增加相同投影的 `citation` 事件，保持 JSON、SSE、历史一致。`backend/tests/test_agent_citation_contract.py` 覆盖投影、无效 scheme、有效 HTTPS、旧字段和真实 Flask JSON/SSE 两个路由。

复现命令（仓库根目录、`mkg` 环境）：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8; $env:PYTHONUTF8='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_agent_citation_contract.py backend/tests/test_agent_turn_contract.py backend/tests/test_agent_stream_parity.py
```

并行提示词中的 JSON 大括号起初触发注册错误；改为转义字面大括号后，已重新运行上述相关测试及主链联合回归。联合回归结果为 `85 passed`（含引用、HTTP/SSE、提示词注册、图谱与运行时测试）。这是 Flask test client 与模拟工具/模型的契约验证，不代表真实浏览器或线上模型质量验收。

边界：URL 允许列表只保证浏览器链接 scheme 的最小安全性，不验证站点真实性、来源可信度、引用与原文是否吻合。`egress_pipeline._public_orchestration` 当前不投影 `orch.citations`，所以一般编排内部若只放这里，并不保证用户端自动出现引用；本轮未扩展这条共享出口。

## 现状与建议（未实现）

| 契约 | 当前可见行为 | 下一步最小可验收改造 |
| --- | --- | --- |
| 澄清问题 | Agent 可返回 `agent_clarify` 文本；Experts lead 决策含 `missing_slots`/`clarify_options`，公共 progress 仅暴露部分字段。没有稳定的 `clarification_request` part。 | 定义带 `question/options/missing_slots/continuation_id` 的类型化澄清事件；补答应回到原 turn 状态，而非仅把新文本当独立问题。用两轮测试验证。 |
| 长任务进度 | `build_turn_events` 从**完成后的** PublicTurn 生成 `turn_start/progress/tool_done/.../turn_done`。Supervisor `/chat/stream` 先 `_run_supervisor` 跑完才 yield 事件；不能称为实时进度。 | 将执行期事件写入可重放队列，先建立 `turn_id` 与单调 `event_id`，再验证首个事件先于工具结束。 |
| 取消/恢复 | `/api/agent` 有 session 删除/归档/历史，无 turn 取消或恢复路由；当前 SSE 客户端断开也未证明能取消后台执行。 | 复用上游成熟的 cancellation + checkpoint 思路而非自造状态机：OpenCode `session/run-state.ts`/`session/prompt.ts` 有显式 cancel、abort 和 `busy/idle`；Codex `client_common.rs` 有 consumer-dropped cancellation；LangGraph 官方提供 checkpoint + interrupt/resume：[LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)。先定义幂等 cancel 与 side-effect 边界，再接 UI。 |
| 工具权限/失败 | `permissions.py` 中 HTTP 交互不能处理 ASK：ASK 被转 DENY，`pending_tool` 仅为未来 UI 保留；工具拒绝可进入公共 `permission_denials`。 | 不把“已拒绝”误写成“等待确认”。若做审批，需 typed `approval_required`/`approve`/`deny` 和恢复点、超时、审计，测试未批准绝不执行。Codex 本地 `apply_patch.rs`/`apply_patch_tests.rs` 有独立 approval requirement，可参考边界而不复制内部协议。 |
| 附件状态 | 文件后端有 `ingesting/ready/failed` 和 `upgrade_status/ocr_capability`；前端 `agentMedia.ts` 最多轮询 240 次×500ms，仍 ingesting 时**本地合成 failed**，可能与后端权威状态分叉。同步 `/chat` 接受 `attachment_ids/content_parts/voice_turn_id`；`/chat/stream` 只接收非空文本，不具附件/多模态同等入口。 | 前端轮询超时显示 `still_processing/unknown`，不能写入服务端 failed；同步与流式共用 `prepare_user_turn` 契约，测试相同附件 ID 得到同样完整性标记。 |
| 引用与透明度 | 本轮仅修公共引用投影。进度、附件 `document_complete` 等可见，但尚无每个关键职业判断到实际证据片段的强制绑定。 | 将证据记录关联 `file_id/page/block` 或网页抓取快照；缺证据时显式“依据不足”。用人工标注样本测引用支持率，不能拿 URL 存在率替代。 |

## 尚未验收 / 不作承诺

- 未验证真实浏览器点击、实际长任务断线重连、取消竞态、审批恢复及前端 HTTP 模式全链路；以上均不得写成已交付能力。
- 未评估主模型职业建议质量或引用事实正确率；要先完成 OCR/附件逐页校对和全文消费证明。
- `turn_contract._progress_summary` 的 `missing_slots` 原来把整个 Python 列表转成字符序列再逐字拼接；本轮已改为逐项拼接，并在 `test_agent_turn_contract.py` 加了四项上限的回归测试。
