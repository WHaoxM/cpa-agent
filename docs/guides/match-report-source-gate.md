# L3/L4：可信岗位发布与持久化证据快照

维护对象：后端工程师与联调验收人员。日期：2026-10-10。状态：发布仓库、匹配快照和证据报告适配已实现；真实岗位与完整职业建议质量未验收。

## 当前真实状态

本机 base Docker 项目的 MySQL、Neo4j、Redis 已运行。只读检查发现 job_posting 和 job_portrait_4d 均为 0 行；没有可以自动升级成可信来源的真实岗位。本轮只应用了新增 publication 表的 additive migration，不修改既有岗位数据。

原来的“永久 catalog gate”已改成查询服务端发布仓库：缺发布依然 partial，不执行 scorer。存在经审核发布时，L3 可以持久化匹配，L4 可以固化同一证据版本。但是：

- 缺少已确认且可溯源的 D3 事实或其他评分字段未知时，L3 仅返回 `partial/scoring_evidence_incomplete` 管线任务，`match_id=null`，不把数据库 NOT NULL 默认零分写成实际评分。
- 证据充足时 L3 仍明确 status=partial、score_kind=policy_estimate、reason=scoring_policy_not_validated。评分策略尚未验收，不能把规则分数称为可靠职业结论。
- L4 明确 status=partial。行动计划、推荐来源与评分质量缺口会列入 missing_sections，不再调用旧 ReportGenerator 重算画像/匹配，也不填充行业趋势、学习时长、晋升路径的 fallback。
- verified=true 仅表示业务记录、owner 和内容摘要写后回读一致，不表示建议质量通过。

## 共用执行链与边界

页面 POST /api/profile/student/pipeline 与 Agent pipeline_run 使用同一 PipelineRunner 的 L3/L4 类。

1. 核验服务端 owner 下 L0 确认任务与当前 student_profile 内容。
2. 核验同 owner、同目标岗位或无目标岗位的 L2；画像引用的任务必须 completed，任务结果中的 ID/完整画像必须一致。
3. read_published_job 只读取 job_catalog_publication。旧岗位表的 published 字段、请求体、模型参数不能授予发布权限。
   发布来源还必须有获取时间、许可证/使用限制声明、UTF-8 来源文本 SHA-256 和逐字段原文引文。发布与可评分是两个状态：如果任一硬约束或必备技能来源为 `not_stated`，L3/L4 返回 `partial/catalog_requirements_unknown`，不把旧 scorer 的空学历视为「无要求」。
4. L3 将发布快照的 required_skills 和完整 job_profile 硬约束显式传入现有 scorer；删除原先 50/0.8 的第二套硬编码判定和旧混合图 fallback。保留 scorer 的 D1/R-08 结果，不以高总分覆盖失败约束。
5. scorer 的薄适配把简历 flat skills 转为其 typed skills 输入；原始确认 profile 不变。未知评分先停在任务 `partial`，不写 `match_result`；数值证据齐全时，result_json 保存确认/画像/发布版本、规则配置与实现摘要及原始快照。匹配写入后按 owner 回读完整 JSON 才返回真实 match_id。
6. L4 校验匹配所属 owner、任务状态/verified、match_id、内容摘要和当前来源版本，消费已落库匹配快照。内部/外部 report_id 均由服务端生成并一致，用户传入 ID 被忽略。报告写后按 owner 回读；失败绝不记完成。

完整 JSON 与摘要用于可追溯，不是授权替代品。当前 match_result 的唯一键仍按 owner/job 覆盖；报告保存不可变副本，后续重算不能改变既有报告证据。并发确认/重算的数据库级串行化尚未专项验收。

## 发布操作

2026-10-10 补充：用户授权模型承担岗位审查后，新增独立 `job_catalog_review` 结构化审计和官方 feed 模型审查 CLI，见 [岗位模型审查](job-catalog-model-review.md)。模型访问、逐字段分析、证据门禁与真实 MySQL 落库均已执行；模型主体明确记录为 `actor=model`，不伪装旧 `approved` 的人工含义。本轮两条真实候选各两次审查均因证据契约失败保持 blocked，没有发布或绕过评分质量门禁。

新增表在 init_mysql.sql 中；已有实例应用 backend/scripts/mysql/migration_job_publication.sql。这个表不自动回填历史行，也没有开放 HTTP/模型写入入口。

发布前人工核对来源、逐项要求及记录 reviewer。输入 JSON 必须显式包含：

- position_name、source_uri（HTTP(S)）、source_text、reviewed_by、reviewed_at（ISO 8601）；
- review_state=`pending|approved`；待人工逐项批准的候选只能 `--check`，`publish_job` 拒绝写入；
- source_fetched_at（带时区、不得晚于审核）、source_license（来源许可或使用限制原文标识）、source_text_sha256；
- field_evidence：position_name、degree_requirement、major_requirements、major_strict、required_certs、required_skills 各有 `status=explicit|not_stated`；explicit 必须附来源文本中真实存在的 `excerpt`，not_stated 不得填写引文；
- required_skills：对象列表，每个对象有 name；来源没写时可空，但不会进入评分；
- job_profile：degree_requirement、major_requirements、major_strict、required_certs，不能靠缺省值推断岗位约束。

