# 官方岗位的模型审查与独立审计

日期：2026-10-10。状态：真实来源 → 真实模型 → 结构/来源字段门禁 → MySQL 审计已验证；没有合格发布岗位，未声称 L3/L4 职业质量通过。

## 边界与复用

本轮用户明确允许模型承担核验工作。检查 `CONTEXT.md` 与 accepted ADR 后，没有发现“岗位审查只能由人工完成”的已接受领域决策；旧代码中 `review_state=approved` 的含义确实是人工批准。因此本切片新增 `job_catalog_review` 独立审计，不篡改旧 `approved` 的含义，不把模型标成人工，也不把模型生成的理由当作发布授权。

模型负责逐字段理解；代码负责固定来源、原文/记录摘要、严格 schema、真实引文包含关系、截止日期、当前 scoring_readiness 和落库回读。没有关键词/正则代替语义审查。全部原记录字符串字段送入模型，不截前 N 字；超预算或输出截断沿用现有 `LLMClient` 的显式失败，不伪称审完全文。

- 直接复用项目 `LLMClient.chat_with_metadata`（provider/model/usage/预算）、Pydantic strict JSON schema、`validate_snapshot` / `scoring_readiness`、`MySQLClient`；不新建 Agent 编排、通用审核引擎或模型客户端。v3 参照项目已有简历证据 ID 思路，模型只选官方 feed 的 `jobname` / `entreq` 字段 ID，服务端从冻结的 `source_record` 回填整个原字段作为引文，避免模型拼接不连续子句；字段 ID 不替代模型语义审查，也不放宽候选来源真实性或评分门禁。
- 对照 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`（MIT）`packages/opencode/src/tool/read.ts` 的不完整内容标记；对照 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`（Apache-2.0）`codex-rs/core/src/tools/approvals.rs` 的可信审查上下文和审批主体分离。仅领域薄适配，未复制上游运行内核。
- 来源限定 [CSB 官方招聘 feed](https://www.csb.gov.hk/datagovhk/gov-vacancies/gov-job-vacancies-en.json)。[DATA.GOV.HK 条款](https://data.gov.hk/en/terms-and-conditions) v1.2 支持注明来源/IP 所有者后的复用；审计记录注明 Government/CSB/DATA.GOV.HK。链接或职业分类不替代招聘事实。

## 可重复执行

从仓库根目录、`mkg` 环境执行。既有数据库先应用 `backend/scripts/mysql/migration_job_catalog_review.sql`；新部署 `init_mysql.sql` 已含同表。迁移仅新增表，不更改岗位/用户记录。

```powershell
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B backend/scripts/mysql/review_hk_csb_jobs.py --list
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B backend/scripts/mysql/review_hk_csb_jobs.py --record-id 50330 --record-id 50368 --persist
```

`--list` 只读、无模型费用与数据库写入；不带 `--persist` 时只输出本次审查 JSON。`--persist` 每次生成新的 `review_id`，仅 INSERT，完整 JSON 与摘要写后回读一致才返回 `verified=true`。不自动修改既有审查或发布。重跑会再次调用模型并形成新审查，不能拿过去通过的摘要套在改变后的原文上。

记录包含 `actor=model`、provider/model、提示词 hash、review_version、真实 usage、原始 record/record hash、feed hash、获取/截止/审查时间、完整来源文本及摘要、license URL、模型原输出、逐字段 `explicit|not_stated|unsupported`、原文引文/解释、不确定性和阻断原因。模型原输出无效时也保存独立审计，但不产生可发布快照。原文未说“无证书要求”不等于没有要求，等价学历不能硬转本科。

`status=blocked|candidate_ready` 都不是 `approved`。`candidate_ready` 仅表示本候选通过技术完整性检查；`publication_authorized=false`，不会绕过 L3 既有 `policy_estimate/partial` 的质量边界。当前没有把新审计变成模型可调用的公开写接口。

## 实际验证与限度

2026-10-10 08:24 UTC feed 有 **63 条**记录，已完整盘点 ID/标题/截止日期。没有使用确定性语义筛选宣称“63 条全部不合格”，也没有逐条调用模型。选择两个毕业生相关候选精审：

1. `50330` Civil Engineering Graduates and Structural Engineering Graduates：原文学历依赖 HKIE 专业/培训入场标准，且有永久居留条件；不能凭外部常识猜为单一学历等级。
2. `50368` Educational Psychology Service Assistant Officer：原文含学位或等价资格、相关专业及语言考试分支；当前 schema 不能无损表达这些条件。

真实 `qwen-plus` 两轮共 4 次调用、原始返回及 usage 已持久化。v1 把有引文字段标为 `not_stated`，违反契约而阻断；v2 明确状态与连续引文约束后，仍有不在原文中的引文，故**最后一次窄测后停止重试**，未以模型自信替代证据：

| 来源 ID | 版本 | 权威 review_id | 最终结果 |
| --- | --- | --- | --- |
| 50330 | v1 | `jreview_f590235b7575450fa95c5f3de7f550b7` | blocked / invalid_absent_evidence:position_name |
| 50368 | v1 | `jreview_74261153f15d4f78b7da8e9c991eb953` | blocked / invalid_absent_evidence:position_name |
| 50330 | v2 | `jreview_5d6dbdfff30e40b8860870ced936a926` | blocked / invalid_evidence:required_certs |
| 50368 | v2 | `jreview_6611c51b305d405aa51509871f08b242` | blocked / invalid_evidence:required_skills |

结构化 assessment 同时保留了等价学历/资格与居留等 unsupported 条件，不能只修一个引文就宣称可评分。旧可信目录仍为空；没有写入合成岗位，也未修改 `student_001` 的 L2/匹配/报告。

后续窄测只重审 `50368` 一次（`csb-schema-review-v3`，`jreview_1f0d791cd5c24bf7af2728c97335c8b3`，真实 `qwen-plus`，3062 tokens）。模型选择 `jobname` / `entreq` 来源字段，服务端回填原值，候选快照通过结构与来源包含验证；不连续引文误报已消除。之后补齐未来候选的 `source_record_id` / `source_record_sha256` 等元数据；用既有审计中的原记录及摘要重组该次候选做**只读**官方 feed 复核，返回 `current=true`、`record_unchanged=true`，没有改写这条不可变审计。审计仍为 `blocked`：`degree_requirement` / `major_requirements` / `major_strict`、证书分支与等价资格不能用当前单一硬约束 schema 忠实表达，并且仍有 `semantic_uncertainties`、`unsupported_requirements`。另一个既审候选 `50330` 原文以 HKIE 资格而非固定最低学历为准，且未列可评分的必需技能，亦不满足门禁。两个候选都不能安全发布；本轮没有重试模型或遍历 63 条凑目录。`job_catalog_publication` 仍为空，匹配/报告继续 `partial`，不是程序假完成。

按确切 ID 回读审计（`review_json` 含完整证据）：

```sql
SELECT review_id, actor, review_version, status, content_sha256, review_json
FROM job_catalog_review
WHERE review_id IN ('jreview_5d6dbdfff30e40b8860870ced936a926',
                    'jreview_6611c51b305d405aa51509871f08b242');
```

专项回归（含 MySQL INSERT/readback、不可覆盖 ID、发布表未变及撤回 L3/L4 双拒绝）：

```powershell
$env:CPA_TEST_LIVE_MYSQL='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest backend/tests/test_job_catalog_model_review.py backend/tests/test_hk_csb_source_verification.py backend/tests/test_verified_match_report.py backend/tests/test_verified_match_report_mysql.py backend/tests/test_match_report_source_gate.py -q
```

结果 **42 passed**。测试网络/模型 fixture 为合成；真实 feed + 真实模型审查以以上 MySQL 行为证，不混称整条真实学生职业规划完成。
