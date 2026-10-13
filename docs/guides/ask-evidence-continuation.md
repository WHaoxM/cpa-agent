# Ask 证据分页续读（2026-10-10）

## 问题与修复边界

当前报告 `chapter:role_directions` 有 4 项；Ask 首轮因 EvidencePack 的 4500 字符上限只读 2 项，并公开 `next_offset=2`。旧实现只把完全等于“继续”等四个短命令视为续读；“继续读取剩下两个”重新选中章节第一页，导致重复。修复仅位于 `mode_runtime.py` 的 Ask 分页意图路由，不改报告生成、资源读适配器或 UI。

精确分页命令仍无额外分类调用。对其他自然语言，仅在服务端保存的游标与当前 Principal、session 和 30 分钟 TTL 一致时，调用现有 `LLMClient.chat` 的结构化 JSON 路由，判断是否承接上一页。模型只看到问题与上一章节名，不接收 URI、owner、offset，也不能产生实际读取参数。判断为续读后仍由 `build_evidence_pack` 复核资源所有权、版本、状态修订及字符/条目预算；游标失效时 fail closed，不重新读第一页。单次分类输出上限 80 tokens，纳入现有 `capture_usage_ledger` 和 turn cap；模型不可用时不猜测续读。

复用边界：本项目已有 `evidence_retrieval.py` 的 list/read、显式 cursor、Principal 与版本校验；对照 OpenCode commit `388406238b` 的 `packages/opencode/src/mcp/index.ts` 与 Codex commit `a06545b` 的 `codex-rs/core/src/tools/handlers/mcp_resource/read_mcp_resource.rs` 所用 URI 资源读边界。没有复制其 MCP 传输或另建分页存储。自然语言意图是本项目 Ask 回合路由，不是资源协议本身。

## 真实验收

5001 宿主后端、`CPA_DEV_AUTH=1`，`X-Dev-Student-Id: student_001`。临时会话 `session_27f437d7d280` 已删除；报告资源 `career://me/resume-reports/resume_1ac0d7ae7997bf6f42831bd38694503d`，确认版本 `task_b13910b97b4c`。

1. 创建会话，Ask 的 `content_parts` 引用该报告，并询问四个候选岗位方向；首轮公开进度为 2/4、剩余 2、`next_offset=2`。
2. 同一会话问“继续读取剩下两个候选岗位方向，并列出它们各自的名称和依据。”；返回第 3、4 项，公开 `orchestration.progress.evidence` 为 `returned=2,total=4,complete=true,has_more=false`，来源 section 为 `chapter:role_directions`，版本仍为 `task_b13910b97b4c`。
3. 第二轮 `usage_ledger` 的模型调用为分类 136 tokens、回答 3280 tokens，`verified=true`；测试会话 DELETE 成功。

相关 `test_ask_evidence_surface.py` 与 `test_evidence_retrieval.py` 共 22 条通过，覆盖自然语言续读、新话题不续读、跨身份拒绝、版本变化和预算边界。注意：第二轮本身仅返回剩余 2 项；`total=4` 是该章节总量，不等于第二轮重发全部四项。
