# 个人图谱的主 Agent 读取边界与迁移契约

## 当前事实（2026-10-10）

主 Agent 的既有 `graph_query` 工具新增 `personal_skills` 固定模板；不新增图谱角色、会话或记忆。模型可以选择模板、精确技能名及分页位置，不能选择 owner 或执行 Cypher。工具从 Flask `resolve_request_scope()` 取得 Principal；无请求上下文或 strict 模式无 Principal 时返回 `denied`，不以工具参数中的 `student_id` 兜底。

本机真实 Neo4j 的只读盘点结果：

- `Person` 4 个，`owner_student_id` / `source_id` 均为 0 个；4 个带 `entity_id`。
- `Person → HAS_SKILL → Skill` 125 条，边的 `owner_student_id` / `source_id` 均为 0 条。
- 独立 `PublicSkill` 种子 11 个。

这些统计不是用户资料导出。旧 `EntityPipeline.run_l1_extract` / `GraphStorage.save_skills` 只写 `entity_id`，且历史 `/extract` 接收调用者传入的实体 ID；同名 `Skill` 又被全局 MERGE 和覆盖。**不能把 `Person.entity_id` 等于学号当成可靠归属，更不能把共享 Skill 的最新属性当成本人的原始证据。** 本次没有认领、改写或迁移任何历史数据。

没有**当前 MySQL 确认版本**的完整合格快照时，`personal_skills` 返回 `success=false, status=unavailable`，明确不能据此推断用户没有技能，建议通过既有 `career_data_read` 读取已确认简历/画像。后续同日已补齐下述受控发布器，`student_001` 的当前已确认资料已真实发布 35 个技能。这是重新投影已确认资料，不是“历史个人图已恢复”。

## 独立标记契约 v1

查询只走下列新分区，绝不回退 `Person`、`Skill` 或公共技能：

```text
(:PersonalGraphSnapshot)-[:HAS_PERSONAL_SKILL]->(:PersonalSkill)
```

固定来源 ID 为 `confirmed_student_profile_v1`，不接受模型提交的来源标签。

| 元素 | 必需字段 / 不变量 |
| --- | --- |
| 快照 | 唯一标签 `PersonalGraphSnapshot`；`owner_student_id`；`snapshot_id`；非空 `source_ref`；`source_task_id`；`content_sha256`；`source_id`；`provenance_version=1`；`visibility=private`；`state=verified` |
| 技能节点 | 唯一标签 `PersonalSkill`；与快照相同的 owner、snapshot、source、source_ref、source_task_id、content_sha256、provenance_version；`visibility=private`；`skill_id`、`name`、非空 `evidence` |
| 边 | 唯一允许关系 `HAS_PERSONAL_SKILL`；与快照及技能相同的 owner、snapshot、source、source_ref、source_task_id、content_sha256、provenance_version |

每次读取先调用现成 `require_confirmed_profile` 核验当前 MySQL profile 与完成确认任务的 owner、状态、candidate 内容一致，再由 owner + 确认 task + 当前候选内容 SHA256 生成确定性 snapshot_id。只接受这一版本的唯一 verified 快照，不以 Neo4j 时间或“最新”代替权威当前版本。旧版本可以保留私有记录但不再通过读取门禁。

读取再检查节点/边完整性：总边数、有效边数、唯一技能 ID 数必须都等于当前确认技能数，且每个技能名/字段证据必须匹配当前 MySQL 来源。任何缺失、重复、跨 owner、混合标签或来源不符都让整个快照 unavailable，而不是返回部分技能却声称完整。返回前再次核验 MySQL 当前版本，以拒绝读取期间发生的版本变更。只返回 `name/evidence/source_ref` 和恒为 null 的兼容 `category` 字段，不读取无来源的图属性。

标记是**受控发布器的契约**，不是模型自己宣布 `verified` 的依据。没有开放发布 HTTP API、模型写入工具或手工 JSON 发布入口。服务端函数 `publish_confirmed_personal_graph(mysql, neo4j, owner_student_id=可信身份)` 已实现：