管理员命令（从项目根目录）：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B backend/scripts/mysql/publish_job_catalog.py <reviewed-job.json> --source-file <reviewed-source.txt> --job-id <job-id> --version <immutable-version> --reviewed --check
D:\Miniconda\miniconda3\envs\mkg\python.exe -B backend/scripts/mysql/publish_job_catalog.py <reviewed-job.json> --source-file <reviewed-source.txt> --job-id <job-id> --version <immutable-version> --reviewed
```

`--source-file` 是人工复核过的 UTF-8 网页文本快照，必须与 JSON 中 `source_text` 完全一致；`--check` 不写 MySQL 并显示可评分缺口。来源 URI、抓取时间、license、reviewer 和文本摘要可审计，但 CLI **不会替代真人访问原始岗位页、判断是否仍在招聘以及逐项确认语义**。不要把 ESCO/O*NET 职业分类冒充招聘岗位，也不要把某岗位的“本科或等效经验”擅自简化成绝对本科门槛。版本只 INSERT，不 UPSERT；重复版本失败。撤回时由管理员对确切 publication id 将 status 改为 revoked；读取最新版本时不会退回更老的 published 版本。不要删除历史行以掩盖撤回记录。

### 2026-10-10 真实来源审查候选（未发布）

选择 [DATA.GOV.HK 公务员事务局招聘数据](https://data.gov.hk/en-data/dataset/hk-csb-csb-gov-vacancies/resource/97076ed1-b6a6-4b7f-9f0d-dc899e944a51) 中的实时 `jobid=50368`（Educational Psychology Service Assistant Officer），**这是招聘广告而非 ESCO/O*NET 职业 taxonomy**。官方 [DATA.GOV.HK 使用条款](https://data.gov.hk/en/terms-and-conditions) 允许商用/非商用下载、复制、分发并要求注明来源及知识产权人；许可记录放在快照中。官方 feed URL、记录 ID、获取时间、抓取时 feed/record SHA-256、发布日期、截止日期、逐字段引文在 `backend/tests/fixtures/reviewed_sources/hk_csb_50368_snapshot.json`，最小引文复核文本在旁边的 `_source.txt`。这是机器辅助审查材料，**不是人审批准**。

该招聘写“学士学位……或同等资格”，现有 scorer 的单一学历等级无法准确表达替代资格，故 `degree_requirement=null` 而非虚构绝对本科；专业等价、证书要求未能从当前原文安全映射，留 `not_stated`。预检结果：`valid=true, publishable=false, scoring_readiness=false`，缺 `major_requirements`、`major_strict`、`required_certs`、`degree_requirement_value`。不能将该候选写入可信目录并让 L3 打分。只有人工批准且扩展/验证等价学历与专业规则后，才可重取官方 feed、重新逐项审阅，确认未过期，改 `review_state=approved` 并以新版本发布。

只读重验命令（feed 会更新；`record_unchanged=false` 会返回 `current=false`、`reason=record_changed` 并以退出码 1 触发重审，而非自动更新）：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B backend/scripts/mysql/verify_hk_csb_source.py backend/tests/fixtures/reviewed_sources/hk_csb_50368_snapshot.json
D:\Miniconda\miniconda3\envs\mkg\python.exe -B backend/scripts/mysql/publish_job_catalog.py backend/tests/fixtures/reviewed_sources/hk_csb_50368_snapshot.json --source-file backend/tests/fixtures/reviewed_sources/hk_csb_50368_source.txt --job-id hk_csb_50368 --version source-2026-10-09 --reviewed --check
```

### 2026-10-10 08:10 UTC 重验（替代上述候选创建时的运行状态）

模型已经执行了官方来源访问、许可证核对、原记录摘要/引文/截止日期复验、发布预检与数据库状态核验，不需要把这些技术验证交还用户手工执行。真实 feed 的 `50368` 记录摘要仍为 `8317decd44928022aa2afe20e47dc74e899680b8682e4460dfbb3dd54c46f055`，截止时间为 `2026-10-22T23:59:00+08:00`；结果 `current=true, reason=verified_unchanged`。feed 整体摘要更新不影响该记录的审查有效性；该检查不等于人工批准，也不补齐无法表达的等价学历规则。

修复前，来源记录变更但所选引文未变会返回 `current=true` 和退出码 0；现以**完整单条记录摘要**为重审边界。重复 ID、记录消失、截止时间未知、达到截止时刻均失败；有时区的日期保留原时区，仅官方无时区日期采用香港时间。新增的网络响应测试均是明示的合成夹具，不替换真实捕获文件。

