# 公共技能分区：发布与验证

状态：代码与替身契约已接线。2026-10-09 本机 Docker 恢复后，在 `localhost:17687` 发布了审阅种子 `repo:public-skills:v1`（11 节点、4 条边），`CPA_TEST_LIVE_PUBLIC_GRAPH=1` 的隔离负测 **1 passed**。这只是公共技能小种子的真实 Neo4j 验证，个人图迁移、完整岗位关系和全平台证据隔离仍未完成，不能描述为完整技能图谱恢复。

## 边界

- 旧 `Skill`/`JobPortrait` 混有简历和运行时 JD 输入，**不从旧库回填 public 标记，也不按名称推断公共性**。
- 新 `PublicSkill` 是仅供公共查询的独立标签，不兼挂 `Skill`。每个节点须独占该标签、`visibility=public`、`source_id=repo:public-skills:v1`；每条可遍历边也需相同来源。路径中的每个中间节点都校验。
- 唯一发布器是离线 `backend/scripts/seed_public_skills.py`；应用上传和 `GraphStorage.save_skills/upsert_skill_node` 仍只写旧 `Skill`，不能修改新分区。公共模板不接收标签、Cypher 或来源参数。
- 种子来自已审阅的仓库 `init_neo4j.cql` 子集，共 11 个技能、4 条先修边，内容单列于 `public_skill_seed.json`。不是实时市场证据、完整技能目录或技能-岗位关联。
- API `skill-neighbors`、`skill-categories`、`query` 的 `skills/all_skills/skill_neighbors/skill_categories`，主 Agent `graph_query`，planner `skill_recommend/learning_path` 共享分区。未发布为 HTTP 503 / `status=unavailable`，而非“没有相关技能”。JobPortrait、技能-岗位关系及全库概览继续停用。
- 老 `GraphStorage` 公共邻居/推荐/路径/子图读取也限制到新分区；其旧列表返回类型没有完整可用性封装，新入口应复用 `query_public_skills`。
- 扩展接口 `/api/pipeline/ext/recommend|path|graph` 已直接改用同一 `query_public_skills`，不再构造 `GraphStorage`；分区为空或不可达均返回 `success=false,status=unavailable` / HTTP 503，禁止回退旧 Skill。`path.max_hops` 只接受 1–5，节点列表上限 50、单名上限 80 字；`graph` 只返回来源已校验的先修边子集。此项不代表其他 ext 路由已全部完成数据隔离。
- `db_query` 的个人表只在有效 Flask request 中复用 `resolve_request_scope`，SQL 强制绑定 Principal；无 request/无 Principal 拒绝。跨线程可信身份下沉仍未完成，不能用工具 kwargs 替代。消息通过 `conversation` JOIN 校验 owner；不存在通用全表 fallback。岗位 SQL 表无可信 provenance，暂停工具读面。
- 四个旧向量集合均可能接收用户写入，包括 skill/jd；`semantic_search` 暂停所有这些集合及任意名称，**没有**用 `is_it_position` 假装公共标记。集合级 owner/provenance 迁移、其他 REST/Hybrid 向量读取仍需后续专项验收，本次不宣称全部向量访问面已隔离。

这是应用层分区，不是 Neo4j 数据库管理员权限隔离。拥有数据库写权限的运维发布者仍可改变公共分区；后续部署应分离发布/运行凭证。个人图 owner 分区与历史迁移未完成。

## 开源参考与适配边界

修改前检查了 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/core/src/v1/permission.ts`（明确拒绝契约，MIT），以及 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 `codex-rs/core/src/tools/sandboxing.rs`（宿主提供权限上下文，Apache-2.0）。没有复制代码或引入第二套权限框架；二者是文件/命令沙箱，不提供本项目 Neo4j 业务 provenance。这里复用现有 Neo4j 官方 driver 的参数化查询、事务和项目 Principal，只增加业务标签/来源薄契约。

## 发布与回放

从仓库根目录、使用 `mkg` 环境，先确认 Neo4j 已启动并且连接参数指向预期实例。发布不会读取/删除旧 Skill，也不覆盖旧个人数据。

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8='1'
& 'D:/Miniconda/miniconda3/envs/mkg/python.exe' -B backend/scripts/seed_public_skills.py
& 'D:/Miniconda/miniconda3/envs/mkg/python.exe' -B -m pytest backend/tests/test_public_skill_partition.py backend/tests/test_graph_query_tool.py backend/tests/test_graph_api_public.py -q
& 'D:/Miniconda/miniconda3/envs/mkg/python.exe' -B -m pytest backend/tests/test_pipeline_ext_public_skill_routes.py -q
$env:CPA_TEST_LIVE_PUBLIC_GRAPH='1'
& 'D:/Miniconda/miniconda3/envs/mkg/python.exe' -B -m pytest backend/tests/test_public_skill_partition.py -k live -q
Remove-Item Env:CPA_TEST_LIVE_PUBLIC_GRAPH
```

发布器在一个 Neo4j 写事务中幂等 MERGE。live 测试以随机 `test_run` 创建/清理隔离夹具：公共技能、旧 Skill、混合 Person 标签、错误 source；真实查询应只返回唯一合法公共邻居。未设置 opt-in 时明确 skip，不冒充真实数据库验收。

启动后对 `GET /api/graph/skill-neighbors/Python` 应看到 `机器学习`、`scope=public_seed` 和明确 source_id；学习路径从 Python 到机器学习沿“需要某先修技能”边的反向查找。先核对这些响应，再扩大数据集；不要自动回退旧库。

## 失败与回滚

- 连接拒绝：修复 Neo4j 服务后重试，禁止改为读取混存图。
- 分区为空：运行发布脚本，核对 11 节点 / 4 边；返回空集合不是发布成功的证据。
- 若需撤回该版本，在预期实例上仅删除 `PublicSkill` 且 `source_id=repo:public-skills:v1` 的节点（先 dry-run count，再由运维执行）；读面自然回到 unavailable。不得删除整个 Skill 图或给旧节点批量加公共标签。
- 暂停的语义检索/岗位 SQL 不能仅用新 Prompt 恢复；必须先补可信数据来源、owner 写入、索引迁移和跨用户负测。