1. source_ref 对应真实、已确认的简历/画像版本，数据库 owner 等于当前 Principal，而非前端或模型自报。
2. 仅投影已确认 `skills` 列表中的字符串或 `name`，evidence 是 `已确认简历 skills[i]` / `.name` 的字段指针，不声称它是逐页 OCR 原文引文；不推断掌握等级，不复制共享 Skill 属性、简历全文或联系方式。无效名称或超过 500 项直接停机，不静默丢弃。
3. 复用 `ConfirmationTransaction`，以与确认流程一致的 profile→task 行锁顺序锁住来源；在锁内复验。Neo4j `session.execute_write` 在单事务中创建/锁住确定性 snapshot，完整重建这一版本的私有技能和关系，校验数量和来源后才标记 verified。异常导致整笔图事务回滚；不得降级为多条自动提交写入。
4. 给 `snapshot_id` 和 `(snapshot_id, skill_id)` 加唯一约束（snapshot 哈希已包含 owner）。重复发布产生同一业务 snapshot ID 和相同技能；丢失确认后的重放可恢复。已有部分快照不视为成功，重放在事务内完整修复；遇跨 owner 节点或混合标签冲突停机。
5. 原 source_ref 被撤回、candidate 被改动、任务不再 confirmed/completed，或当前资料变成新版本时，读取端直接 unavailable，不依赖后台撤销同步。旧版实体保留但不可读。模型无权发出发布、认领或撤销操作。

这不是 MySQL/Neo4j 的分布式提交协议：MySQL 没有随发布改写已确认资料，只持有来源行锁；Neo4j 原子提交投影。读取端重新核验 MySQL 当前版本，所以写入完成后来源发生变化也不会把旧图当当前资料。后续同日已接入确认提交后的持久队列触发（见下节）；新确认版本在队列投影完成前明确 unavailable。历史 `Person/Skill` 始终不读取、不迁移、不认领。

## 确认后持久触发、失败与恢复

`confirm_resume_candidate` 在真实 MySQL 确认事务**退出并提交后**才调用投递器。确认资料和既有 `pipeline_task_event` 的 completed 事件先成为持久事实；没有任何 Neo4j 写入发生在确认提交前。投递异常不把已经成功的确认伪报失败，返回的简历状态仍 completed，另带 `personal_graph_projection` 状态。

- 复用现有 Celery app 与 Redis/AOF，专用 `cpa.graph` 队列、`cpa.graph.project` task；late ack、worker-lost redelivery 和确定性任务 ID 沿用现成机制，不新造后台线程。
- `personal_graph_projection` 是领域投影状态表，主键 `(student_id, source_task_id)`；状态为 pending、retry_pending、completed、superseded。**不修改已完成 L0 的 result_json**，从而不改变页面/Agent 引用的简历结果 hash/version。
- 没有侧表记录但 L0 已完成确认也意味着待投影。这样确认提交后进程在投递前崩溃，仍能由完成任务恢复扫描发现；旧任务即使早于 journal trigger 安装也不丢失。
- Broker 故障留下 retry_pending；Neo4j 故障也留下 retry_pending，Celery 自动重试最多 3 次，之后仍可显式重放。恢复入口直接扫描持久业务源与投影侧表，不依赖进程内 map 或 Celery SUCCESS。
- Worker 在发布器 SQL 锁内核对预期确认 task。新的确认已经替代旧任务时，旧请求只标 superseded，不发布旧版，也不把新图记成旧请求成果。
- 图提交后、侧表确认前崩溃：待办仍 pending；重放同一个确定性快照，业务数据不会重复。
- 表迁移必须显式运行，不在请求事务内做 DDL。尚未迁移或依赖故障时，确认仍成功、投影返回 pending；安装/恢复后执行重放。

在 `backend/` 目录、mkg 环境执行一次迁移及按 owner 的恢复投递：

```powershell
& D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m app.services.personal_graph_jobs --install
& D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m app.services.personal_graph_jobs --replay --student-id student_001
```

同一个 Celery app 的 worker 需监听图谱队列（只监听 `cpa.media` 的媒体 worker 不会消费图任务）：

```powershell
& D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m celery -A app.services.agent_runtime.media.jobs:celery_app worker --pool=solo --queues=cpa.graph --concurrency=1 --loglevel=INFO
```

恢复扫描每次最多 50 条，返回 has_more 时用 `--after-id <next_after_id>` 继续本次扫描；下次恢复从 0 开始。这只是单次分页位置，不是永久全局事件消费游标，避免 auto-increment 与事务提交顺序不一致漏任务。没有引入常驻恢复扫描器；自动重试耗尽或提交后投递前进程崩溃仍需要运营入口显式重放。

