# 已确认简历诊断与动态岗位方向报告

`career_report` 的 `job_portrait_id` 非空且 L4 依赖服务端已发布岗位与匹配结果，不能用它承载简历本身的诊断。新成果 `resume_diagnostic_report` 单独存储，只有当前 Principal 的 L0 候选经用户确认后才可生成。

## 接口与状态

- `POST /api/report/resume-diagnostic`：使用服务端 Principal 的当前已确认简历，按 `student_id + source_confirmation_task_id` 幂等生成/复用。模型失败的 `partial` 允许同接口重试。
- `GET /api/report/resume-diagnostic`：读取当前确认版本；`GET /api/report/resume-diagnostic/<report_id>`：按 owner 读取指定报告。未确认返回 `409 needs_input`；当前报告不存在或他人 ID 返回 404；旧确认版本返回 `409 stale`。
- `data.report_type=resume_diagnostic`；`chapters` 按教育、项目实践、技能资质、竞赛研究、语义事实、实体归一化审阅等结构化。所有当前已确认 profile 中的非空业务域进入固定章节或“其他已提取事实”，系统元数据不当作经历。
- 每条事实的 `evidence_refs` 是在 L0 原始页块中**精确找到**的引句与 `page/block_id`；找不到时标 `confirmed_profile_only`，不猜页码。`entity_review` 只是候选归一化，未修改公开图谱或已确认简历。
- `analysis` 是模型对已确认事实的解释性综合，所有条目必须引用现有事实 `id`。输入显式排除电话、邮箱及未确认的岗位/市场数据。模型不可用、输出截断、引用非法或输入超预算时保留结构化事实，但 `status=partial`，列出 `missing_sections`，不得称完整职业诊断。无缺页且模型输出通过结构化与引用校验时，`completed` **仅表示简历范围内的报告已形成**；`quality_status=model_interpretation_unverified` 表示引用校验并非语义事实性验证，不表示岗位匹配、就业概率或建议质量签审。
- `model_usage` 来自 LLM 实际返回的 usage，不用本地估算替代。

## 岗位方向始终可见，但权威层分离

- `role_directions` 是简历报告中的独立章节。模型在有界（24,000 字符）已确认事实输入上以 JSON Schema 选择 1–4 个探索方向、事实 `evidence_ids` 与待核验问题；服务端校验引用存在，并由被引用事实的原值生成克制的依据文案和精确页块引用。它没有职位 ID、市场需求、薪资、匹配分或「当前在招」含义。模型输出截断、引用非法、超预算、不可用时章节状态可见，`missing_sections` 保留 `resume_based_role_directions`，整体不得假完成。
- 报告事实和方向来自 `source_confirmation_task_id`，GET 同时回传权威 `resume_confirmed_at`；岗位目录的动态叠加 `job_catalog` **不写入** `report_json` 或 `content_sha256`。每次 GET 由 `list_published_job_candidates` 读已审核发布记录，返回独立 `catalog_version/catalog_latest_published_at/status/total/items/has_more`（最多首 20 条）。目录为空仍保留简历方向；目录不可用不使简历事实失效；目录版本变化也不使报告整体 409。
- 已发布岗位只表示来源审阅和发布。`vacancy_validity=unknown` 时不能称实时在招；`empty` 与 `unavailable` 分开。模型方向与目录岗位未建立经核验的语义等价时，以两个独立栏目展示，不能硬连成正式推荐或填造职位 ID。
- 旧版已校验报告 GET 不空白：保留原事实章节并附 `role_directions.status=pending_upgrade`、响应 `status=partial`；用户可 POST 对当前确认版本幂等升级。升级失败仍保留旧事实与明确可重试状态。

## 开源适配边界

报告底层复用已有 OpenAI SDK 兼容 `LLMClient.chat_with_metadata` 及 JSON Schema 输出、已有 MySQL owner/version 回读、L0 页块和确认状态。Agent 读取复用 `opencode` commit `388406238b` 的 `packages/opencode/src/mcp/index.ts` list/read 边界与 `packages/opencode/src/session/tools.ts` 截断状态思想，以及 Codex commit `a06545b` 的 `codex-rs/core/src/tools/handlers/mcp_resource.rs` scoped resource read 和 `codex-rs/core/src/compact_remote_history.rs` 显式截断提示。只做本项目的业务投影；这些实现不提供本项目的职业方向真实性、岗位目录权威、简历版本语义或报告章节，故不能照搬其领域结构。没有另造通用工具协议、预算器或任务队列。

## 验证

`D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_resume_diagnostic_report.py backend/tests/config/test_prompt_loader.py -q`：31 passed。执行 `migration_resume_diagnostic_report.sql` 后，在本机已确认真实简历上以 `CPA_TEST_LIVE_RESUME_DIAGNOSTIC=1` 跑 `backend/tests/test_resume_diagnostic_live.py`：1 passed；当前实库报告 4 个概览事实、4 个教育事实、6 个实践条目、35 个技能、10 个竞赛/研究条目，模型实际 usage 4028 tokens，状态 `completed` 且 `quality_status=model_interpretation_unverified`。这证明链路及覆盖，不构成人工逐字 CER 或模型建议事实性验收。

2026-10-10 当前再验：专项/提示词 `33 passed`，真实 student_001 POST/GET 通过，报告 `resume_1ac0d7ae7997bf6f42831bd38694503d` 有 4 个模型候选方向，证据引用实际覆盖第 1–3 页；目录 `status=empty,total=0,catalog_version=jobpub-fc6505fb34f1557709c93475`。方向如「AI应用与多Agent系统工程师」只展示简历依据与待核验问题，不称实时岗位或已匹配。真实方向措辞的职业适用性仍未通过人工评审。旧报告 GET `pending_upgrade` 单测覆盖。

同日浏览器回放当前 `task_b13910b97b4c`：通过本地学生演示登录进入职途导航，切到「查看完整简历报告」，页面实显 4 张岗位方向卡、3/3 页覆盖与独立的空岗位目录；点击「刷新岗位」再次 GET 后仍有 4 张卡且空目录如实呈现。发现第四张卡的前 3 个引句原按章节顺序显示，未与依据文案选取的事实顺序一致；已改为按模型返回的 `evidence_ids` 顺序投影引句，回归用例覆盖该顺序。重新生成后 HTTP/页面的前三条引句均为第 2 页的 Trace、Hardhat、Solidity。截图位于 `%TEMP%/cpa-role-report-ui-wide-2026-10-10.png`（修正前布局截图；修正后以页面 DOM/HTTP 核对引句）。
最终浏览器再验显示 4 张方向卡和 12 条逐项呈现的核验问题，宽屏截图为 `%TEMP%/cpa-role-report-ui-final-2026-10-10.png`。全量后端回归 `807 passed, 27 skipped`；前端 `type-check`、`build-only` 通过。
