# cpa-xingtu API Catalog（理想 FE 契约）

Status: active  
Last updated: 2026-10-13
标签定义：[status-legend.md](status-legend.md)

按域列出产品需要的 method/path。状态依据后端路由及随后逐域真库复验；**不表示前端已接线**。

公共 envelope：`{ "success": true|false, "data"?: ..., "error"?: string, "message"?: string, "count"?: number }`

---

## auth

| Method Path | 用途 | Fixture | BE 状态 | 备注 |
|-------------|------|---------|---------|------|
| `GET /api/auth/csrf` | 获取会话 CSRF token | — | backend-ready | 自建账号真 HTTP + MySQL/Redis 已验；页面仍使用 mock 登录，未接线 |
| `POST /api/auth/register`、`/login`、`/logout`、`GET /me` | 学生开户、登录、登出、当前身份 | — | backend-ready | 管理员仅可信本机 CLI 开户；需强 `SECRET_KEY`、Redis、账号迁移与 HTTPS/cookie 配置；页面未接线，不可继续把 mock 身份当正式身份 |
| `POST /api/auth/change-password` | 已登录账号自助改密 | — | backend-ready | 当前密码与 CSRF 必需；成功后所有设备会话失效并需重新登录；无密码找回入口，页面未接线 |

---

## career

| Method Path | 用途 | Fixture | BE 状态 | 备注 |
|-------------|------|---------|---------|------|
| `GET /api/career/landscape` | 职业分析全景 | `career/landscape.json` | backend-ready | smoke PASS |
| `GET /api/career/insights` | 洞察列表 | `career/insights.list.json` | backend-ready | query: `limit`,`offset`,`category`,… |
| `GET /api/career/insights/<role_name>` | 单角色洞察 | `career/insights.detail.json` | backend-ready | |
| `GET /api/career/explore` | 探索搜索 | `career/explore.json` | backend-ready | `q`,`top_k` |
| `GET /api/career/skill-tree` | 技能树 | `career/skill-tree.json` | backend-ready | |
| `GET /api/career/learning-path` | 学习路径列表 | `career/learning-path.list.json` | backend-ready | |
| `GET /api/career/paths` | 晋升路径 | `career/paths.json` | backend-ready | |
| `GET /api/career/transfer` | 转岗 | `career/transfer.json` | backend-ready | |
| `POST /api/career/assessment` | 技能自评提交 | `career/assessment.create.json` | backend-ready | 后端已接受当前 Exams 的弱项 `string[]` + 0–100 答题分数，按 `gap_only/partial` 保存；不伪造技能等级或方向推荐，strict HTTP 仍须正式登录 |
| `GET /api/career/assessment/<student_id>` | 最新自评 | `career/assessment.latest.json` | backend-ready | |
| `GET /api/career/role-intro?role=` | 能力图谱角色简介 | `ability/role-intro.frontend.json` | backend-ready | 拼 `career_insights`；无匹配 `data:null`；无 Python 业务兜底文案 |

---

## profile

| Method Path | 用途 | Fixture | BE 状态 | 备注 |
|-------------|------|---------|---------|------|
| `GET /api/profile/student/<student_id>/4d` | 四维画像 | `profile/student.4d.student_001.json` | backend-ready | smoke PASS |
| `GET /api/profile/student/<student_id>` | 学生档案 | `profile/student.detail.json` | backend-ready | |
| `PATCH /api/profile/student/<student_id>` | 更新账号资料 | — | backend-ready | user_account + 可选同步 student_profile 列 |
| `GET /api/profile/student/<student_id>/basic` | 基础字段 | `profile/student.basic.json` | backend-ready | |
| `GET /api/profile/job/<portrait_id>` | 岗位详情 | `profile/job.job_001.json` | field-mismatch | 可评分发布快照与 `source_csb_` 来源原文是两层；后者 `portrait_json:null`、`match_eligible:false`，不承诺 fixture 的四维权重/title 字段 |
| `GET /api/profile/job/list` | 岗位列表 | `profile/job.list.json` | field-mismatch | 可评分发布摘要及独立 `source_only` 目录；来源后台核验不可用时 `status=partial`、`failed_sources`，仍保留已发布候选，页面必须提示不完整并区分不可评分/匹配；旧画像行不公开 |
| `GET /api/profile/job/search` | 岗位搜索 | `profile/job.search.json` | field-mismatch | 仅按已审核岗位名搜索；来源专用岗位不可评分；不公开旧 job_posting 的公司/分类/薪资 |
| `POST /api/profile/student/upload` | 简历上传触 L0 | — | heavy-excluded | 真解析管线 |

