# 次要后端入口体验验收：第一轮

日期：2026-10-13。状态：**多处经真实请求复现的次要入口缺陷已修复；全量平台后端仍未完成验收**。

## 范围与发现

- 被动盘点当前 Flask 应用工厂的 15 个 blueprint 注册点，并查看 `roles`、`messages`、`favorites`、`ability` 的路由实现及前端对收藏、消息、能力图的实际调用路径。这是入口导航，不是 15 个域的功能验收。
- 首个已复现缺陷：实际注册路径 `/api/saved-jobs`、`/api/target-roles` 的 POST，在认证通过后收到非 JSON、损坏 JSON、或顶层数字 JSON 时进入通用异常分支，返回 HTTP 500。`role_name` 为数组也缺少类型门禁。错误请求不应被描述成服务器故障，更不应触达写库。
- 最小修复：两个写入口只接受 JSON 对象；目标职业名必须为非空文本。无效输入统一返回 HTTP 400、`success:false`，在调用 repository 前停止。没有修改 Agent 主链或扩展 workspace 能力。
- 第二处已复现缺陷：`/api/admin/python-info` 原先直接在应用工厂注册，未经过 `admin_bp.before_request` 的服务端管理员门禁。Strict 模式无 Principal 时，该管理诊断入口仍返回 HTTP 200 和 Python 可执行文件、版本、模块路径；相邻真正的 admin 路由返回 401。
- 最小修复：将原路径原方法迁入 `admin_bp`，复用现有的 `resolve_request_scope()` + `g.cpa_admin is True` 检查；不创建第二套管理员身份规则。管理员仍可读取诊断信息，匿名和普通 Principal 分别收到 401/403。

## 复现与复验

使用 README 指定的 `mkg` Python，从仓库根目录运行：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q `
  backend/tests/test_p0_admin_vector_scope.py `
  backend/tests/test_app_smoke.py `
  backend/tests/test_favorites_input_contract.py `
  backend/tests/test_historical_favorites_egress.py
