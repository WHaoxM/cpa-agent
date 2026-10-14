# 管理员岗位发布 HTTP 契约（2026-10-13）

状态：后端最窄闭环已实现并以真 MySQL/Redis 验证（2026-10-13）；不等于真实岗位已完成人工签审。管理员身份使用平台自建账号；该规格不把模型审查、页面按钮或自报 `reviewed_by` 视为人工审核。

## 实施前现状与仍存边界

实施前，`job_catalog_publication` 已是 L3/L4 唯一可信岗位仓库，`publish_job` 只供本机 CLI，HTTP 管理端没有可信预检、发布、撤回入口。现在下述账号审核入口已实现；旧 CLI 仍从输入 JSON 接受 `reviewed_by/reviewed_at`，不适合作为浏览器凭据。模型 `job_catalog_review` 是独立审计，`candidate_ready` 也不是批准。真实库当前无可信岗位；不因开通入口自动发布旧表或模型候选。

## 最窄闭环

1. `POST /api/admin/job-publications/preview`：只接受受限大小的 JSON 对象，包含 `job_portrait_id`、`publication_version`、`snapshot`。`snapshot` 不得含 `reviewed_by/reviewed_at/review_state`。服务端从登录数据库重载的 admin 身份导出 reviewer，注入暂态 `pending` 做现有 `validate_snapshot` 和 `scoring_readiness`，并检查来源时间、截止时间及完整来源摘要；不发出任何 HTTP 请求，不会因客户端提供 URL 而 SSRF。通过才返回精确候选摘要、来源文本摘要、完整待审快照及短效签名 preview token。预检不是审核。
2. `POST /api/admin/job-publications`：同一 admin 在短效期内提交**完全相同**候选、preview token、服务端返回的候选/来源摘要及明确的人类审核确认。服务端重算摘要、验签、再校验所有字段，拒绝改变后的候选、缺证据、不可评分、过期及模型 `reviewed_*` 冒名字段。审核时间和批准状态由服务端在事务内生成。确认只能证明此账号对所见精确快照作出声明；不能证明网页内容真实、许可证有效或所有语义已人工读懂。
3. 一个 MySQL 事务写不可变版本 `job_catalog_publication` 与 `admin_audit_log`，事务内回读 digest。唯一键冲突明确 409，不能把同版不同内容伪装成幂等成功。发布后再通过仓库回读校验。既有 L3/L4 仍自行确认当前发布版本。
4. `POST /api/admin/job-publications/<id>/revoke`：仅精确 publication id + 预期内容摘要 + 非空理由，事务内 `FOR UPDATE` 与条件更新，审计同事务。已撤回重复请求返回 409，不退回旧版本，也不删除记录。未登录、学生、`X-Admin` 或仅测试注入的 `g.cpa_admin` 均不能操作。
5. `GET /api/admin/job-publications?limit=&offset=` 和 `GET /api/admin/job-publications/<id>` 供管理员回看版本、内容摘要与撤回状态；含撤回记录但不将其重新作为公共岗位。详情回读完整快照并校验摘要。
6. 管理员可另通过 `GET /api/admin/job-reviews` 与 `/<review_id>` 回看模型审查队列，尤其是阻断原因。该记录是原始模型审计，**不是**人审凭据。只有 `candidate_ready` 且重校验通过的记录提供 `candidate_for_preview` 以便复制到第 1 步；不直接签发 preview token，不把模型账号变成人类 reviewer。现存官方源审查全部 blocked，不能因此发布。

## 信任与不覆盖范围

- 不在 HTTP 流程内抓取任意 URL；source URI/文本/许可证是管理员提交并声明已独立核对的资料。CSB 固定源已有只读复核脚本；其 feed 当前性不自动推广到通用 URL。
- `source_closes_at` 可缺省；缺省时招聘有效期未知。即使字段存在，也只校验**管理员提交的时间值**尚未过去，不会自动访问原始页面确认招聘仍开放。对外仍标 `vacancy_validity=unknown`。
- 逐字段 substring 引文和现有评分字段门禁只证明结构，不证明招聘真实性、复杂等价资格语义或评分政策质量。发布后职业匹配仍 `policy_estimate/partial`，列表仍 `vacancy_validity=unknown`。
- 短效 token 由 Flask/itsdangerous 签名而非自造审批状态机；账号身份、CSRF、Redis session 与登录限速复用已实现的 Flask 扩展。服务端不接受 `role` 请求头或请求体、模型 review 直接升级、历史岗位自动回填。
- 旧 `backend/scripts/mysql/publish_job_catalog.py` **仍是单独的可信本机运维 CLI**：它从 JSON 读取 `reviewed_by/reviewed_at`，并调用旧 `publish_job` 直接写入发布表，不经正式账号会话、预检 token 或新的 `admin_audit_log` 原子审计。只有控制本机执行环境、亲自审核了来源原件和 JSON 的运维人员才可使用；不得把该途径产生的 reviewer 字段当作平台账号已认证的人审证据。若要统一批准来源，后续需迁移/禁用该 CLI，不能因为新 HTTP 流程存在而声称所有发布记录具有同等审计强度。