---

## favorites

| Method Path | 用途 | Fixture | BE 状态 | 备注 |
|-------------|------|---------|---------|------|
| `GET /api/saved-jobs?user_id=` | 心仪岗位列表 | `favorites/saved-jobs.list.json` | backend-ready | FE 类型 camelCase → adapter |
| `POST /api/saved-jobs` | 收藏岗位 | `favorites/saved-jobs.create.ok.json` | backend-ready | body: snake_case 权威；亦接受 camelCase |
| `DELETE /api/saved-jobs/<job_id>` | 取消收藏 | `favorites/saved-jobs.delete.ok.json` | backend-ready | |
| `GET /api/target-roles?user_id=` | 目标方向列表 | `favorites/target-roles.list.json` | backend-ready | |
| `POST /api/target-roles` | 添加目标方向 | `favorites/target-roles.create.ok.json` | backend-ready | 权威 `role_name`；兼容 `role` |
| `DELETE /api/target-roles/<role>?user_id=` | 删除方向 | `favorites/target-roles.delete.ok.json` | backend-ready | |
| `GET /api/target-role-market/<role>` | 方向市场卡片 | `favorites/role-market.<role>.json` | backend-ready | 返回 FE 全字段；无数据时 `data: null` |

---

## report

| Method Path | 用途 | Fixture | BE 状态 | 备注 |
|-------------|------|---------|---------|------|
| `GET /api/report/list?student_id=` | 报告列表 | `report/list.student_001.json` | backend-ready | **非** `/api/reports` |
| `GET /api/report/<report_id>` | 报告详情 | `report/detail.report_001.json` | backend-ready | |
| `GET /api/report/<report_id>/export?format=` | 导出 | — | backend-ready | |
| `GET /api/report/<report_id>/progress` | 生成进度 | — | backend-ready | |
| `POST /api/report/generate` | 触发生成 | — | heavy-excluded | DEMO_MODE=false 触 L4；`source_csb_` 返回 `409 job_source_only_unscored`，不建报告 |

前端 stub 曾写 `/api/reports` + `userId` → 理想契约已统一为上表（`GET /api/report/list?student_id=`）。

---

## agent

| Method Path | 用途 | Fixture | BE 状态 | 备注 |
|-------------|------|---------|---------|------|
| `POST /api/agent/chat` | AI 助手对话 | `agent/chat.demo.json` | backend-ready | body 含 `mode: "demo"` 可测 |
| `POST /api/agent/chat`（langgraph/react） | 真推理 | — | heavy-excluded | |
| `POST /api/agent/chat/stream` | SSE | — | heavy-excluded | 无 demo 短路时偏重 |
| `POST /api/agent/session/create` | 建会话 | `agent/session.create.json` | backend-ready | |
| `GET /api/agent/session/<id>/history` | 历史 | `agent/session.history.json` | backend-ready | |
| `POST /api/agent/career-suggestions` | 成长建议 | — | backend-ready | 真 LLM；余额不足 503 |
| `POST /api/ai/growth-analysis` | 报告成长分析 | — | backend-ready | 真 LLM；余额不足 503 |

---

## learning

| Method Path | 用途 | Fixture | BE 状态 | 备注 |
|-------------|------|---------|---------|------|
| `GET /api/learning/courses` | 课程列表 | `learning/courses.list.json` | backend-ready | |
| `GET /api/learning/courses/<course_id>` | 课程详情 | `learning/courses.course_001.json` | backend-ready | |
| `GET /api/learning/learning/progress/<user_id>` | 进度列表 | `learning/progress.list.json` | backend-ready | 路径双 `learning` 非笔误 |
| `POST /api/learning/learning/progress` | 写进度 | `learning/progress.write.ok.json` | backend-ready | |
| `GET /api/learning/recommend` | 推荐课 | `learning/recommend.json` | backend-ready | |
| `GET /api/learning/learning/report/<user_id>` | 学习统计 | `learning/report.json` | backend-ready | |

