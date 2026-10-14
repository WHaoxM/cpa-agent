# Hybrid 候选召回的公开来源门禁

状态：2026-10-13 后端门禁已实现并以真实 HTTP/MySQL 验证；页面仍使用 mock。`/api/match/hybrid-candidates` 是公开候选入口，`retrieve_score` 仅是检索相关度，不是人岗匹配分；岗位候选不得绕开 `job_catalog_publication` 的人工发布门禁。

## 现场缺陷

修复前 keyword 通道直接扫 `job_portrait_4d.portrait_json`，vector 通道接受 Milvus 命中的原始 `metadata/text`，融合后即公开。旧画像表或索引中的岗位未必审核、最新版本可能已撤回；向量文本/标题也可能与已发布快照不同。源不可信时仅在别的岗位目录做发布过滤，不能保护此入口。邻接的 `/api/pipeline/ext/vector/search` 虽拒绝私有集合，仍把 `jd_vectors` 原始命中及 JD 正文直接公开，是另一条相同门禁旁路。

## 目标契约

- 岗位候选只能从当前最新 `published` 且快照摘要有效的 `job_catalog_publication` 导出。keyword 技能依据正式快照的 `required_skills`，不是旧画像。vector 可以提供召回排序信号，但其 `metadata/text/position_name` 不得直接进入公开结果；命中的岗位 ID 必须回查当前发布快照，输出标题、版本、摘要和来源均以快照为准。被撤回、未发布、未知 ID 全部排除。
- `career_insights` 的公共职业方向候选与具体岗位分层：方向候选明确 `candidate_kind=role`、`match_eligible=false`、无 job ID；向量自报的 role 也须回查该公共目录，不回显向量原文。`source_csb_` 来源专用岗位不是可评分岗位，不进入此 API 的可匹配岗位候选。
- RRF 只在通过来源验证的候选上融合；融合分不可写入 L3/L4 确定性匹配表。图谱 hop 仅是公共方向解释，不为未经发布岗位背书。候选仓库无法核验时返回明确不可用，不以空结果伪称无匹配。
- JSON 输入必须是对象；`skills` 是有界非空技能名数组，`top_k` 是 1–50 整数，图谱开关是布尔值。非法输入在依赖访问前返回 400。不能让 `bool("false")` 启用图谱。
- `/api/pipeline/ext/vector/search` 的 `jd`/`all` 只输出当前发布快照的岗位 ID、标题、版本、摘要、来源与有限 distance；不返回 Milvus `metadata/text`。未知、来源专用及已撤回 ID 被过滤，发布库不可校验则 503。`skill` 集合仍是公共技能索引；私有 resume/conversation 继续拒绝。`query` 与 `top_k` 有界，在触达向量服务前校验。

## 验收

唯一旧岗位画像行、未发布向量命中在真实 Flask→MySQL 请求中先复现泄漏；修后均不出现。构造另一条已审核发布快照，使其只凭正式快照的技能召回，标题取正式版本而非旧画像/向量元数据。最新撤回后再请求不得回退旧版；非法 JSON 类型、技能/分页/开关在访问服务前 400。测试数据仅按随机 ID 清理；不得把合成岗位视为真实采集或审核。

实测旧版返回了唯一未发布 `hybrid_unpub_*` 岗位、伪造的向量标题和 `source_csb_*` 条目。修后：验证岗位取已批准快照标题/版本/摘要；未知、未发布、来源专用与已撤回岗位均排除，向量私有文本不回显；公共 `career_insights` 角色经精确回查后以 `candidate_kind=role`、`match_eligible=false` 返回。篡改发布摘要时 HTTP 503，不当作空候选。`backend/tests/test_hybrid_publication_gate_http_mysql.py`、`test_hybrid_candidates_input_contract.py` 与邻接测试已通过。

随后用同一真 HTTP/MySQL 夹具和隔离的 Milvus 假命中复现 `vector/search` 另一旁路：旧版把未发布/来源专用 JD 的 `metadata/text` 共 3 条原样返回；修后仅一条已发布岗位的服务端快照投影，撤回后为 0，原索引 `PRIVATE` 文本不回显。`test_p0_admin_vector_scope.py` 覆盖 malformed JSON、超界 `top_k` 与私有集合在索引访问前拒绝；这没有证明当前 Milvus 索引已按发布摘要重建。