真实队列专项 `test_personal_graph_projection_jobs.py` 使用唯一隔离队列：先在无 worker 时确认并入队，再启动隐藏 Celery 子进程，最终读取真实 Neo4j；结束后关闭子进程。测试 worker 不是常驻部署进程，不据此声称本机图谱 worker 已永久启用。

## 分页与继续读取

`personal_skills` 支持可选精确 `entity_name` 和 `offset`（0–10000）。每页最多返回 10 项，数据库额外取第 11 项判断 has_more，不先拉取整图。个人窗口在进入共享 4000 字符模型出口前，按 3200 字符正文预算减少实际展示行数，并以实际行数推进游标，避免 continuation 被出口截掉。返回 `partial/truncated/next_offset/continuation`，模型视图保留明确的下次 offset；不能把第一页当成全文。单字段预览超过 160 字标记截断，完整证据应转向已确认简历/画像的 `career_data_read` 分页读取。

公共模板行为不变。个人 owner 不进入工具 schema；即使直接调用传入 `student_id`、`owner_student_id` 或 `cypher`，也不参与实际 SQL/Cypher 身份或代码选择。

### 主 Agent 工具调用契约（2026-10-10）

`graph_query` 采用现有 LangChain `args_schema` 薄适配：`query` 必填，`query_type`/`entity_name` 可选，`offset` 是整数且首读为 0；不暴露 owner。原先的 `{参数名:说明}` 旧格式会把所有参数建成字符串，模型按分页语义传整数时可能在工具执行前被 Pydantic 拒绝。个人已确认技能直接用固定模板 `personal_skills`，只需一次图查询；模型不必先查公共技能或调用另一个分类模型。这个参数 schema 取法参照仓库内 `opencode/` commit `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/tool/tool.ts`：工具输入先由显式 schema 解码再执行。这里只复用项目已有 LangChain/Pydantic adapter，不引入 OpenCode 运行时。

一次独立进程真实 `/api/agent/chat` 请求（`student_001`，qwen-plus，MySQL/Neo4j）产生 1 次 `graph_query(query_type=personal_skills, offset=0)`，返回 10 项、`confirmed_student_profile_v1` 来源和下一页 `offset=10`；回合状态为 `partial/tool_partial`，因为 35 项快照只读取首 10 项，不能标为全部完成。隔离测试会话随后删除；常驻后端进程需重载代码才能采用新 schema。

### 原始资料与图谱投影的证据优先级

上述图谱请求是**明确要求图谱视图**时的验证，不是“已确认资料中的技能”这一普通问句的默认答案。图谱快照按技能名排序；权威简历 `career://me/resume` 的 `skills` section 保留用户确认时的原顺序。Agent 对普通“前 10 项已确认技能”使用 `career_data_read(operation=evidence, uri=career://me/resume, section=skills, max_items=10)`，该薄适配复用 `evidence_retrieval.build_evidence_pack` 的 owner、版本、coverage 和 continuation 契约，且只接受服务端目录中当前 owner 的 URI/section。未新增资料副本或新的检索循环。图谱视图只在用户明确索取时使用，并声明派生排序与来源。

独立进程真实 Agent 请求已验证前 10 项为 PostgreSQL、Redis、Celery、RabbitMQ、Dify Workflow、Socket.IO、Docker Compose、Next.js、TypeScript、MinerU，与 Ask 权威资料顺序一致；来源版本 `task_56edb2e94056`，总数 35，下一页 `offset=10`。本轮 1 次工具调用、3 次有核验用量的模型调用（合计 10,211 tokens，低于 30,000 cap），回合 `partial/tool_partial` 仅指尚有 25 项未读取。通用 UI 工具卡的续读文案也按 `has_more + next_offset` 显示实际分页，而非“缩小查询范围”。

## 复用来源与适配边界

- OpenCode 本地 commit `388406238bd5ca15564a762840a2362c3a45bd9c`，MIT：实际阅读 `packages/opencode/src/tool/read.ts` 的参数、offset、明确截断与 continuation 实现。这里只复用其窗口读取契约到已有 Neo4j 参数化查询，未复制 TypeScript 执行内核。
- 项目既有 `Principal/RequestScope`、`Neo4jClient.execute_query`、LangChain BaseTool 适配器及 `gated_tools` 全部复用；未造新的身份/session/通用状态机。
- 发布端复用 `backend/scripts/seed_public_skills.py` 已使用的 Neo4j managed transaction `session.execute_write`，以及 `pipeline_events.ConfirmationTransaction` 对 SQLAlchemy 单事务内权威校验器的适配，不建立第二套通用事务或任务系统。
- OpenCode 的文件权限并不能替代本领域的节点/边 owner 与 source provenance，因此领域固定谓词由本项目实现，并以真实 Neo4j 跨 owner 负测验证。它不改变确定性匹配分，也不替换公共图谱发布链。