章节视频流等 → `backend-gap`（见学习中心页）。

---

## pipeline

| Method Path | 用途 | Fixture | BE 状态 | 备注 |
|-------------|------|---------|---------|------|
| `GET /api/pipeline/list` | 管线目录 | `pipeline/list.json` | backend-ready | |
| `POST /api/pipeline/ext/parse/text` | 粘贴简历解析 | `pipeline/parse.text.json` | backend-ready | 职途导航 / 简历页轻量 |
| `POST /api/pipeline/ext/parse/upload/resume` | 文件抽文本 | — | backend-ready | 偏 I/O，demo 可用 fixture |
| `POST /api/pipeline/trigger` | 旧管线启动 | — | backend-gap | strict 模式执行器未接入，503 且不建任务；demo 仅 partial 预览 |
| `GET /api/pipeline/status/<task_id>` | 任务状态 | `pipeline/status.sample.json` | backend-ready | 读状态可测 |
| `POST /api/pipeline/ext/exams` | 试卷提交 | `pipeline/exams.create.json` | backend-ready | 前端未接 |
| `GET /api/pipeline/ext/exams/<id>` | 试卷结果 | `pipeline/exams.detail.json` | backend-ready | |

---

## match

| Method Path | 用途 | Fixture | BE 状态 | 备注 |
|-------------|------|---------|---------|------|
| `POST /api/match/run` | 人岗匹配 | — | heavy-excluded | 视实现可能偏重；分数确定性 |
| `GET /api/match/result/<student_id>/<job_portrait_id>` | 匹配结果 | `match/result.json` | backend-ready | 权威分数 |
| `GET /api/match/skill-gap/...` | 技能缺口 | `match/skill-gap.json` | backend-ready | 含 evidence / keyword_coverage |
| `GET /api/match/explain/<student_id>/<job_portrait_id>` | 可解释匹配 | `match/explain.json` | backend-ready | **不计分**；只读 total_score |
| `POST /api/match/hybrid-candidates` | Hybrid 召回 | `match/hybrid-candidates.json` | field-mismatch | RRF 只融合当前已发布且摘要有效岗位及公共职业方向；岗位带 `candidate_kind=published_job`/版本/摘要，方向带 `candidate_kind=role`、`match_eligible=false`；旧 fixture 的 `job_001` 不证明已发布，真实目录为空时可仅返回方向。`retrieve_score` 不是匹配分 |
| `POST /api/pipeline/ext/vector/search` | 公共向量检索 | — | backend-ready | JD 命中只返回当前已发布岗位投影；技能命中须绑定当前 MySQL taxonomy 结构/摘要，污染行过滤并标 `partial`；私有 resume/conversation 403 |
| `GET /api/match/career-recommendation/<student_id>` | 职业路径参考 | — | field-mismatch | strict 模式只基于已验证 L3 岗位和公共图谱给**通用**路径，`recommended_dir=null`、`personalized_recommendation_verified=false`；无 L3 为 404，撤回为 409；旧个性化推荐契约不可直接接用 |

---

## admin