本机权威 MySQL 只读回读：`job_posting=0`、`job_portrait_4d=0`、`job_catalog_publication=0`；`student_001` 的 `student_profile=1`、`student_portrait_4d=1`、`match_result=0`、`career_report=0`。当前确认 L0 为 `task_56edb2e94056`，最新 L2 `task_c2357837ba76` 为 `partial`，缺 `school_tier`、`major_category`、`communication`、`teamwork`、`stress_tolerance`、`responsibility`。存在画像行不等于已完成可用于匹配的画像。

直接只读调用 `require_confirmed_profile` → `require_authoritative_match_sources` 的实际结果是 `partial/confirmed_portrait_unavailable`、`next_action=generate_portrait`。因此 `student_001` 当前最早阻断是 L2 证据不全，而后还有空可信岗位目录；本轮没有替该用户补造画像、发布合成岗位或产生匹配/报告 ID。修复未知事实的语义策略与可信岗位的审查主体是后续独立契约，不应靠伪标 `approved` 或写入默认分数绕过。

## 验证与复现

### 详情与导出的读侧版本门禁

严格身份模式下，旧 `GET /api/match/result/<student_id>/<job_portrait_id>`、
`GET /api/report/<report_id>` 与 `GET /api/report/<report_id>/export` 不再把
`match_result` / `career_report` 行存在直接当作当前可信成果。详情先按 owner
查询，再复用 `match_report_resources.read_match_report_resource` 回查任务、内容摘要、
当前确认简历/画像及岗位发布版本。过期或撤回返回 `409 status=stale`，不返回旧分数、
报告正文或导出地址；仍可在数据库中审计旧行。有效但 `partial` 的详情明确返回
`status=partial`，不将其写成 completed。显式 `CPA_DEMO_MODE` 的旧 demo 报告
仍使用演示读取契约，不代表权威职业成果。列表和匹配解释等旧读取面尚需同一门禁，
不能仅凭本节视作所有读侧已封闭。

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_verified_match_report.py backend/tests/test_match_report_source_gate.py backend/tests/test_resume_golden_path.py backend/tests/test_match_scope.py backend/tests/test_p0_scope_truth.py -q
$env:CPA_TEST_LIVE_MYSQL='1'
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_verified_match_report_mysql.py -q
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_hk_csb_source_verification.py backend/tests/test_verified_match_report.py backend/tests/test_verified_match_report_mysql.py backend/tests/test_match_report_source_gate.py -q
```

真实 MySQL opt-in 测试：随机隔离 owner/job，提交合成确认与画像、发布合成岗位、执行真实规则计算、回读 match/report，验证专科学历不能通过本科要求、版本不可覆盖和撤回后 L3/L4 均拒绝，原报告仍保留且不生成新报告。finally 只按本轮确切 owner/job 清理测试记录。语义归一化模型禁用；未调用 OCR/LLM；这不是“真实简历全链质量通过”。禁止把测试 example.com 岗位留在生产目录。本轮四个专项文件合计 **34 passed**（启用 `CPA_TEST_LIVE_MYSQL=1`）。

单元/入口测试另覆盖跨 owner、内容被改、来源版本变化、失败任务、写后回读丢失、伪造 report_id 和页面→Agent 共享快照。此前 SourceDB gate 测试继续保留。

## 复用记录与剩余事项

- opencode commit 388406238bd5ca15564a762840a2362c3a45bd9c，packages/opencode/src/tool/read.ts：显式标注来源与不完整投影；未复制其通用文件工具。
- openai/codex commit a06545b311fe01e51ce855c7aa5d8da21e9e7aaf，codex-rs/core/src/tools/approvals.rs：可信审批上下文独立于模型内容；未复制通用审批状态机。
- 直接复用项目 PipelineRunner、SkillMatcher/config loader、SQLAlchemy/MySQLClient。新增的是领域发布仓库和快照渲染适配，不是新的 Agent 编排器。
- 外部数据标准核对：[Schema.org JobPosting](https://schema.org/JobPosting) 的 title/description/skills/educationRequirements 能作为原始字段导航，但其结构化数据不自动赋予发布时间、真实性或硬约束语义；[Google JobPosting 说明](https://developers.google.com/search/docs/appearance/structured-data/job-posting) 要求 description 覆盖职责、资格、技能和教育要求。ESCO 是职业分类、不是实时职位；[O*NET 授权说明](https://www.onetcenter.org/license_agreements.html) 提供职业信息复用许可，但同样不是具体岗位的招聘证明。因此本流程保留逐字段审阅和原文引文，不以分类库自动填补 `not_stated`。

下一步需要经审核的真实岗位来源、真实简历输入、D3 等语义策略替换/验证、推荐来源和并发重算验收。未满足前维持 partial，不仅移除门禁就宣称完整职业报告。