## 验收

先红测未注册 HTTP 路由（404），再真 Flask HTTP + MySQL/Redis 测学生/伪管理员拒绝、预检与改动摘要、无证据/不可评分拒绝、正式发布及审计写后回读、重复版本冲突、精确撤回及旧匹配来源失效。测试使用唯一随机 job/account、最终仅清理该随机数据；全后端套件另由集成方执行。

当前执行：`CPA_TEST_LIVE_MYSQL=1`、mkg Python、D 盘 TEMP，
`pytest backend/tests/test_admin_job_publication_http_mysql.py backend/tests/test_verified_match_report.py backend/tests/test_verified_match_report_mysql.py backend/tests/test_job_catalog_listing.py backend/tests/test_p0_admin_vector_scope.py backend/tests/test_account_auth_http_mysql.py -q`
→ **36 passed**。新增测试含真实 loopback socket HTTP、真实 MySQL/Redis、审核审计注入失败后的 MySQL 回滚；全部测试随机账号与岗位数据已清理。先前组合测试遇到 Flask-Limiter 多 app 工厂前缀复用造成 429，账号适配器已修复，复跑通过。

### 请求形状

2026-10-14 新增 **dataset 来源模式**：当请求含 `source_dataset_id` 时，不再接受客户端 `snapshot`，而按 [dataset 来源绑定](admin-dataset-publication-binding.md) 从私有暂存文件选一条记录预检，并在确认及发布事务内重读、核对文件与记录摘要。下述 `snapshot` 模式仍只适用于单独人工核对的外部来源候选；不能自报 `staged_dataset_*` 伪装成 dataset 绑定。dataset 上传和摘要本身不证明官方来源或批准。

预检：`{"job_portrait_id":"…","publication_version":"v1","snapshot":{…}}`，snapshot 保留来源文本、摘要、许可证、获取/截止时间、逐字段证据、技能与硬约束；**不得**包含 `reviewed_by/reviewed_at/review_state/review_id/review_actor`。预检 200 返回 `candidate_sha256`、`source_text_sha256`、`snapshot_for_review` 与十分钟 `preview_token`，不写发布表。

发布：重复提交完全相同的 job ID、版本和 snapshot，增加预检响应中的 `preview_token`、`expected_candidate_sha256`、`expected_source_text_sha256` 及 `confirm_review` 四个严格布尔真值：`source_checked`、`requirements_checked`、`license_checked`、`publish_exact_digest`。由管理员本人另发第二个 HTTP 请求。成功 201 返回 publication id 与正式内容摘要；缺确认 400，摘要/签名变化或同版重放 409，证据可评分性不足 422。

撤回：`POST /api/admin/job-publications/<publication_id>/revoke`，JSON `{"expected_content_sha256":"<正式内容摘要>","reason":"…"}`。正式摘要不是预检候选摘要。成功后 GET 详情仍可审计 `revoked`，公共目录和 L3/L4 最新来源不得回退到更旧已发布版本。所有写请求还须账号会话的 `X-CSRFToken`。

底层复用与边界：项目 `job_publication.validate_snapshot/scoring_readiness`、`Principal`、账号 DB 角色门禁、`admin_audit_log`；itsdangerous 2.2 `URLSafeTimedSerializer.loads(max_age=600)` 只签预检精确摘要，SQLAlchemy 2 的 `Engine.begin()` 保证发布/撤回与审计提交或回滚。参照 [ItsDangerous 定时签名](https://itsdangerous.palletsprojects.com/en/stable/timed/) 与 [SQLAlchemy 事务](https://docs.sqlalchemy.org/en/20/tutorial/dbapi_transactions.html)。没有复用 OpenCode/Codex Agent 内核，因为这是平台人类账号批准与领域仓库事务，不是模型工具审批。