| Method Path | 用途 | Fixture | BE 状态 | 备注 |
|-------------|------|---------|---------|------|
| `GET /api/admin/dataset` | 岗位数据集列表 | `admin/dataset.list.json` | backend-ready | |
| `POST /api/admin/dataset/upload` | JSON 创建元数据；multipart 私有来源暂存 | `admin/dataset.upload.ok.json` | field-mismatch | 当前前端仍用 JSON metadata；multipart JSON 文件仅写私有来源与 staged_record_count，真实 count=0、pending、content_ingested=false，不发布岗位或建图 |
| `DELETE /api/admin/dataset/<id>` | 删除 | `admin/dataset.delete.ok.json` | backend-ready | |
| `GET /api/admin/knowledge-base` | KB 列表 | `admin/kb.list.json` | backend-ready | |
| `POST /api/admin/knowledge-base/upload` | JSON 创建元数据；multipart 私有文本暂存 | `admin/kb.upload.ok.json` | field-mismatch | 当前前端仍用 JSON metadata；multipart TXT 文件可私有暂存并管理员回读，pending、content_ingested=false；旧 fixture 不代表正文入图谱 |
| `DELETE /api/admin/knowledge-base/<id>` | 删除 | `admin/kb.delete.ok.json` | backend-ready | |
| `POST /api/admin/knowledge-base/smart-upload` | 智能入库 | — | heavy-excluded | 含解析 |
| `POST /api/admin/pipeline/build` | 管理端建图 | — | backend-gap | 来源内容与执行器未接入，503 且不建任务 |
| `GET /api/admin/job-reviews`、`/<review_id>` | 管理员回看模型岗位审查及阻断原因 | — | backend-ready | 真 HTTP + MySQL/Redis 已验；仅只读模型审计，现存五条均 blocked，不能直接发布；页面未接线 |
| `POST /api/admin/job-source-listings`、`POST /<id>/revoke` | 精确核对官方原文后独立展示/撤回不可评分岗位 | — | backend-ready | 真 HTTP + MySQL/Redis 已验；必须正式管理员+CSRF；`source_csb_` 不参与匹配/报告，页面未接线；见 `docs/specs/source-only-job-catalog.md` |
| `GET /api/admin/job-source-listings`、`/<id>` | 回看来源专用岗位版本及原文 | — | backend-ready | 含已撤回版本；仅正式管理员；响应不缓存；页面未接线 |
| `POST /api/admin/job-publications/preview`、`POST /api/admin/job-publications` | 账号管理员预检、人工确认并发布精确岗位快照 | — | backend-ready | 真 HTTP + MySQL/Redis 已验；需要已登录 admin、CSRF、来源逐字段证据和明确确认；页面未接线，不等于自动核验招聘真实性 |
| `GET /api/admin/job-publications`、`/<id>`、`POST /<id>/revoke` | 发布版本回看和精确撤回 | — | backend-ready | 仅正式管理员；撤回后公共目录与 L3/L4 不回退旧版本 |

---

## ability-graph / course-system

| Method Path | 用途 | Fixture | BE 状态 | 备注 |
|-------------|------|---------|---------|------|
| `GET /api/ability-graph?role=` | 能力图谱 | `ability/graph.json` | backend-ready | 导入：`POST /api/admin/graph/import-ability-course` |
| `GET /api/learning/course-system-graph?role=&variant=` | 课程体系图 | `learning/course-system.graph.json` | backend-ready | 同 Neo4j |
| `POST /api/admin/graph/generate-ability-course` | MySQL 投影生成 | — | backend-ready | body `{role}` |

---

## messages / profile-ui / home

| Method Path | 用途 | Fixture | BE 状态 | 备注 |
|-------------|------|---------|---------|------|
| `GET /api/messages?user_id=` | 消息列表 | `home/messages.list.json` | backend-ready | 共享公告 + 按用户已读/删除 |
| `POST /api/messages/<id>/read` | 标记已读 | — | backend-ready | query/body `user_id`，缺省 `student_001` |
| `DELETE /api/messages/<id>` | 软删除 | — | backend-ready | 仅对该 user 隐藏 |
| 用户资料更新 API | 个人中心 | — | backend-gap | |
| 首页专用聚合 API | 首页 | — | backend-gap（可组合现有 API，无独立端点） | |

---

## 已知 field-mismatch 清单

**已修复（2026-07-18）**，下列项现标 `backend-ready`：

1. ~~target-roles POST `role` vs `role_name`~~ → BE 兼容两者；FE 发 `role_name`
2. ~~saved-jobs camelCase~~ → BE 接受 camelCase 别名；FE adapter 仍发 snake_case
3. target-role-market 已由 BE 保留方向技能、显式标市场字段未核验；当前 FE `mergeTargetRoleMarket` 仍会用 mock 市场数值回填不可用字段，真实体验尚未闭合
4. ~~report `/api/reports`~~ → FE 已改 `/api/report/list?student_id=`

**2026-10-14 后端接收边界**：Exams 页面提交的 `assessed_skills` 是弱项名称 `string[]`；后端已以 `gap_only/partial` 接收并保存，缺少逐项掌握等级时不生成“已掌握技能”或技能式职业推荐。当前页面仍需正式登录后才可在 strict 模式写入，前端本地答题与持久结果展示不是仅凭后端返回 200 就完成的全链验收。