## 精确版本绑定与部分结果（2026-10-13 增补）

JD 索引命中必须携带 `job_portrait_id`、`publication_version`、`content_sha256`，并与当前最新已发布快照的版本及完整快照摘要逐项相等。只有岗位 ID、旧版本、旧摘要、缺字段的历史索引均不能参与 Hybrid 融合，也不能从 `/api/pipeline/ext/vector/search` 返回。索引正文和标题仍不得回显；管理端 JD 写入现只从指定当前发布快照取 `source_text` 和标题，不接受浏览器正文。此门禁会让尚未按新协议重建的旧向量暂时消失，不能把它解释为“岗位无匹配”。

`/api/match/hybrid-candidates` 在关键词目录可校验、但向量模型/Milvus 或图谱不可用时，保留已核验候选，`data.status=partial`、`data.failed_sources` 列出 `vector`/`graph`、`data.source_coverage` 标明各来源，`next_action` 提醒重试。各来源完成但无候选时 `status=empty`；有候选且无失败时 `status=complete`。图谱未映射是 `unmapped`，主动关闭是 `disabled`，两者不冒充服务故障。关键词/发布目录不可校验仍为 HTTP 503。`retrieve_score` 依旧只是召回排序，不是匹配分。

邻接的公开 `/api/pipeline/ext/vector/search` 现把单条 embedding 的一维向量交给 Milvus，而非误传外层向量批次；查询异常不再伪装成空命中。单集合或全部目标集合故障返回 503；`all` 中只有一个集合故障时保留另一集合的可用命中，同时返回 `data.status=partial`、`failed_collections` 和 `source_coverage`。这里的故障也包括 JD 发布目录不可校验：只要 skill 来源仍可校验，`all` 保留 skill 命中并明确标出 JD 不可用；单独 `jd` 查询仍返回 503。正常检索无命中为 `empty`，有命中为 `complete`。JD 命中仍必须通过上述精确发布版本门禁。

当前实现扫描已发布目录再读取快照，不适合无限增长的岗位库；正式规模化前需要按已发布快照建立可验证的检索索引。新契约只验证检索命中与当前发布快照一致，未证明现存 Milvus 历史索引已迁移、召回率或多通道排序质量。HTTP 覆盖测试使用可控依赖故障及索引替身，非真实 Milvus 写入成功证明。

## 公共技能向量写入输入边界（2026-10-14）

管理员 `POST /api/pipeline/ext/vector/store/skills` 是公共索引写入口。请求体必须是 JSON 对象，`skills` 为 1–100 项数组；每项为对象，非空 `name` 最多 200 字，`description` 最多 4000 字，`category` 最多 100 字。所有项先整批校验并去除首尾空白，缺少或空白描述才回退技能名；非法输入在加载 embedding 或访问 Milvus 之前返回 400，不再让字符串/混合数组产生 500 或部分写入。`backend/tests/test_skill_vector_write_input_contract.py` 先红后绿，输入合同与公共向量邻接 **33 passed**。另外，`CPA_TEST_LIVE_MILVUS_LITE=1` 下通过隔离 Milvus Lite 真实写入、公开 skill 检索和元数据回读；与 JD Lite 专项及输入合同合跑 **16 passed**。这里 embedding 使用确定性替身。该入口仍依赖可信管理员操作，尚未定义技能来源版本绑定或现存公共技能索引重建协议，不能据此声称内容真实性和真实模型召回质量已验证。

**后续替代（2026-10-14）**：上段是当时输入校验的历史状态，已被可信索引重建与来源绑定取代。旧 `skills` 文本写入现在一律 400；管理员只能请求 `mysql.skill_taxonomy` 与预期源行数的幂等同步。公开 skill 命中必须精确绑定当前 taxonomy 的结构文本、模型和摘要；无法核验的行不回显且标部分结果。隔离真 MySQL/Lite 的 TCP HTTP 正负测试、默认真 BGE-M3/索引的公共查询和操作边界见 `docs/guides/skill-vector-index.md`。不把旧 33/16 项历史测试当作当前旧格式仍可写的证据。
