# 附件到主模型上下文：本轮验证与后续预算接口

## 结论范围

本轮自动化测试验证了**解析产物进入模型调用的字节链路和提示词路由**；另有一次使用真实主模型的两页简历小样本（见下文），但不把 mock 回复或单例真实回答当作职业建议质量证明。`complete=true` 仅说明当前保存的提取文本完整进入这一轮提示词；不证明 PDF/DOCX 的 OCR、版面、表格、图像识别正确。

## 可复现证据

测试：`D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q backend/tests/test_agent_document_budget.py`。13 项通过。

1. Ask 和 Agent 均将本轮第 5 份附件送入实际模型消息或 QA TaskPacket，不再只处理前 4 份。
2. 8 份大附件共享一轮 `48_000` 字符近似预算。每份都有文件身份、部分覆盖标记和续读指引；`report.pack_chars <= 48_000`。8 份小附件均以 `prompt_complete=true` 输入。
3. Ask 附件分支现在使用 Prompt Registry 的 `supervisor/ask/attachment` 路由，而非普通 `text` 路由；系统消息含“Ask 附件问答助手”契约。
4. 旧 OCR 产物有缺块或截断时，`materialize_for_ask` 只排队 `schedule_upgrade(file_id)`，返回现有文本及 `upgrade_status`，不在请求线程运行重型解析，也不将现有文本误标为全文。
5. Ask 附件问答保留预算后的本会话历史；在直连模型前，对最终 `system + history + user/附件` 消息调用 `context_window_status`。若估算超窗或模型能力未知，不内联完整附件，保留全部文件指针并转附件工具分页；测试覆盖 `fits=false` 和 `fits=None`。

## 脱敏黄金问题矩阵

本组是**合成的脱敏契约 fixture**，不声称代表本地真实简历的逐页内容；字段为“某大学计算机专业”“独立完成 RAG 检索实验、尚未写评测指标”“Python、SQL”“未写意向城市与薪资”。姓名、联系方式、原始文件 ID 未进入断言或报告。真实简历的逐页 OCR 校对另由解析评估承担。

| 类别 | 问题 | 必须进入用户消息的证据 | 当前验证 |
| --- | --- | --- | --- |
| 教育 | 教育经历对校招有什么影响？ | 学校与专业 | 证据进入完整附件包；mock 答复通过出口。 |
| 项目 | 项目能证明 RAG 工程能力吗？ | 项目描述与缺失评测指标 | 证据进入完整附件包；mock 答复通过出口。 |
| 技能 | 申请后端岗位的技能缺口？ | Python、SQL | 证据进入完整附件包；mock 答复不虚构 JD。 |
| 证据不足 | 去上海工作的薪资能到多少？ | 材料未写求职偏好 | 证据进入完整附件包；mock 答复要求澄清，不编造薪资。 |

**未全面验证：**真实主模型能否在多页 OCR 噪声下稳定抓住项目深度、技能缺口；能否在不同简历上持续拒绝凭空推断薪资；多附件相互矛盾时能否做来源归因。这些需要固定模型版本、更多真实调用、人工金标准与逐题评分，不能由 mock 或下面的一次小样本证明。

## 真实主模型小样本（与端到端 Ask 验收分开）

在两页扫描简历已由 MinerU 重解析、并人工核对页覆盖之后，取隔离临时目录中的 `content_list.json`，按 `page_idx` 拼上显式页标记，使用当前附件专用 Prompt Registry system prompt 直接调用本机配置的 `qwen-plus`。不输出姓名、学校、公司、联系方式或原始回答正文。首次 JSON 问答的实际 `usage` 为输入 **2,463**、输出 **53**、合计 **2,516 tokens**；用下述可复现脚本再跑一次为 **2,470 + 53 = 2,523 tokens**。两次均正确指出教育经历在第 2 页、学历层级为本科、材料未给目标薪资数字，岗位方向概括为 AI 应用开发与 Agent 系统开发，证据页覆盖 1、2 页。请求前 `context_window_status.fits=true`。

复跑脚本 `backend/tests/fixtures/live_resume_model_probe.py` 只打印页字数、受限枚举/布尔评分与 provider `usage`，不打印简历或原始回答：

```powershell
$content=(Get-ChildItem -LiteralPath $env:TEMP\cpa_resume_eval\mineru_output -Recurse -Filter '*_content_list.json' | Select-Object -First 1).FullName
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B backend/tests/fixtures/live_resume_model_probe.py $content
```

另一次使用旧存储的 `full_text` 做非页码职业方向问答，实际 `usage` 为输入 **2,412**、输出 **355**、合计 **2,767 tokens**；答复给出两个岗位方向、相关项目证据及两项需要澄清的偏好。由于该旧记录的版面块仍缺第 2 页，这一轮只作为“已存正文可被模型理解”的旁证，不作为解析完整性证据。

这个小样本表明**该模型在这两个固定问题上使用了末页证据并遵守了未知薪资不推断**；不能据此宣布职业规划提示词总体合格。调用是 `Prompt Registry + LLMClient` 直连，不是完整的会话 Ask API；没有多附件冲突、噪声扰动、真实岗位 JD 与人工盲评分。后续应把这组问题固化为脱敏/授权的人工评分集，再测完整 Ask/Agent 流程。

## 预算现状与未覆盖面

当前 `report.budget_unit=chars_estimate`：`48_000` 仍是附件包的字符兜底，不是精确 token 上限。另已引入**按实际消息和候选模型**的 `context_window_status`：配置中有经核验的 Qwen/OpenAI 模型能力条目，其他模型能力未知时 fail closed；非 OpenAI 模型使用明确标注的 UTF-8 字节保守估算，不宣称与 provider tokenizer 完全一致。Ask 直连前使用这项预检；`LLMClient.chat_with_metadata()` 回传 provider、model、usage 与预算状态，兼容的 `chat()` 仍返回字符串。LLMClient 对每个 failover 候选重新检查窗口，而非复用主模型预算。

仍未证明：Agent/Experts 的 LangGraph 路径全面使用同一最终消息预算；工具输出/多模态 token 精算；生产 provider 对大材料的真实用量与自动压缩效果。未知模型或估算不足时，本轮仅将 Ask 完整内联降级为文件指针 + 工具分页，不能称为全系统 token 预算已解决。

参考本地开源源码：OpenCode `packages/opencode/src/session/overflow.ts` 使用模型的 context/input limit，扣除输出保留额后用实际 usage 判断溢出；`session/compaction.ts` 对待保留消息估算 token 并裁剪旧工具输出。Codex `codex-rs/core/src/tools/handlers/get_context_remaining.rs` 从会话暴露剩余 token；`compact.rs` 在截断保留来源时显式标记 incomplete。

后续集成点（先接现成 tokenizer/provider metadata，不再造字符换算公式）：

1. Agent/Experts 的 LangGraph 模型调用也应在**最终消息**组装后验算；为工具回合及流式调用保留输出 token。未知 capability 需安全降级，不把文档截头当全文。
2. 将 provider 实际 `usage` 与 `finish_reason` 传入会话 compaction/监控，按真实用量调整后续回合；针对超窗形成可见的分页/压缩重试。
3. 尽可能使用 provider 对应 tokenizer；无法确认时保留 `estimate_method`、模型与安全余量可观测，不把字节估算称作精确 token。
4. 记录每个附件的 `source_complete`、`prompt_complete`、OCR 覆盖质量是三个不同维度，不能共用一个“已完整”布尔值。