## 可复现验收

PowerShell 在仓库根目录运行（解释器为 mkg）：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8='1'
$env:CPA_TEST_LIVE_PERSONAL_GRAPH='1'
$env:CPA_TEST_LIVE_PUBLIC_GRAPH='1'
$env:CPA_TEST_LIVE_GRAPH_QUEUE='1'
& D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_personal_graph_projection_jobs.py backend/tests/test_personal_graph_publication.py backend/tests/test_personal_graph_scope.py backend/tests/test_graph_query_tool.py backend/tests/test_public_skill_partition.py -q
```

真库用例创建随机 UUID 作用域的 owner/victim 快照，先验证裸 legacy entity_id 仍 unavailable，再验证分页、伪造 owner、跨用户边、边 owner/source、节点 owner/visibility/source_ref、混合公共标签；每次只改一个变量。finally 按本次唯一 test_run 删除所有合成节点，不改动既有个人或公共记录。测试也经过真实 LangChain schema → gate → 模型/UI 双出口，验证新模板及 offset 可达且 owner 不在 schema。

2026-10-10 第一阶段为 **48 passed / 0 skipped**，当时尚未发布真实个人快照。追加发布器后专项为 **56 passed / 0 skipped**，含真实 MySQL/Neo4j 的发布、重复重放、写入完成但验证前故障注入→回滚、部分数据修复、确认版本替换、源任务撤回和跨 owner 负测。合成来源和图节点均在 finally 精确清理。

追加确认后持久触发后，上述五文件专项为 **61 passed / 0 skipped**（包含真实 Redis/Celery 子进程）；另一次连同 pipeline journal、resume confirmation、resume golden path、media async 的扩展回归为 **111 passed / 0 skipped**，此扩展回归先于最后增加的“图提交后侧表确认前崩溃”用例。专项明确断言：投递/失败/重放/完成全过程 L0 result_json 保持不变。测试隔离队列及本次启动的 worker 均清理，不改服务队列。

## 本机真实确认资料发布与回滚

同日实际读取 `student_001` 的 MySQL 当前确认 `task_56edb2e94056`，通过上述发布器保存 **35 个技能 + 35 条私有关系**：

- source_ref：`resume-confirmation:task_56edb2e94056`
- snapshot_id：`d49677d5dc3637b9ed06ee0e0ff5488ce60eae92ea98e9154e29840af518c919`
- 幂等重放：相同 snapshot_id、source_ref、skill_count。
- 可逆验证：仅把该快照 state 设为 withdrawn，当前 Principal 实际查询 unavailable；重新受控发布恢复 verified。
- 同一主 Agent 工具链：真实 LangChain adapter → qa gate → `graph_query(personal_skills)` → model/UI，offset `0/10/20/30` 对应 `10/10/10/5` 项；前三页 partial、末页 completed；source 与下一页 cursor 都进入模型视图，没有新增图谱会话。
- 回读权威节点：state verified、35 skills、35 edges；旧 Person 仍 4、旧 HAS_SKILL 仍 125。

复现发布仅需服务端 Python 调用（不接收模型提交的 profile 或 task ID）：

```python
from app.services.db import get_mysql_client, get_neo4j_client
from app.services.personal_graph import publish_confirmed_personal_graph
publish_confirmed_personal_graph(get_mysql_client(), get_neo4j_client(), owner_student_id="student_001")
```

需要停用这次发布时，可在管理通道以**该 owner 与完整 snapshot_id 双条件**将 `PersonalGraphSnapshot.state` 改为 `withdrawn`；不要删除/修改旧 Person 或公共分区。重放会重建这一已确认版本并重新启用；若要持久撤回源资料，应撤回 MySQL 权威确认源，查询及发布都随之失败关闭。

尚未覆盖：真实主模型自主选择该工具及最终解释质量、广泛并发压力测试、常驻恢复扫描器和主机启动托管。门禁在每次读取前后核验来源，但不宣称跨数据库分布式快照隔离或后台同步撤回已经实现。