```

- 修复前：新增契约测试 **7 failed**；两个路由的三种坏请求均为 500，loopback TCP 请求同样为 500。
- 修复后：邻接测试 **18 passed**（其中新增契约测试 8 passed，含 loopback TCP）；坏请求为 400，repository 写入 spy 未被调用，既有有效收藏写入与 ID 回读替身测试仍通过。
- 另用实际 `create_app()`、`CPA_DEV_AUTH=1` 的 loopback 临时服务，真实 HTTP 验证 `/health` 为 200，两个已注册 POST 路径在顶层数字 JSON 下均为 400、`success:false`。只用了临时测试身份和无效请求，没有写业务行。
- 管理诊断修复前：新增负测确认无 Principal 的 `/api/admin/python-info` 为 **200**，而同域受保护路由为 401。修复后：匿名 401、普通 Principal 403、服务器注入 `g.cpa_admin=True` 的管理员 200；实际 `create_app()` loopback TCP 复验匿名 401、Dev Principal 403。
- 两处修复合并后的邻接回归：`test_p0_admin_vector_scope.py`、`test_app_smoke.py`、`test_favorites_input_contract.py`、`test_historical_favorites_egress.py` 共 **28 passed**（含新加的实际 `create_app()` loopback TCP 管理拒绝测试）。`git diff --check` 无差异错误；Windows 的 LF/CRLF 提示不代表内容损坏。

## 验收边界与下一轮入口

- 本机本轮 Docker/MySQL 未运行；应用启动会记录 schema ensure 的连接拒绝。因此没有声称收藏/目标职业的真实 MySQL 写入、列表或删除全链已验收。当前修复的拒绝路径不依赖数据库，写库不可达由测试 spy 证明。
- 尝试运行消息域邻接集时，`test_messages_live_owner_state_and_verified_mutations` 在 `messages_repo.ensure_schema()` 处因 `localhost:13306` 拒绝连接而失败；这是本轮环境阻断，不记作消息功能缺陷或通过。管理员诊断拒绝路径同样不依赖数据库；实际服务启动虽记录 schema ensure 连接拒绝，HTTP 门禁仍已复验。
- 尚未逐项验证其余 blueprint、历史 `/api/pipeline/ext/*`、`/api/roles` 和图谱聚合。后续须逐路由判定公共/私有边界，并对私有资源追到 owner 与权威业务行；它们是**待验证范围**，不是本轮新发现的已证实缺陷。
- 下一轮优先从当前真实页面使用的收藏/消息/能力图等次要入口继续，选择能在实际请求和权威结果上重放的缺陷；若依赖未启动，先做可独立验证的拒绝/错误路径，不用替身成功冒充真实业务完成。

## Docker 恢复后的续验（同日）

本节追加于上面的“Docker 未运行”快照之后；不改写其当时的测试条件。MySQL、Neo4j、Redis 容器现在均为 healthy。

- 收藏与目标方向：新增 `backend/tests/test_favorites_live_http.py`。实际 `create_app()` loopback HTTP 依次执行 POST→GET→DELETE，MySQL 回读验证返回 ID 对应唯一 owner 行、另一 Principal 列表不含该行、删除后行消失；测试使用唯一 UUID owner/job/role，`finally` 精确清理。**1 passed**。这证明现有有效写链和前述坏请求门禁可并存，不代表岗位来源可信。
- 消息：`test_p0_messages_ability_scope.py` 经真 MySQL 验证 A/B owner 已读、软删除与未知 ID，测试自行清理；未发现确定缺陷。能力图：同文件经真 Neo4j 验证跨角色边隔离、别名命中和无匹配 404；未发现确定缺陷。该文件 **2 passed**，使用 Flask test client，不冒称独立服务 socket。
- 学习进度：`completed="false"` 原会穿透到 MySQL TINYINT 并形成 HTTP 500；现在非 bool 在 DB 前返回 400。真实有效 `completed=false` 的 POST 与 MySQL 行 `completed=0`、owner GET 均经验证，临时数据已清理。
- 技能自评：`assessed_skills=["not-a-skill"]` 原因元素缺少 `.get` 返回 500；现在列表项必须包含非空文本 `skill`/`level`，错误输入返回 400，真 MySQL 无行。有效结构化自评写入/owner 读取与跨 owner 403 经验证，临时数据已清理。学习/自评专用回归 **9 passed**。
- 职业探索：`top_k=-5` 原样进入 MySQL `LIMIT` 导致 500；现入库前限制为 1–20。真实依赖请求同输入返回 200、0 候选，不再发生负数 LIMIT 错误；边界回归含 -5、0、999。Role Inference 专用回归 **9 passed**。
- 全后端回归（Docker healthy 后运行）：`D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests -q --tb=short` → **903 passed, 28 skipped**，约 220 秒。绿灯证明当前测试集通过；28 项跳过、真实模型质量和未覆盖产品路径不被算作完成。

上述定点测试复跑命令（均从仓库根目录执行，先设 UTF-8 环境）：

```powershell
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q `
  backend/tests/test_favorites_live_http.py `
  backend/tests/test_p0_messages_ability_scope.py `
  backend/tests/test_career_assessment_scope.py `
  backend/tests/test_career_role_inference.py
```

### 尚未闭合的产品链路

1. **当前页面与自评接口不一致**：`cpa-xingtu/src/views/course/ExamsView.vue` 提交弱项名称 `string[]`，而后端 `/api/career/assessment` 需要 `[{skill,level}]`。真实 HTTP 模式会 400；不能把弱项暗中推断为某个自评等级。已在 `docs/frontend/cpa-xingtu/contracts/api-catalog.md` 与 `pages/exams.md` 改标 `field-mismatch`，待选定权威转换/主自评入口。
2. **真实登录未建立**：页面使用 mock 用户，后端 strict 模式要求服务器 Principal；DevAuth 只供 loopback 测试，不能当交付认证。已在 API catalog 撤销“产品接受无登录 API”的含混表述。
3. **L3/L4 真实用户链仍需到达**：真库只读确认 `job_catalog_publication`、`job_posting`、`job_portrait_4d`、`match_result`、`career_report` 均为 0。唯一临时 owner 调 `/api/match/run`、`/api/report/generate` 在缺已确认简历处先被拒绝，未生成成果；临时失败 task 已精确清理。下面的合成 fixture 已单独证明发布门禁，但不能替代真实简历与可信岗位进入链路。
4. **方向推断降级语义**：真实 Java/Spring Boot 技能请求返回 taxonomy 候选，但 Neo4j 主评分是否命中未证明。服务层目前可能把 Neo4j/MySQL 查询错误吞为无结果，HTTP 仍 `success:true`；需区分“真实无资料”与“依赖失败”，并定义公开 partial 契约后验证。

### 隔离发布门禁复验与第三轮次要入口

- 显式开启 `CPA_TEST_LIVE_MYSQL=1`，运行 `backend/tests/test_verified_match_report_mysql.py` 和 `test_job_catalog_listing.py`，**5 passed**。前者建立唯一合成 L0/L2 确认记录与已发布岗位，运行真实 L3/L4 规则和 MySQL 持久化；撤销发布后，旧匹配/报告 HTTP 读取为 409，新 L3/L4 返回 `catalog_publication_unavailable`，报告行数不增加。`finally` 按唯一 owner/岗位 ID 清理，随后只读确认上述五张业务表恢复为 0。这是**合成源和规则门禁**的证据，不是实际简历解析、岗位采集或模型质量验收。
- 历史自评入口 `/api/pipeline/ext/exams`：`answers=['bad']`、字符串 `level` 原返回 500；越界等级原可写出无效置信度。现每项要求非空文本 `skill`、0–5 整数 `level`，无效输入在触库前返回 400。有效提交经真 MySQL 回读 owner/弱项，另一 Principal 读取 404；临时行已清理。专用测试 **8 passed**。
- 管理员数据集/知识库元数据删除：不存在 ID 原返回 200 伪成功，现返回 404；已有记录删除后回读 MySQL 确认消失。新增真实 HTTP+MySQL 测试同时验证上传 ID、写入失败无行、普通 Principal 403 和精确清理；与管理员相邻测试合计 **9 passed**。这只验元数据接口，不代表文件内容、索引构建或知识库检索可用。
- 学生基础信息 PATCH：`sync_student_profile="false"` 原按 Python truthiness 当成真，可能意外同步；现只接受 JSON 布尔值，其他类型 400 且权威账号/档案行不变。A/B Principal 的自有读取、跨 owner 403 和写入不越权也经真 MySQL 验证；专用测试 **2 passed**。岗位列表/搜索仅有空目录 200，非空投影和发布筛选尚未验收。
- 本轮上述改动的邻接联合测试：`test_admin_catalog_metadata_mysql.py`、`test_p0_admin_vector_scope.py`、`test_p0_legacy_ext_scope.py`、`test_profile_basic_live_scope.py`、`test_favorites_live_http.py`、`test_career_assessment_scope.py`、`test_career_role_inference.py`，**38 passed**。第三轮改动后的全量复跑：`D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests -q --tb=short` → **908 passed, 28 skipped, 9 warnings**，约 217 秒。跳过项及未覆盖的用户链仍不算验收。

本节属于持续推进中的中间证据。完成“平台想要的所有后端功能”仍需逐域合同、权威数据、真实身份和用户旅程验收，不以本轮八处修复、合成门禁或测试绿灯替代。

## 第四轮：公开目录、学习报告与只读图谱

- `GET /api/profile/job/list`、`/<id>`、`/search`、`/categories` 原分别直读旧 `job_portrait_4d`、`job_posting`，绕过已有 `job_catalog_publication` 来源审核/发布门禁。用唯一已发布快照、未发布画像和带标记的原始岗位经真 MySQL→HTTP 复现：未发布画像出现在列表且详情 200，搜索回显未发布岗位/公司，分类回显未发布类别。现列表/搜索复用发布目录校验，详情按 ID 重查当前已发布快照，最新版撤回后不回退；原始行隐藏。发布快照没有可信分类字段，分类返回空及明确 reason，不从旧库猜测。真实唯一夹具按 publication、portrait、posting ID 精确清理。岗位专用与邻接测试 **6 passed, 1 skipped**；跳过的是另一个显式 opt-in 测试。前端 fixture 中的四维权重/公司/分类仍与新公开合同不一致，已在前端合同文档标记 `field-mismatch`；不是页面接通验收。
- `GET /api/learning/learning/report/<owner>` 的 `total_study_time` 原把百分比进度直接乘以章节小时。真 MySQL 唯一章节 2 小时、进度 25% 时，旧 HTTP 返回 50.0，修后返回 0.5 小时；临时数据已清理。专用回归 **1 passed**。`completed_courses` 的产品语义尚未验收。
- 公共 `/api/graph/query` `career_path` 模板的非法 `params` 原进入异常并返回 500；现模板字段集合及 1–200 字符岗位名在访问 Neo4j 前校验，六种坏输入返回 400。后续同样复现并修复非对象顶层 JSON/非文本 `template` 的 500，三种坏输入返回 400。用唯一 Neo4j `CareerNode` 路径及混合 `CareerNode:Person` 节点经实际 `create_app()` 验证只返回公开标量路径、不回显私有 marker，空路径保持空结果；节点 `finally` 精确删除并回读为 0。图谱邻接测试曾 **14 passed**，加入顶层 JSON 回归后与其他本轮测试合跑 **34 passed, 1 skipped**。未验图谱写链或 Agent chat。
- `/api/career/infer-role` 非对象顶层 JSON 原 500；现请求体非 JSON 对象在数据库/Neo4j 前返回 400。新增测试旧版红、新版绿，Role Inference 文件 **10 passed**。这不解决前述依赖失败被吞后返回 `success:true` 的降级语义问题。
- 角色配置只读链 `list → detail → greeting → knowledge` 经 Flask test client 与仓库内 YAML 比对，未知角色/主题 404，未发现可复现缺陷；没有因“必须找问题”而改代码。未覆盖 `/api/roles/chat`。

以上仍是中间证据；岗位真实采集/审核、正式认证、自评页面合同、角色聊天与真实用户 L0→L4 没有因此完成。

第四轮代码落地后的全后端复跑：`D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests -q --tb=short` → **921 passed, 28 skipped, 9 warnings**，约 220 秒。由于全量运行期间把岗位专用测试改为完整 `create_app()`，随后对该文件独立再跑 **1 passed**，确认实际应用工厂注册路径仍通过。`git diff --check` 无差异错误。最后只读 MySQL 回查 `job_catalog_publication`、`job_posting`、`job_portrait_4d`、`match_result`、`career_report`、`job_dataset`、`knowledge_base` 均为 0 行；本轮唯一夹具无这些表的残留。28 项跳过与正式产品身份/来源缺口不被 921 项绿灯抵消。

## 第五轮：审核状态、来源降级与更长的 HTTP 链

- OOV 管理审核：真实 MySQL→`create_app()` 红测证实未知 ID `reject` 原 200 伪成功，终态可反向审批或重复批准并插第二条规范实体。现 `pending → approved/rejected` 在行锁事务中原子完成；未知 ID 404，终态/重放 409；规范实体插入与状态更新同事务。又验证请求体 `reviewed_by=forged-operator` 原会写成审计人；现审计人取服务端 Principal ID，伪造字段被忽略。请求体形状、`canonical_name`、`aliases`、`category`、`reason` 的非法类型在 MySQL 前返回 400，不再触发 500。唯一两条 OOV 与生成实体在 `finally` 精确清理并回读 0；普通 Principal 403。相关测试 **14 passed**。
- 用户确认保留方向推断的可用候选，但在 Neo4j 或 MySQL 查询失败时标为部分结果。原接口把依赖故障吞为空数据，甚至在全部 MySQL 查询失败时与合法无匹配一样返回 `success:true`、空候选。现 `status=complete|empty|partial`、`source_coverage`、`failed_sources` 和重试指引区分结果；`success:true` 仅表示本次请求完成，调用方必须检查 `status`。真 MySQL/Neo4j 读路径加单来源故障注入的 HTTP opt-in 测试 **1 passed**，纯服务/入口测试 **12 passed**。当前真实图谱 `CareerNode-[:REQUIRES]->Skill` 为 0，不能宣称图谱主信号质量已验收。前端当前忽略 `partial`，文档已标 `field-mismatch`；见 `docs/guides/career-role-inference-source-coverage.md`。
- `POST /api/ai/growth-analysis` 的顶层数组/数字 JSON 原在 `.get` 处返回 500；现认证通过后要求 JSON 对象，非法输入 400 且不读取 MySQL。旧版红测，新版与来源邻接测试 **21 passed**。没有调用真实 LLM，未宣称生成质量。
- 在前轮合成 L3/L4 规则/发布门禁之外，新增显式 opt-in 的**实际 HTTP 写链**：唯一已确认 L0/L2、已发布岗位经 `/api/match/run` → 匹配读取 → `/api/report/generate` → 报告读取，MySQL 各回读一条 owner 行、D1 学历硬约束使结果 `partial`；请求体伪造 owner/报告 ID 无效，另一 Principal 403/404。撤销发布后旧读取 409，新 L3/L4 不产生 ID 或新报告行。`backend/tests/test_match_report_http_mysql.py` **1 passed**，fixture 逐表回读无残留。这仍是合成简历事实，不是真实解析/岗位采集。
- 管线任务读取：唯一 `pipeline_task` 从 pending→running 触发真实 MySQL 事件，`GET /api/pipeline/status/<id>` 与 `/events/<id>` 经完整 Flask 路由验证 owner、投影、未知 ID/他人 404、负 cursor 400，私有结果不流入事件；`backend/tests/test_pipeline_task_read_http_mysql.py` 显式 opt-in **1 passed**，两表精确清理回读 0。未验证 trigger 首次迁移或任务投递可靠性。
- 课程详情和列表/推荐：真 MySQL 唯一课程、章节、路径经完整请求验证非空字段、章节顺序、分类分页、弱技能/目标岗位推荐及热门兜底，未发现确定缺陷，故没有为了凑修复而改代码。历史自评补荐、极限分页和大目录性能仍未覆盖。

第五轮定点联合复验（显式 `CPA_TEST_LIVE_MYSQL=1`，含上述 OOV、Growth、Infer Role、L3/L4、Pipeline 读专用测试）**34 passed**。全量统计以第五轮改动后复跑结果为准；通过数量不替代真实模型、身份、来源与页面端到端体验验收。

随后补验 OOV 待审核列表的真实非空分页：原 `admin.py` 使用未导入的 `_iso`，非空结果直接 500；补齐复用共享时间格式化函数后，红测进一步揭示 `entity_type=skill` 列表已过滤，但 `total` 仍统计全部待审类型（唯一 skill+major 两条时返回 2 而应为 1）。现计数查询复用同一类型条件，唯一夹具清理回读 0；OOV/管理员邻接测试 **15 passed**。这次修复发生在第五轮全量 931 项复跑之后，须由下一次全量结果覆盖旧统计。

## 第六轮：不能消费的任务不得假装已受理

- strict 模式真实 Flask 请求和 MySQL 复现旧 `/api/pipeline/trigger`、`/l1..l4` 五个启动入口：200/pending，数据库进度 0、无结果/错误，等待后仍不变。管理员 `/api/admin/pipeline/build` 同样 200/pending、没有投递，且原 `next_steps` 指向普通 owner 状态入口，管理员任务无 student_id，故该链接为 404。管理员/学生合成任务由审计 Agent 按唯一 ID 精确清理；这些入口没有当前页面调用证据，当前页面 L2 用 `/api/profile/student/pipeline` 同步执行，不在本次改动范围。
- 建立六入口失败测试，旧版六项均因测试 spy 揭示原路由会写库而红灯 500。现在旧 strict 启动入口在认证/最小校验后统一返回 503、不生成 ID、不碰任务表；Demo `/trigger` 继续只返回 `partial` 预览。新增 `GET /api/pipeline/list` 的 `trigger_mode` 显式指示 demo 预览或不可用。管理员入口不再将单纯元数据 ID 写成假建图任务。`CPA_TEST_LIVE_MYSQL=1` 对唯一 owner 的六次完整 Flask 请求逐次检查真实 `pipeline_task` 仍为 0 行；与 OOV/管理员邻接合计 **22 passed**。
- 本次是**结果真实性修复，不是异步构建完成**。真正重启需先定义可信来源、按现有任务 ID 执行、Celery worker/outbox 与幂等恢复、终态权威回读；详见 `docs/guides/pipeline-dispatch-boundary.md`。本机 OpenCode/Codex 快照与现有 Celery 的参考版本、模块和适配边界也记录在该指南。存量 pending 未自动清理，因为它们可能来自其他真实同步管线的在途状态，不能按状态一刀切改写。

第六轮后全后端复跑：`D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests -q --tb=short` → **938 passed, 32 skipped, 9 warnings**（约 4 分 23 秒）。显式 `CPA_TEST_LIVE_MYSQL=1` 的 OOV、推断来源、Growth、L3/L4、Pipeline 读取及六入口不建假任务的联合测试 → **42 passed**。32 个默认跳过项和未接真实工作队列、正式认证、岗位来源仍不能算完成；旧版 931 项全量统计已由此次复跑覆盖。

## 第七轮：当前真实 L2 与次要写入口

- 当前页面使用的 `POST /api/profile/student/pipeline` 无 `job_id` 重复运行 L2 时，MySQL `(student_id,target_job_id)` 唯一键允许多个 `NULL`，旧 HTTP 两次均报 completed，却落两条无目标岗位画像、返回不同 ID。新增显式 opt-in 测试用唯一已确认简历夹具、确定性画像替身、真实 Flask + MySQL 红测 **1 failed**（2 行），修后 **1 passed**（同一画像 ID、第二次分数及来源 task 已回读更新）。写入前短事务锁已确认 profile 行并复核来源，避免模型生成期间简历被替换后落过期画像；旧历史重复行不自动删除。与 L0/L2、L3 来源门禁邻接合跑 **27 passed**。证据与开源适配边界见 `docs/guides/resume-confirmation-golden-slice.md`。模型是替身，绝不记作真实评分质量验收。
- 同一当前页面入口的顶层数组/数字/字符串 JSON，以及 `job_id`、`job_portrait_id`、`title` 的数组/对象值，旧版会在调用执行器前返回 500 或穿透为失败任务；现在认证后、建任务前返回 400。六个参数化用例均先红后绿，连同 L0/L2 邻接测试 **21 passed**。这只是输入边界，不等于真实简历/模型全链验收。
- 并行真实 MySQL 审计发现学习报告只要有一章进度，即把该课程算作“已完成”：50%、`completed=0` 时旧 HTTP 报 `completed_courses=1`。现只统计显式 `completed=1` 的课程；学习小时仍按百分比换算，独立专用测试 **2 passed**，唯一课程/章节/进度夹具清理。见 `docs/reports/2026-10-13-learning-report-completion-evidence.md`。
- 管理员数据集/知识库“上传”实际上只写元数据，却原样接受客户端 `status=completed`，真 MySQL 列表也显示已完成。现新记录状态固定 `pending`，两类资源的唯一 ID 经完整 Flask 请求→MySQL→列表回读均验证，测试 **4 passed**（含原元数据邻接测试），精确清理。这**没有**实现内容上传/解析/建图；管理页文档明确标注。
- 数据集还会把客户端自报 `count=1` 当作已入库岗位数；红测真 MySQL 仍有 1，尽管没有任何岗位内容。现元数据新增行的实际 `count=0`，响应明示 `content_ingested=false`、`status=pending`，不再称“上传成功”。两类上传的非对象、缺失/非文本 title、非法分类/类型原可触库或 500，现写库前 400；八个参数化负测先红后绿，连同元数据/管理员邻接 **19 passed**。客户端原样提交的预估数量尚无独立声明字段，不暗中占用真实 `count`。

第七轮改动后全后端复跑：`D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests -q --tb=short` → **954 passed, 34 skipped, 9 warnings**（约 3 分 51 秒）。显式开启 `CPA_TEST_LIVE_MYSQL=1` 的本轮 L2、学习报告、管理员元数据与邻接联合复跑 → **36 passed**。L2 画像生成器仍是确定性替身；真实 MySQL/Flask 的写入身份、行数、ID 与结果来源已验，真实模型质量、并发恢复、正式认证和异步执行仍未验收。

## 第八轮：完成状态与画像原子性、目录并发读取、确认输入

- 在第七轮 L2 重复写入修复之后，新增真实 Flask/MySQL 故障注入：连续两次生成画像后，第三次在 `pipeline_task` 完成状态写入处失败。旧实现先提交画像，后写任务，导致 HTTP 500、失败任务，却把第二次可信画像覆盖为第三次结果；红测分别覆盖无 `job_id` 和有 `job_id`。现真实 MySQL 路径把画像写入/回读、完成任务更新和状态事件触发器放入同一事务；第三次故障时画像维持第二次 ID、分数与来源任务，失败任务事件为 pending→running→failed。两种目标情况 **2 passed**；测试 `finally` 清理画像、任务、事件、资料并逐表回读 0。修测试前遗留的本轮唯一 `l2_null_` 事件共 95 行，经确认无对应资料、画像或任务后精确清理，回读 0。画像生成器是确定性替身，不能据此宣称真实评分、进程崩溃恢复或历史重复行迁移完成。
- 发布目录原先先读版本标记，再组装页面；另一岗位在其间发布/撤回可让结果携带旧 `catalog_version`，形成混合时间点。现组装后重读标记，若变化返回 `unavailable/catalog_changed_during_read` 和空 items，而非假称当前版本；单页竞态回归及真实 MySQL 发布/撤回邻接测试通过。该重检不把跨分页请求变成事务快照；调用方仍要在分页间比对版本。
- `/api/profile/student/confirm` 顶层 JSON 数组原会在 `.get` 处 500，现为 400。真实 MySQL 确认并发/重复/故障测试 `CPA_TEST_PIPELINE_MYSQL=1` → **2 passed**；它验证旧事务边界未被本轮输入修复破坏。定点联合测试 `CPA_TEST_LIVE_MYSQL=1` 下 L2、目录与简历金路径 → **22 passed**；最后补加成功任务事件断言后，L2+目录单独再验 **7 passed**。

本轮所有改动后的全后端复跑：`D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests -q --tb=short` → **955 passed, 35 skipped, 9 warnings**（约 3 分 50 秒）；`git diff --check` 无差异错误。增加的 skip 是显式 opt-in 的真库参数化测试，不是隐藏失败。产品级缺口仍是正式认证与用户来源、真实简历/模型质量、可信岗位供给、可持久恢复的异步执行，以及前端旧 fixture 与当前后端合同对齐；全绿不等于这些链路已可用。

## 第九轮：L3/L4 业务结果与任务状态一致性

- 真实 Flask + MySQL 故障注入先证明 L3 旧问题：同一 owner/岗位第二次匹配写入已经覆盖 `match_result` 的 `source_match_task_id`，但随后任务 `partial` 状态写入失败，HTTP 500、任务 failed，原先可信匹配却被失败任务污染。L4 同样先 INSERT 报告再单独提交任务状态，存在孤儿报告窗口。现在真实 MySQL 的 L3/L4 将领域写入、owner/内容回读、任务终态和数据库状态事件放在同一事务；状态写入失败时匹配仍保持上次快照，L4 新报告不留行。测试监听到两处领域 INSERT 已触达后才注入故障，并核对两个失败任务的事件均为 pending→running→failed。`CPA_TEST_LIVE_MYSQL=1` 的 HTTP、真库及来源门禁联合测试 **28 passed**。此验收不证明并发确认/发布撤回与匹配重算之间的强串行化；见 `docs/guides/match-report-source-gate.md`。
- 并行审计岗位与消息入口：已发布岗位的真实 MySQL→公开 HTTP 读取测试 **1 passed**，但发布由测试直接调用 `publish_job()`；管理端尚无人工审核/发布 HTTP 路由，元数据上传也不等于岗位内容导入，不能称完整“审核→发布→公开读取”用户链。消息中心真实 Flask→MySQL 的 owner 隔离/已读/删除测试 **2 passed**，未发现可证实的缺陷；未读数由前端按消息 `read` 字段计算，现有测试未覆盖真实 socket 的统计体验。两处审计没有为了制造绿灯而添加虚假发布或未读接口。
- 全套测试第一次复跑遇到系统盘 `C:` 可用空间为 **0**，pytest 在 `C:\Users\HoSheil\AppData\Local\Temp\pytest-of-HoSheil` 无法创建临时目录，产生大量环境性 setup 错误；没有删除其他目录的数据。仅将该次测试进程的 `TEMP`、`TMP`、`TMPDIR` 指向工作区已有 `output/` 后原样完整复跑：**955 passed, 35 skipped, 9 warnings**（约 4 分 20 秒）。旧版测试未清理的本链唯一前缀 `http_l3l4_` 状态事件 47 行，经核对其资料、画像、任务、匹配、报告及发布记录均为 0 后清理，事件回读 0；新测试已在 `finally` 精确清理事件。

## 第十轮：来源初检后的撤回与匹配/报告输入边界

- 真 Flask/MySQL 故障时序复现：L3 已在初检读到有效发布记录，但在写 `match_result` 之前另一请求把该记录撤回；旧流程仍返回 `verified=true` 和规则估计，并把新匹配行归于已失效来源。真实 opt-in 红测观察到 `scoring_policy_not_validated`，而预期是来源不可用。L4 存在同一时间差。现真实 MySQL 的领域事务先锁 owner 已确认资料、当前岗位发布行（L4 还锁当前匹配行），用同一连接重新核对简历、画像、发布版本和引用；若撤回或版本变化，只保留 `partial` 阻断任务，不覆盖旧匹配、不插新报告。真库测试分别在初检后撤回，并回读业务行未被污染；这不等于高并发压力或所有旁路写者的全局串行化。
- `/api/match/run`、`/api/report/generate` 的认证后顶层非对象 JSON 和非法岗位 ID 原会 500 或进入 Runner/生成失败任务。并行子任务以 **16/16 红测**复现，修后先绑定 Principal，再要求 JSON 对象和与 MySQL 发布表 `VARCHAR(64)` 对齐的非空岗位 ID；非法输入在创建任务、报告 ID 及业务持久化前返回 400。拒绝链测试用 Flask `test_client` 和 Runner 哨兵证明未触达写路径，不将其冒充真库成功生成验收。合同见 `docs/guides/match-report-request-contract.md`。
- 岗位发布写入口审计再次确认不可把模型审查结果或客户端 `review_state=approved/reviewed_by` 直接当作人工授权：当前应用内没有明确注入 `g.cpa_admin` 的正式认证适配器，也没有通用来源真实性核验和审批摘要绑定协议。子任务没有为了“开通功能”而新增可伪造审批的 HTTP 路由。真实完整审核→发布→读取依然是产品级未闭合链路；需先定义服务端操作者身份与审批凭据。已有 CLI/发布服务不等于页面 HTTP 操作完成。

本轮 L3/L4 真库、输入合同与来源门禁联合复验（`CPA_TEST_LIVE_MYSQL=1`）**46 passed**。完整后端测试在 `mkg` 环境、`TEMP/TMP/TMPDIR` 指向工作区 `output/` 后：**973 passed, 35 skipped, 9 warnings**（约 4 分 02 秒）。真库唯一测试前缀 `http_l3l4_` 的资料、画像、任务、事件、匹配、报告、发布记录再次回读均为 0。正式认证、人工发布工作流、真实简历/模型评分质量、来源撤回高并发与可靠后台消费仍未完成。

## 第十一轮：旧读侧来源回查与 L3 旁路封闭（进行中）

- 已有详情/导出对岗位撤回返回 `409 stale`，但匹配列表、技能差距、匹配解释以及报告列表、进度、任务仍可能从旧表返回过期结果。现在这些 strict 读取面复用 `read_match_report_resource` 对当前 owner、任务摘要、简历/画像及发布版本回查；匹配类过期记录 409 且不泄露评分，报告列表只保留当前已验证记录，按过滤后的集合分页并计算总数。演示模式维持旧投影，不冒充可信成果。
- `backend/tests/test_match_report_http_mysql.py` 在唯一合成 owner/岗位上以真实 Flask+MySQL 运行 L3/L4，撤回岗位后回读匹配列表、解释、技能差距、报告详情均为 409； `test_verified_match_report_mysql.py` 验证报告列表不含撤回成果且进度/任务 409。`test_report_list_verified_pagination.py` 另外覆盖第一条过期时仍能取到后续合法条目及当前 `partial` 状态过滤。相关定点合跑 **13 passed**；技能差距的旧行是隔离合成夹具，未宣称 L3 现在会自动生成该表。
- 继续审计发现已注册的 `/api/pipeline/ext/save/match-result` 可把客户端提供的 `student_id` 和 `total_score` 写入权威 `match_result`，`/match/list` 缺学生参数时全局读分数；同模块另四个旧 L3 路由亦接受客户端身份/数据。先以六条 strict 请求和 DB 哨兵红测，确认旧路由会触达 DB/计算（六项失败）；修后六条在认证后统一 `410 legacy_disabled`，不触 DB，未认证仍 401。`test_pipeline_ext_match_retirement.py` **7 passed**。demo 预览保留；此处不宣称旧趋势 API 已有可信替代品。
- 旧 `/api/match/career-recommendation` 读取独立 `career_path` 表，其写路径来自已停用的旧 L3 扩展接口；尚无与当前匹配快照绑定的证据协议，不能把它当作已验证匹配结果。当前真实用户 L0→L4、人工岗位审核/发布、可靠异步执行仍是独立缺口。

以上数字是当时的定点回归；第十一轮尚未有全量统计，需以下一节认证改动稳定后的全套复跑为准。

## 第十二轮：平台自建账号的可信登录入口（首个后端切片）

- 用户确认正式管理员身份由**平台自建账号**提供。已接入 `user_account`：学生注册由服务端生成新 ID 且强制 `student`，管理员只通过可信本机 `provision-admin` 命令开户；登录、登出、`/api/auth/me` 和 `/api/auth/csrf` 构成最小闭环。使用 Argon2id、Flask-Login、Redis 服务端会话、Flask-WTF CSRF 与 Redis 限速；每请求重新读 DB 中 `role/status` 后注入 Principal 与管理员标志。客户端传入 `id/role` 或管理员请求头不会提权，正式认证启用时本地开发身份头也不能旁路。
- 真实 MySQL+Redis、真实本地 HTTP socket 验证注册→登录→owner 403→登出→旧 cookie 拒绝；另验管理员 CLI 开户、角色降权、禁用账号、CSRF 和登录频率限制。`backend/tests/test_account_auth_http_mysql.py` 显式 `CPA_TEST_LIVE_MYSQL=1` **4 passed**；邻近启动/授权测试 **32 passed**。默认弱 `SECRET_KEY` 下认证入口返回 503，不假装具备安全登录；`/health` 仍可用。
- 账号禁用时请求会被拒绝并清掉**当前使用的**会话；但若一个旧会话在禁用期间完全没有请求，随后账号重新启用，它仍可能恢复。尚无全会话 `session_version`/主动撤销机制，不能将禁用描述成所有设备的永久登出。前端尚在 mock 阶段，未接认证；历史资料不能仅凭同名自动认领，密码找回/MFA/审计与人工岗位发布 HTTP 工作流也未完成。

上述 L3 旁路与认证改动稳定后，使用 `mkg`、Docker healthy 且仅将测试进程的 `TEMP/TMP/TMPDIR` 指向工作区 `output/` 完整复跑 `backend/tests`：**982 passed, 39 skipped, 9 warnings**（4 分 35 秒）。这是新的全套统计，覆盖旧版 973 项统计；默认跳过的真库测试仍需以本节显式 opt-in 定点结果解读。测试后 MySQL 只读计数：`user_account`、`match_result`、`skill_gap`、`career_report`、`job_catalog_publication` 均为 0；`git diff --check` 未报空白错误。全套绿灯不代表生产登录部署、真实用户的 L0→L4 体验或岗位人工审批已经完成。

## 第十三轮：全设备会话撤销、账号审核发布与可信职业路径

- 平台自建账号禁用后重新启用时，**完全没在禁用期间发请求**的旧设备 cookie 原本可能恢复。真 HTTP + MySQL + Redis 用同一账号的两个独立浏览器先红测复现，再在 `user_account` 增加服务端 `auth_epoch`：状态、角色或密码哈希变化由 MySQL 触发器提升代数；每次请求校验登录时写入 Redis session 的代数，过期会话返回 401。普通资料编辑不触发撤销。既有数据库需要显式执行幂等 `flask --app app migrate-account-session-epoch`；缺列或触发器时账号入口 503，不悄悄回退身份模式。迁移已对当前 Docker 库重复执行并核验。账号专用真依赖测试 **6 passed**。这仍不取消变化提交前已通过鉴权的在途请求，也未覆盖主从延迟或账号 ID 删除后复用。
- 按用户选择，管理员由**平台自建账号**认证。新增最窄 `job-publications` HTTP 预检→同一管理员确认精确候选/来源摘要→不可变版本发布→回读→撤回；审核时间与身份由服务器写入。发布/撤回与 `admin_audit_log` 在同一 MySQL 事务中，审计失败即回滚；学生、伪造管理员头和仅模型 `job_catalog_review` 结果均不能授权。真本地 socket + MySQL/Redis 测了权限、摘要篡改、版本冲突、来源字段、事务回滚与撤回不回退旧版；与邻接测试 **36 passed**，唯一账号/岗位/审计夹具清理。管理员提交的原网页和许可真实性**仍需人审**，HTTP 预检不证明这些事实；旧本机发布 CLI 也仍有独立信任边界。详见 `docs/specs/admin-job-publication-http.md`。
- 严格模式下职业路径旧读接口会把历史客户端可写的 `career_path.path_json` 当推荐。现必须先找到该 owner 的 L3 匹配，回查任务、简历/画像、当前岗位发布，再从已验证快照取岗位名，只读取公共 `CareerNode` 的 `PROMOTION` 路径。无匹配 404、已撤回 409；公共路径只能标通用参考，`personalized_recommendation_verified=false`。图谱无精确路径或故障保留岗位上下文但标 `partial`，明确缺失来源/重试；不伪造 `recommended_dir`。先红测旧路径泄漏，再真 HTTP + MySQL/Neo4j 测唯一公共/私有节点、故障与撤回，**19 passed** 且唯一夹具清理。岗位名称的语义映射和真正个性化规划尚未实现；见 `docs/specs/verified-career-path-read.md`。
- 又以正式账号 cookie + CSRF 经真实 socket 走收藏岗位写/读/删：请求体伪造另一个 `user_id/student_id`，MySQL owner 仍是登录账号，另一账号列表看不到；缺 CSRF 不落行，登出后不能继续写。`test_account_business_write_http_mysql.py` **1 passed**，唯一账号/收藏夹具清理。此前认证测试只验登录，不把它冒充真实业务接通。
- 学习进度写入口顶层数组、数字、字符串或非 JSON 原因 `.get`/Flask 415 被统一转成 500；现认证后、数据库写入前要求 JSON 对象，返回 400 且不产生进度。先红后绿，学习与自评邻接联合 **16 passed**。
- 本轮真依赖联合测试 `CPA_TEST_LIVE_MYSQL=1`，含账号、账号→收藏、管理员岗位发布、可信职业路径、学习输入与自评 scope，**30 passed**。默认跳过的真依赖测试不能用普通全绿替代。用户端仍是 mock；真实招聘来源采集/核验、管理员生产运维、跨来源个性化、可靠异步任务与真实简历→模型质量验收仍是独立缺口。

本轮代码稳定后，使用 `mkg`、Docker healthy、`TEMP/TMP/TMPDIR` 指向工作区 `output/` 完整运行 `D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests -q --tb=short` → **993 passed, 45 skipped, 9 warnings**（4 分 10 秒）。真实依赖定点复验命令为 `$env:CPA_TEST_LIVE_MYSQL='1'; D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_account_auth_http_mysql.py backend/tests/test_account_business_write_http_mysql.py backend/tests/test_admin_job_publication_http_mysql.py backend/tests/test_verified_career_path_read.py backend/tests/test_verified_career_path_live.py backend/tests/test_learning_progress_input_shape.py backend/tests/test_career_assessment_scope.py -q --tb=short`，**30 passed**。只读回查 `user_account`、`job_catalog_publication`、`admin_audit_log`、`match_result`、`career_report`、`career_path`、`saved_job`、`learning_progress` 均为 0 行；`git diff --check` 没有空白错误。测试绿灯不等于产品全部后端功能已完成。

## 第十四轮：自建账号改密与全设备重新登录

- 自建账号已有开户、登录及数据库触发的旧会话撤销，却没有账号本人可用的改密入口。新增 `POST /api/auth/change-password`：只有当前登录者、正确的当前密码、有效 CSRF 和不同的 12–128 字符新密码才能进入事务。MySQL 行锁内核对账号状态与登录代数，更新 Argon2id 哈希并回读触发器递增代数；成功后清除本设备会话，其他设备旧 cookie 也在下次请求被拒。客户端不能提供目标账号 ID/role，未知密码不提供公开重置后门。每 IP 限速五次/小时。
- 真本地 socket HTTP + MySQL/Redis 先红测未注册路由（错密码请求 404），实现后验证无 CSRF、错当前密码、太短/相同新密码均不改变哈希或代数；改密后两个浏览器的 `/me` 与 owner 入口均为 401，旧密码登录失败、新密码登录成功，随机账号行精确清理。专项与原账号测试合跑 **7 passed**；此证明当前单主数据库/Redis 的下一次请求撤销，不声称消除已通过认证的并发在途业务写入。
- `docs/specs/self-managed-account-auth.md` 增加请求、重登、故障与开源复用边界；README/API catalog 明示页面仍 mock、无邮件找回或 MFA。尝试按用户偏好并行分派另一条语义事实恢复任务时，协作会话因系统盘 `C:` 剩余空间为 0 而创建失败；没有清理不属于当前工作区的临时文件或伪称该任务已开始。本轮继续本地推进。

稳定后完整后端回归（`mkg`，`TEMP/TMP/TMPDIR` 指向工作区 `output/`，不启用真库 opt-in）为 **993 passed, 46 skipped, 9 warnings**（4 分 01 秒）；比前轮多出的 skip 是新改密真库测试，非隐藏失败。再次显式设置 `CPA_TEST_LIVE_MYSQL=1`，联合运行 `test_account_password_change_http_mysql.py`、`test_account_auth_http_mysql.py`、`test_account_business_write_http_mysql.py`、`test_admin_job_publication_http_mysql.py` → **10 passed**。只读回查 `user_account`、`job_catalog_publication`、`admin_audit_log`、`saved_job` 均为 0 行，`git diff --check` 没有空白错误。尚未验证多主/跨区撤销，或无当前密码时的身份找回流程。

## 第十五轮：模型岗位审查的管理员只读桥接

- 新增正式管理员专用 `GET /api/admin/job-reviews` 和 `/<review_id>`；对模型审查原文、摘要、来源 ID 与索引字段重新校验。`blocked` 可回看阻断原因，只有结构上 `candidate_ready` 的审查会提供复制给**独立预检**的候选，但模型审查本身无发布权。摘要篡改返回 503，不以异常记录充作已审岗位。
- 真本地 socket HTTP + MySQL/Redis：学生和匿名不可读、blocked/ready 审查列表与详情可读、ready 候选只能走另一步管理员预检、篡改失败关闭；随机审查/账号/审计夹具精确清理。与既有管理员岗位发布专项 **14 passed**；随后整套后端 **993 passed, 47 skipped, 9 warnings**（4 分 20 秒）。
- 现存五条真实 `job_catalog_review` 均为模型 blocked，仍无真实人工发布。只读复核官方 CSB feed 中 `50368`：记录唯一、原文摘要未变、当次来源截止字段为 2026-10-22 23:59:00；这只是读取证据，不等于人工核准或持续招聘有效。

## 第十六轮：可看原文、不可评分的官方岗位层

- 用户明确选择：官方来源已核对但资格条件暂不能由当前评分器完整表达时，允许**先展示**，必须禁止评分和匹配。为此建立独立 `job_catalog_source_listing` 表及迁移：正式管理员对精确模型审查/原记录摘要确认，服务端只重取固定 CSB feed 的同一原文、检查未过截止日期，再将原文、来源摘要、核验时间、管理员身份和独立展示版本与审计同事务提交。模型 blocked 仅允许这种原文层，不会被改写为可评分 `job_catalog_publication`。
- 公共 `/api/profile/job/list`、`/search`、`/<source_csb_id>` 可读取原文层，明确 `source_only`、`match_eligible=false`、`scoring_status=not_supported`、`vacancy_validity=unknown`；超过来源截止时间隐藏，最新版本撤回后不回退。篡改摘要时目录/详情 503，而非静默漏数或展示损坏数据。管理员另有版本列表/详情与精确撤回，包含已撤回审计历史。
- `/api/match/run`、`/api/report/generate`、页面 `/api/profile/student/pipeline` 的 L3/L4 在建任务/报告 ID 前对 `source_csb_` 返回 `409 job_source_only_unscored`；正式评分发布 HTTP 和旧本机 CLI 同样拒绝这个保留前缀。合成官方源的真实 HTTP/MySQL/Redis 测试覆盖匿名拒绝、来源变更、审计注入失败回滚、批准/列表/搜索/详情、篡改关闭、L3/L4 零任务/零业务结果、到期及撤回不回退；与发布、审查、旧目录及匹配合同邻接联合 **25 passed**。
- 当前 Docker MySQL 已应用加表迁移并核验表为空，未把现存五条 blocked 审查自动发布。既有 volume 不会重跑 `init_mysql.sql`，部署前须按 `docs/specs/source-only-job-catalog.md` 单独迁移。当前**没有定时重新抓源/持续撤稿服务**，所以公开条目只称“核验时来源记录存在”，不能当作实时在招保证；前端仍用 mock，尚未展示这两个信任层。

第十六轮代码稳定后的完整后端回归：`mkg`、Docker 服务可用、测试进程 `TEMP/TMP/TMPDIR` 指向工作区 `output/`，`pytest backend/tests -q --tb=short` → **994 passed, 48 skipped, 9 warnings**（4 分 04 秒）。新增的默认 skip 是需显式启用真库的来源专用 HTTP 测试；它已在上述 25 项定点联合回归中真实执行。只读回查：现存模型审查 5 条，来源专用展示 0、评分发布 0、管理员审计 0、自建账号 0、匹配结果 0、职业报告 0；`pipeline_task` 尚有先前存量 10 条，未把它们误删为本轮测试残留。`git diff --check` 无空白错误；大量工作树改动为前几轮连续修复与既有未提交文件，未重置或清理无关文件。

## 第十七轮：Hybrid 与原始 JD 向量的岗位发布旁路

- 继续追实际可达的公开候选入口。真实 Flask socket + MySQL 中插入唯一**未审核** `job_portrait_4d` 行，向量命中另含伪造标题/私有文本与 `source_csb_` 来源专用 ID：旧 `/api/match/hybrid-candidates` 200 原样返回未审核岗位和来源专用 ID。旧 keyword 来源是可变旧画像，vector 原文没有版本核对；其他目录的发布门禁不能覆盖此路由。
- 现 keyword 只从经摘要与最新版校验的 `job_catalog_publication` 快照取技能，vector 只能提供排序线索；所有候选在融合前回查正式发布或公共 `career_insights`，在返回前再次回查。岗位标题、来源、版本和摘要只来自正式快照；职业方向另标 `candidate_kind=role`、`match_eligible=false`，不伪装成岗位。缺/坏发布仓库 503，未知、未发布、已撤回和 `source_csb_` 均不进入候选。非法 JSON、技能结构、分页与图谱开关在访问依赖前 400；`"false"` 不再被当真值。
- 同一真 HTTP 夹具进一步发现邻接 `/api/pipeline/ext/vector/search` 旧 `jd` 查询会把 3 条 Milvus 原始 `metadata/text`（含未发布、来源专用和正式发布岗位的私有索引正文）全部对外返回。现 JD 命中返回前按最新正式发布投影，仅一条已发布岗位、且只含服务端标题/版本/摘要/来源及有限 distance；撤回后 0，损坏发布库 503。公共技能索引查询保留，resume/conversation 私有集合仍 403；不合形状的查询和 `top_k` 在索引访问前拒绝。
- 故障先红：Hybrid 未发布 ID 断言失败；随后原始 JD 向量 3 条而预期 1 条。修复后显式真库与输入/管理员向量邻接测试 **29 passed**，唯一 publication、旧画像、职业方向夹具精确清理。此测试使用隔离向量假命中，**没有证明当前 Milvus 索引已由正式发布快照重建或向量排序质量**。现实现扫描已发布目录，不适合无限增长岗位库；向量/图谱依赖失败仍可能静默降为空通道，后续须加 `partial` 来源覆盖契约和版本绑定索引。详见 `docs/specs/hybrid-public-candidate-boundary.md`。

本轮最终代码稳定后完整后端回归：`mkg`、`TEMP/TMP/TMPDIR` 指向工作区 `output/`，`pytest backend/tests -q --tb=short` → **1014 passed, 49 skipped, 9 warnings**（4 分 20 秒）。显式 `CPA_TEST_LIVE_MYSQL=1` 的 Hybrid、公开向量、输入门禁及来源专用邻接测试 **29 passed**；全套默认 skip 的真 MySQL/向量替身集成测试已由该组单独执行。复跑后只读库检查 `job_catalog_publication`、旧 `job_portrait_4d`、`job_catalog_source_listing`、`match_result`、`career_report`、`admin_audit_log`、`user_account` 均为 0，`career_insights` 回到原有 26 条，随机 `hybrid_%` 画像残留为 0；`git diff --check` 无空白错误。该结果不代表真实 Milvus 向量来源版本已绑定，也不等于真实用户 L0→L4 全链已验收。

## 第十八轮：官方来源即时核验、JD 索引精确绑定与部分结果

- 官方 source-only 岗位在批准后若官方 feed 删除、更改或出现重复记录，旧目录此前仍公开批准时的快照。现公共列表/详情有可见 source-only 条目时逐次重取固定 CSB feed，按原记录摘要核对；变化则隐藏，上游不可用或核验时间无效则 503。已撤回或过期的条目无需访问上游。`source_fetched_at` 保留批准时刻，`source_verified_at` 改为本次读取的真实核验时刻。合同测试先红后绿，真 HTTP/MySQL/Redis 来源专用专项亦通过。代价是有可见条目时公共请求可能等待上游最长 20 秒，尚无可接受最大陈旧时间的缓存策略或后台对账；这不是低延迟 SLA。
- 管理员 JD 向量写入旧路由可接受浏览器任意 `text_chunks`，先前真 Flask 请求已证明会写入。现在只接受岗位 ID、发布版本和摘要，服务端精确读取当前人工发布快照的 `source_text`/标题生成向量，旧正文、未发布或过期版本在触达 Milvus 前拒绝。metadata 固定携带 `job_portrait_id`、`publication_version`、`content_sha256`。Hybrid 与公开 JD 向量读取只允许三者与当前发布快照完全一致的命中；缺字段的历史向量、旧版本和错误摘要均 fail closed。回归涵盖旧客户端 payload、精确快照写入替身、旧/新命中并存及撤回；**未写真实 Milvus，也未迁移现存历史索引**，旧索引召回会暂时降低。
- Hybrid 过去在 embedding/Milvus/Neo4j 故障时可能把通道静默变空。现在保留已验证关键词候选，但 `status=partial`、`failed_sources`、`source_coverage` 和重试提示显式说明缺失；正常无命中是 `empty`，候选目录本身不可校验仍 503。Neo4j 角色解析失败也不冒充未映射。另发现公开 `/api/pipeline/ext/vector/search` 把**整个向量批次**误当成单条查询向量传给 Milvus，异常又吞成 200 空结果；先以 Flask 请求和索引替身红测复现，再改为传单条扁平向量、严格传播检索故障。`all` 只有一个集合故障时保留另一集合结果并标 `partial`；单集合或全部故障返回 503。此处未宣称向量召回质量已验收。
- 完整回归第一次在无关 `test_ask_enqueues_rollup_at_k8_window` 遇到外部模型 HTTP 瞬断，单测立即复跑通过；该测试只验证 rollup 排队，不应依赖在线模型，已补本地模型替身。稳定后 `D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests -q --tb=short` → **1028 passed, 49 skipped, 9 warnings**（4 分 10 秒）。显式 `CPA_TEST_LIVE_MYSQL=1` 联合运行 Hybrid 发布门禁、source-only 公开层、管理员发布和向量可靠性专项 → **7 passed**；离线公开向量与管理员向量邻接专项 **20 passed**，来源覆盖与版本绑定专项 **10 passed**。只读库回查 `job_catalog_publication`、`job_catalog_source_listing`、`job_portrait_4d`、`admin_audit_log`、`user_account`、`match_result`、`career_report` 均为 0，`career_insights` 仍为原有 26；`git diff --check` 无空白错误。

仍未完成：真实 Milvus 索引重建与召回质量、source-only 对上游延迟的缓存/对账策略、正式用户前端接入、真实简历→岗位评分质量及可靠异步执行。全后端测试通过不能替代这些产品链路的真实体验验收。

## 第十九轮：真实主链断点、目录翻页、向量适配与部分报告原因

- 以新注册的正式学生账号、Cookie/CSRF、真实本地 HTTP、MySQL、Redis、Neo4j 以及已配置的 `qwen-plus`，用唯一合成 TXT 简历重放 L0→L4。注册/登录/资料确认成功，L0 真实模型返回 `needs_input`，确认后 `student_profile` 落库；**L2 首次阻断**为业务 `partial`，`portrait_id=null`、画像 0 行，`missing_fields=["school_tier"]`。候选里的 `school_tier="其他理工"` 没有可信评分映射，D1 为 null，系统没有伪造分数；L3 因 `confirmed_portrait_unavailable` 无匹配，L4 无报告。三次独立探针同样断在 L2；合成账号、任务、上传及对应 Redis/Neo4j 资源按精确 ID 清理。这证明当前主链**未完成**：`confirm_semantic_facts` 仅为提示，`action_available=false`，尚无可用确认/恢复 API。不能用本次真实模型调用成功替代模型评价质量验收。
- L4 前置阻断时 HTTP 原先丢失权威任务已有的 `reason/next_action`，现 `/api/report/generate` 的 `partial` 响应保留这两个字段，让页面能解释具体缺口；定点合同与邻接测试通过，不把“有原因”冒充报告已生成。
- 公开 `/api/profile/job/search` 的空白关键词原会返回整个目录，现在触库前 400；搜索加入 `offset`，可翻到已发布岗位后面的 `source_only` 条目，并返回 `total/limit/offset/has_more`。真实 HTTP+MySQL 的唯一双层目录夹具和输入邻接 **9 passed**，记录清理；官方上游使用固定测试替身，不声称真实在招。
- PyMilvus 适配器原向 `MilvusClient.insert` 传列数组字典，真实 Milvus Lite 报向量类型不符；改为逐行实体，并将返回的 protobuf ID 序列转为普通列表。隔离 Milvus Lite 实际插入/检索一条 JD，另以真实 Flask 路由+MySQL 已发布版本+Milvus Lite 验证写入后公开搜索只展示服务端发布快照、撤回后旧向量不可见，显式 opt-in **2 passed**。模型 embedding 用确定性替身，未核验真实召回质量或旧索引迁移。
- 任务状态另有真实 MySQL 回退：`BasePipeline.update_status` 无条件 UPDATE 可让重复 runner 把 completed 改回 running；Agent 工具包装层又先写 running、后覆盖终态，与真实执行体争夺同一任务。现 BasePipeline 用事务及受影响行数限定 pending→running、running→终态；Agent 包装层不再重复写状态，异常只可将尚未领取的 pending 置 failed，已终结或正运行的任务不被包装层覆盖，响应提示回读权威 task。先红后绿的真 MySQL CAS、Agent 工具委托、严格上传 HTTP 和事件序列与邻接定点共 **68 passed**；测试解析与 NSLE 是替身。用户确认后的 `needs_input→completed` 仍由独立确认事务处理。进程崩溃遗留 pending/running 仍**不能自动恢复**，本轮没有 Worker/队列，不能宣称异步任务可靠性已完成。

本轮稳定后的完整后端回归：`mkg` 环境、`TEMP/TMP/TMPDIR` 指向工作区 `output/`，`pytest backend/tests -q --tb=short` → **1031 passed, 64 skipped, 9 warnings**（4 分 17 秒）。显式启用 `CPA_TEST_LIVE_MYSQL=1`、`CPA_TEST_LIVE_MILVUS_LITE=1` 的新向量/目录/报告定点联合 **7 passed**，另有上述 CAS 与邻接的 68 项真库/假库联合回归。只读库回查 `user_account`、正式发布、来源专用展示、匹配、报告、管理员审计均为 0 行，`status_cas_` 合成任务及事件均为 0。默认 skip 增加主要是新真库 CAS/向量测试的显式 opt-in，不等于跳过已知失败。主链 L2 缺少可执行的语义事实恢复、可信学校层级评分依据、可靠后台恢复和真实岗位/模型质量验证，目标仍未完成。

## 第二十轮：公共技能索引输入与现场数据基线（进行中）

- 公共技能向量写入口的管理员权限已有，但 `skills` 为字符串、混合数组或非文本字段时旧 HTTP 返回 500，且在访问 Milvus 后才发现错误。先用 10 组畸形输入与一组有效输入复现 **11 failed**，再补超长空白等边界；现在整批在访问 embedding/Milvus 前校验 1–100 项、名称/描述/类别类型及原始长度，并规范首尾空白。输入合同、管理员权限与公开查询邻接 **33 passed**。隔离 Milvus Lite 的真实管理员写入→公开 skill 搜索与 JD 专项/输入合同联合 **16 passed**；embedding 为确定性替身，未冒充真实召回质量。
- 对本机 `.env` 实际配置的 Milvus Lite 做只读连接/统计：`conversation_vectors`、`jd_vectors`、`resume_vectors`、`skill_vectors` 四集合均可连接，但 `row_count` **全为 0**。同一时点 MySQL `job_catalog_publication=0`、`canonical_entity=0`，模型岗位审查 5 条、公共职业方向 26 条。故“接口可查询”不等于当前有可召回的可信岗位/技能内容；本轮没有把合成测试数据留在默认索引，也未擅自批准官方岗位。这一现场事实使真实内容供给、管理员核验与索引构建仍是用户体验的前置缺口。
- L2 错置类别的来源边界：`其他理工` 实为专业类别表中的值，不在学校层级评分表。此前有逐字引句就被保留为事实，但没有说明该维度不可评分。现在 L0 候选仍保留原值、引句及来源 hash，可正常由用户确认，同时标 `value_not_in_scoring_category` 与人工复核动作；诊断报告对该条标 `scoring_status=unmapped_category` 并解释引用不等于可评分。真实 MySQL 确认事务→候选 HTTP 回读→L2 HTTP 的合成错置类别专项 **1 passed**：D1 学校分/总分为 null、D3 可单独有分、任务 partial、画像表无行且夹具清理。离线语义/报告邻接 **63 passed**；未修改评分映射、未声称真实模型已纠错，更未写“未评分画像”。
- 旧 `POST /api/pipeline/l3/run-match` 过去只插入永久 pending，现在恢复为有边界的**专用**持久派发：登录 owner 的已确认简历、当前 L2 来源和人工发布岗位须先通过权威校验；同一 MySQL 事务写任务及派发意图，Celery/Redis 仅传 task ID，独立 worker 以租约及 claim token 领取，领域匹配结果与终态同事务提交，按 owner 回读。真实 Flask→Redis→独立 worker→MySQL/事件测试、Broker 首次失败后手动 replay、过期 claim fencing、派发插入回滚通过。另以故障注入先红证明“任务已提交、派发回读异常却 HTTP 503 不给 ID”的窗口；现提交之后始终返回 202 和 task ID，`delivery=unverified` 可回读/重放。`CPA_TEST_LIVE_MYSQL=1`、`CPA_TEST_LIVE_MATCH_QUEUE=1` 与邻接联合 **25 passed**，唯一合成岗位/资料/队列清理。既有 volume 须显式迁移，独立 worker 须运行，重放目前为运维命令而非自动定时；当前真实发布岗位 0 条，不能当作用户已有可匹配结果。见 `docs/guides/pipeline-dispatch-boundary.md`。

第二十轮全部代码稳定后完整后端回归（`mkg`、`TEMP/TMP/TMPDIR` 指向工作区 `output/`，无 opt-in）为 **1048 passed, 72 skipped, 9 warnings**（4 分 51 秒）。另显式设置 `CPA_TEST_LIVE_MYSQL=1`、`CPA_TEST_LIVE_MATCH_QUEUE=1`、`CPA_TEST_LIVE_MILVUS_LITE=1`，把派发 worker、错置学校类别、真实向量及技能输入专项联合复跑 → **21 passed**；新增 post-commit 故障注入和邻接另由真库/worker 联合 **25 passed** 覆盖。只读 MySQL 回查账号、发布、source-only、匹配、报告、审计和 `pipeline_match_dispatch` 全为 0，唯一 `match_dispatch_`/`match_worker_`/`match_enqueue_`/`l2_tier_` 资料、画像、任务、事件残留均为 0；重放 CLI 对当前库返回 `attempted=0`。`git diff --check` 退出码 0。仍未完成：学校类别规则与可执行的语义确认/未评分画像策略、可信内容供给、自动任务重放及其余 L0/L1/L2/L4 异步执行、前端真实集成。

## 第二十一轮：管理员原文暂存、后台恢复、可信技能索引与来源核验缓存

- 管理员 dataset/KB 旧上传路由只存客户端声称的元数据，不能支撑内容处理。新增同路由 multipart 私有暂存：2 MiB、UTF-8、dataset JSON 数组结构与最多 1000 条服务端计数、KB 文本、文件长度/SHA-256、固定路径回读和异常删除回滚。`job_dataset.count` 仍为 0，`staged_record_count` 只代表暂存条目；`status=pending`、`content_ingested=false`，管理员构建仍 503，不把私有原文当已发布岗位或图谱。一次性 MySQL 迁移已在本机应用，管理员相关 **28 passed/3 skipped**。另用正式自建管理员 CLI 创建账号，以真实 TCP HTTP、Cookie/CSRF、学生越权拒绝、MySQL/私有文件验证上传→回读→删除，专项 **1 passed**；`uploaded_by` 回读为真实账号 ID，公开岗位表未变化。跨文件系统/数据库强杀窗口仍需孤儿文件核查，见 `docs/specs/admin-source-staging.md`。
- L3 MySQL intent 的手动重放升级为独立 Celery Beat 每 60 秒自动有界扫描；每轮最多 50 个到期任务，失败发布记尝试时间并按最久未尝试排序，过期 running 租约可重领。真实 Beat→Redis→独立 worker→MySQL 两条故障恢复（首次 broker 失败、过期 claim）均读到唯一匹配和正确 owner 终态；连同公平扫描、原 HTTP 门禁/CAS 邻接 **28 passed**、无合成残留。Beat 与同队列 worker 必须都在线；L0/L1/L2/L4 旧异步入口仍未恢复，当前正式岗位发布数 0。见 `docs/guides/pipeline-dispatch-boundary.md`。
- 旧技能 seed 会 LLM 扩写并回写 MySQL，且默认 `skill_vectors` 原为空；现只读真 MySQL `skill_taxonomy` 的名称/类别/子类等结构字段，携带 taxonomy ID、来源与文本 SHA-256，以可重放 reconcile 写 Milvus，不用随机向量或未核实市场分。默认 Milvus Lite 用本机真实 BGE-M3 建 99 条，重复运行 `inserted=0/deleted=0/verified=99/unmanaged=0`；修复独立进程 Milvus Lite 冷启动集合 `released` 导致首搜失败。隔离真 MySQL/Lite **3 passed**，既有 Lite **3 passed**，邻接搜索 **6 passed**。又从 Flask 公开向量 HTTP 入口用真实 BGE-M3 查询 Python，状态 complete、首条为 MySQL taxonomy 的 Python，opt-in **1 passed**。这只证明索引供给和一次查询，不证明全面语义召回质量；旧管理员技能向量手工写入口的来源一致性仍待收束。见 `docs/guides/skill-vector-index.md`。
- 用户确认官方原文岗位可接受最长 5 分钟后台核验缓存，故障应隐藏该层、保留平台已发布岗位。公共列表/详情不再逐次等待官方上游 20 秒；Beat 每 60 秒刷新固定 CSB feed 的记录摘要到 Redis，TTL 最多 300 秒，重复/变化/删除在下一次成功核验后隐藏；刷新失败主动清缓存。缓存不可用时 source-only 详情 503，列表/搜索保留可评分发布候选并返回 `status=partial,failed_sources=[official_source_verification],retry_after_seconds=60`，非故障无匹配为 `empty`。真实 HTTP+MySQL+Redis 的 source-only 发布→预热→无上游公共读取→删缓存 partial/详情 503→恢复→撤回以及离线 TTL/定时任务合同定点通过。当前 source-only 行数 0，本轮未以真实官方记录证明线上新鲜度/低延迟 SLA；见 `docs/specs/source-only-job-catalog.md`。
- 实际公开向量 HTTP 冒烟同时发现模型加载日志将 Unix 时间戳误报为数十亿秒耗时；只修正显示为单调时钟差，不改变 `get_stats().load_time` 的原时间戳合同，隔离回归已通过。当前主要业务阻断仍为真实新账号简历 L2 的学校类别无法可信评分、缺少可执行语义修正/未评分画像策略；不可因测试全绿而宣称完整用户 L0→L4 已完成。

第二十一轮全部代码稳定后的完整后端回归：`mkg`、`TEMP/TMP/TMPDIR` 指向工作区 D 盘 `output/`，`pytest backend/tests -q --tb=short` → **1063 passed, 79 skipped, 9 warnings**（4 分 17 秒）。显式真库/队列、默认真索引与 HTTP 验收分别运行，非把 skip 当通过。最终只读 MySQL 回查 `user_account`、正式/来源专用岗位发布、dataset/KB 暂存、匹配派发/结果、报告、管理员审计均为 0，`skill_taxonomy=99`；默认技能 Milvus Lite 受管行 99 且重放无变更。`git diff --check` 无空白错误。当前 source-only 记录与正式可评分岗位均为 0，无法证明有真实用户可完成岗位专项匹配；也没有自行批准模型审查记录。

## 第二十二轮：技能公共读写同源与集合故障隔离

- 管理员旧技能写入口虽有身份门禁，但可把任意浏览器文本追加到公共 `skill_vectors`，与上轮按 MySQL taxonomy 重建的 99 条受管数据并存；公开 skill 查询又把原始索引文本返回。现旧 `skills` 负载在访问索引前 400；管理员只能提交 `source=mysql.skill_taxonomy` 和源行数预期，由服务器读取当前结构化 taxonomy 并执行幂等 reconcile。源计数变化或历史未受管行拒绝同步，不静默采纳。公共命中逐条核对当前 taxonomy 结构、模型、摘要和正文，只输出服务端投影；混入未受管行时保留可信命中并标 `partial` 与丢弃数；taxonomy 不可读或有源数据但索引空时不谎称“无匹配”。
- 用真实 TCP HTTP、真实 MySQL 99 行、隔离 Milvus Lite 与确定性测试向量验证旧文本 400、管理员同步重放、计数冲突 409、模拟未受管近邻后只返可信 Python 且标部分结果、再次同步因污染 409，**1 passed**，没有碰默认索引。默认 Milvus Lite + 真实 BGE-M3 的公开 Flask HTTP 搜索复跑 **1 passed**，Python 首条且来源绑定有效。离线输入/投影/向量传输专项 **19 passed**，隔离 Lite/投影专项 **3 passed, 1 skipped**。不能据此宣称全部语义召回质量。
- 邻接公开 `collection=all` 在 JD 命中需要校验发布目录而目录故障时，会越过集合级隔离直接 HTTP 503，丢掉已核验 skill 命中。新增 HTTP 红测复现 `503`，改为 JD 集合 `unavailable`，保留 skill 结果并标 `partial`；单独 `jd` 仍 503。输入/投影/可靠性专项修后 **20 passed**，合同在 `docs/specs/hybrid-public-candidate-boundary.md`。
- 用户确认学校类别人工修正**仅接受可核验的学校官方或教育主管部门材料**。现 L2 仍不接受客户端臆测、未核验网页或把专业类别 `其他理工` 强塞入学校档位；缺可信依据时维持 `partial`、不持久化评分画像。该答复尚未落成可执行的官方材料审核/恢复链路，也未决定是否允许“未评分画像”，不得标记主链完成。

最终代码稳定后的完整后端回归在 `mkg`、D 盘 `TEMP/TMP/TMPDIR` 下为 **1067 passed, 80 skipped, 9 warnings**（4 分 20 秒），已包含 JD 集合级故障隔离新增测试；其先红后绿 HTTP 专项与邻接另为 **20 passed**。只读 MySQL 回查 `skill_taxonomy=99`，正式/来源专用岗位、账号、匹配、报告、管理员审计均为 0；`git diff --check` 退出码 0。当前主链仍卡在学校档位的可信人工复核，真实岗位供应也未建立。
