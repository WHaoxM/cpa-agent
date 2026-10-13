# 简历实体对齐：当前纵向切片

## 已接入的真实路径

`L0ParseResumePipeline → NSLEPipeline.run_resume_text → extract_canonical_profile`
先抽取完整 canonical profile，再独立复用 `LLMSkillExtractor` 的全文证据抽取，
生成 `skill_mentions: [{name, evidence}]`。它不在主画像/merge 的 wire schema 内，
可选调用失败或截断只使 entity_linking unresolved，不破坏主简历结构化。
该可选调用使用 `LLMSkillExtractor.extract_mentions` 的窄 schema，不重复输出
等级、分类、置信度和关系。模型只返回 name/source_segment_index；输入是带索引的
完整原文行，服务端按索引复制证据并保留 source_start/source_end，避免模型改写换行。
无效索引仅保留合法项，同时返回 extraction_partial、issue索引与
retry_entity_extraction，不能把部分证据称为完整抽取。
校验 evidence 是输入正文的连续原文后，读取带发布来源的 `PublicSkill`，
再由模型输出 equivalent / related / unresolved 和判定理由。

结果保存在同一个候选画像的 `entity_linking`，随 L0 `candidate_profile`
进入任务存储，并随既有确认事务进入 `student_profile.profile_json`。
不另建平行画像表、不改 `skills` 原名、不自动写 canonical_entity 或公共图。
提议不是用户能力已认证，也不把 related 当 equivalent。

## 复用与边界

- opencode `388406238b`，`packages/opencode/src/session/prompt.ts` 的 structured output
  完成/错误分支：只有完整 schema 输出才接受结果。项目使用现有 LLMClient
  + Pydantic JSON Schema，而不是复制 TypeScript 调度器。
- codex `a06545b`，`codex-rs/core/src/tools/handlers/mcp.rs` 的 schema 约束工具边界：
  服务端控制可访问来源，不由模型选择权限/owner。此切片没有新增 MCP 服务。
- 复用现有 Neo4j client、PublicSkill 发布隔离条件、Prompt Registry、LLMClient
  的预算和截断拒绝。没有新建通用 Agent loop。
- 旧 EntityNormalizer 会按向量相似度写公共别名/OOV，旧混合 Skill 向量缺乏
  来源隔离，所以没有直接用于简历归一化，也未改动其历史调用者。

## 状态与限制

`proposed` 表示收到覆盖所有 mention、且候选索引合法的模型提议；仍不发布公共实体。
`unresolved` 保留原 mention，next_action 为 retry_entity_linking。
无引句、检索或模型失败、超预算均明确 unresolved，不使整个简历/报告变成失败。

当前最多 100 mentions、200 公共候选；已替换原 `LIMIT 201` 的全目录方案。
显式运行 `backend/scripts/install_public_skill_search.py` 创建公共技能专用FULLTEXT和
source/name RANGE索引。请求不做DDL：每个mention精确召回 + Neo4j全文召回，至多3候选，
公平合并至200；词法召回不是语义等价，仍由模型裁决。索引缺失/未上线/标签不符时
显式exact_only降级，不读legacy混合索引。

`retrieval` 记录source_version、index、strategy、per_mention_limit、candidate_limit、
每mention候选索引、no_lexical_hit/检索失败/截断、candidate_sha256。它明确exhaustive=false。
全文过程最多取41条原始候选，之后再次检查独占PublicSkill label、发布source、visibility；
污染结果不会进入模型，但可能挤占召回，不能宣称覆盖整个库。

这是有界词法RAG，不是已验证向量RAG。当前hybrid_retrieve面向岗位/role，混合Milvus缺少
相同PublicSkill来源门禁，不复用其不可信数据面。未来需独立可信embedding索引或模型查询扩展
提高跨语言/同义词召回；no_lexical_hit不能解释为不存在等价实体。

Neo4j原生API与配置依据：[Neo4j 5全文索引文档](https://neo4j.com/docs/cypher-manual/5/indexes/semantic-indexes/full-text-indexes/)。
本地已核查原先仅lookup/PersonalSkill索引；迁移后两个PublicSkill索引ONLINE。
真实11项公共目录逐项召回11/11；1000项模拟目录可召回末尾Skill999并产生proposed，
不再因目录超过200整体unresolved。source_version当前为发布者的`repo:public-skills:v1`，
候选内容hash仅证明本次候选，不等于全库内容版本。

本切片尚未把 `entity_linking` 的理由写入 PersonalSkill 投影；已确认简历仍可读到它。
未实现学校/项目/竞赛的全实体消歧，未自动回填历史简历，未实现跨轮主动补问循环。
确认前后的原始 mention 与候选需被后续报告/Agent 当作建议性证据，而非已验证事实。

## 验证

运行 `backend/tests/test_resume_entity_linking.py`。覆盖真实 L0 消费函数接线、原文
证据、只读分区查询、模型 schema、索引越界、重复/缺失结果、目录溢出。
模型/数据库真实探针：`backend/tests/fixtures/resume_entity_linking_live_probe.py`，
读取 student_001 当前已确认 L0 的全部证据块，打印新旧业务域计数、实际 usage 和
finish_reason，不打印简历正文、不写数据库。模拟模型单测不证明模型效果。

主结构化单次输出预算为 6000 tokens；它依然经过 LLMClient 的模型上下文与整轮预算
前置检查，`finish_reason=length` 仍拒绝接受。`structure_calls` 单独记录请求输出预算、
输入字符数、usage、模型和 finish_reason，属于运行审计元数据，不是职业事实。
长文本仍用既有重叠分窗；这不能保证任意长度结果都能合并成功，超限必须保留
失败/部分状态与重试路径，不能声称全文事实均已提取。

### 2026-10-10 真实只读回放

来源 `student_001 / task_56edb2e94056`，28 个已有证据块，4040 字符。
主解析 success=true，新旧业务域计数均为项目4、竞赛8、技能35、研究2。
这证明该回放未丢这些业务域条目数量，不替代逐条语义金标准。

| 阶段 | 输出 tokens | 总 tokens | finish_reason | 结果 |
|---|---:|---:|---|---|
| canonical profile | 1233 | 4122 | stop | 原四域计数保持 |
| 原文segment handle抽取 | 912 | 4221 | stop | 40 mentions，0非法引用 |
| 公共候选语义判定 | 2398 | 6499 | stop | proposed |

三阶段实际总量14842 tokens，未写数据库、未替用户确认任何对齐建议。
此前将mentions附在主schema导致2500输出截断，已改为独立可选窄schema。
让模型自由重打引句会改写PDF换行而全部校验失败，已改用原文segment索引引用；
服务器复制原文，不以模糊匹配或去空白“修复”伪造逐字证据。
