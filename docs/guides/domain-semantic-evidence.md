# 领域语义：完整候选、证据、确认，再评分

## 本次范围与来源

2026-10-10：NSLEPipeline、resume_structure、PortraitGenerator、matcher。
参考本地 `opencode` commit `388406238bd5ca15564a762840a2362c3a45bd9c`，
`packages/opencode/src/session/prompt.ts` 的 StructuredOutput：语义结果必须进入结构化契约，而不是由输出文本关键词触发业务结论。
薄适配直接使用当前 mkg 的 Pydantic 2.10.6 `BaseModel`/`model_validate`，复用项目的 LLMClient、确认快照和评分配置；不复制 TypeScript Agent loop。

## 可达链路与改动

`L0ParseDocumentPipeline` → `NSLEPipeline.run_resume_text` → `extract_canonical_profile`
→ 用户确认的 profile snapshot → `L2PortraitPipeline` → `PortraitGenerator`。
发布岗位来源通过后，`verified_match_report.persist_match` 消费 `matcher.calculate_match_score`。

- 简历 NSLE 不再用标题关键词决定块去向，不再额外调用多套分节模型并把输出混为真相。
- 原始文本完整交给 canonical extractor；不再在 section helper 静默切 1000/1500 字，也不再在模型失败时切到技能字典。
- canonical 分片失败显式 `partial_chunk_extraction`、`failed_offsets`、`retry_extraction`，结构校验不再通过。
- 学校名称不推断 985/211，专业名称不猜文理；仅来源支持且经确认的显式类别可进入既有分数表。
- D3 不根据沟通/负责等关键词或项目数量加分，不为缺失资料补 0.3、50 或弱等级。D3 评分消费明确的 `semantic_facts`。
- matcher 的 D4 复用 PortraitGenerator 的 canonical D4 配置评分，不再维护另一套主席、负责、CCF、EI、前5%文本猜测。

## 事实与输出契约

`semantic_facts[key]` 为 `{value, evidence, source_sha256}`；Pydantic 验证值类型，提取时验证 evidence 为原文连续引句，绑定全文 SHA256。
消费者必须有 `_resume_confirmation.task_id`，且 fact 与 profile 的 source hash 一致。
引句存在只证明引用来自输入，不证明模型推断正确，因此这里的等级只接原文明示的自述，不把模型置信度当人格能力分。
生产确认服务仍须按 owner 校验并持久化快照；此函数不是权限边界，也不能接受客户端自造的 confirmation/hash。

无证据时：

- Portrait 返回 `status=partial`、`missing_fields`，D1/D3 `dimension_score=null`，summary `competitiveness_score=null`、level `unknown`。
- Match 返回 `status=partial`、`total_score=null`、`d3_score=null`、`match_result=false`、level `unknown`。
- 上游必须停止“完整评分/完成报告”写入，不能用 `or 0` 填库。数据库不支持可空时，应持久化 partial 任务和缺失项，不能写伪分数。

## 验证与仍未完成

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q backend/tests/test_domain_semantic_evidence.py backend/tests/test_nsle_profile_adapter.py backend/tests/pipeline/test_portrait_generator.py
```

覆盖未知标题/尾部短文本保留、关闭模型不回退字典、关键词堆砌不评分、伪引用丢弃、确认和 hash 门禁、显式事实复用原分数表、分片失败显式化。
早期检查曾无可重放简历；2026-10-10 后续实测已存在已确认 L0 与 partial L2，见下节。仍不声称已完成 provider→新候选确认→完整 L2/L3 的业务验收。

剩余：长期学校/专业权威目录与非自述软技能评估量表尚未接入；canonical 其他字段不是逐字段引用模型；D2/D4 的“未提到”与“确无经历”仍需扩展确认 schema，不能认为本次消除了全平台所有语义规则。

## 后续实测：模型能补什么，不能伪装什么

2026-10-10，owner 校验后从 MySQL `task_56edb2e94056` 读取 `params_json.file_path`，
对该来源重新解析，全文 SHA256 与已确认 profile 相同：
`cd31df1d440de5630f494b44346faff7834262cf3cf63a7b44f8dd8bb35257be`。
共 4069 字、3 页、28 块（各页 10/17/1），没有未提取页；这只证明来源/覆盖，不证明 OCR 字符准确率。
最新 L2 `task_c2357837ba76` 是 partial，缺学校类别、专业类别及 D3 四项。

发现并修复两处数据流问题：

1. 原 prompt 不向模型提供评分表已有的专业枚举，且自由文本 JSON 会漏 `semantic_facts` 或给出错误类型。
   现在复用 `LLMSkillExtractor` 的 Pydantic → `response_format=json_schema` 协议，并做本地校验；
   专业枚举来自现有配置，不写学校名/语义词匹配分支。
   原文与枚举完全一致的专业名可以提取为候选事实，不是猜测专业大类。
2. 长文 map 原先没有相同证据契约，reduce 丢失全部 `semantic_facts`。
   现在每个窗口先验引句，合并已验证的事实原子并绑定全文 hash；冲突值不按先后覆盖，
   新造的 reduce 事实不采纳。无匹配引句显式记 `semantic_fact_issues` 与 `retry_fact_extraction`；
   冲突记 `semantic_fact_conflicts` 与 `review_conflicting_facts`。

开源复用依据：除上述 opencode StructuredOutput 外，检查本地 Codex commit
`a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的
`codex-rs/protocol/src/request_user_input.rs`，确认结构化问题/答复与批准状态分离；
本次不复制 Rust 交互 loop、不新增确认端点，继续使用项目已有 LangGraph `interrupt` 确认边界。

真实 qwen-plus 的 structured-output 调用已产出原文精确引用支持的专业类别及部分行为事实；
正常 `finish_reason=stop`，上下文预算可容纳。先前只有 prompt、无 schema 的调用虽然正常结束，
仍会漏字段/给出错误结构，因此不能用“模型调用成功”代替结构化结果验收。
新候选不继承旧 `_resume_confirmation`，也没有自动写入学生资料；原权威 profile 回读保持不变。

| 缺口 | 现有证据能支持什么 | 不能替代的判断 |
|---|---|---|
| major_category | 第 2 页专业全称与配置枚举逐字一致，模型可直接提取并给原文引用 | 不需要用户再发明一个专业类别；新候选仍需真实确认 |
| school_tier | 校方公开资料说明学校是省属公办普通本科高校 | 配置只有 985/211/双一流/一本/二本/三本/专科院校；不能把普通本科硬映为二本 |
| communication / teamwork / responsibility | 简历有组织讨论、合作、承担开发职责等行为材料，可供模型做有引用的候选评价 | 行为证据不是明确的“强/中/弱”自述，更不是客观量表成绩 |
| stress_tolerance | 当前材料未明确说明压力情境、个人应对及其结果 | 系统稳定性工作、黑客松参赛不能自动证明抗压等级 |

学校类型依据：[校方 2026 年普通本科招生简章第三条](https://sxtjxy.suse.edu.cn/2026/0628/c1117a211750/page.htm)。
该资料没有提供当前评分表所需的唯一层级值，故仅作为待评审证据；本次没有把网络内容写成简历来源事实。

### 可操作的恢复与确认请求

- **立即可做**：重新解析生成结构正确的模型候选，经现有 `confirm_resume` 流程核对；不能用旧确认标记批准新模型结果。
- **D3 的现有兼容入口**：用户若确实认可四项强/中/弱自述，可在补充材料中明确写出，再走 L0 → 核对候选 → 确认。
  自述必须保留自述性质；不能声称用户填写后就证明客观能力。
- **更合理但尚未实现的产品决定**：是否允许“模型基于行为证据的候选评价”作为独立来源进入评分，
  并保留 `assessment` 而非 `fact` 的 provenance。若接受，应先约定量表、证据不足规则与确认契约；
  模型先做提取/评价，人只需核对错误，不必手填所有字段。
- **学校类别仍需修订规则边界**：先决定将学校的权威属性与招生批次分开，还是要求具体入学年份/省份/专业的批次证据。
  用户的个人自评也不能填补类别表本身没有合适值的问题。

因此当前仍保持 partial、未知分数为 null，不生成可供真实匹配消费的“已完成画像”。

### 2026-10-14：同名类别误置的窄域回归

`其他理工` 是 `major_category_score` 的合法值，不在 `school_tier_score` 中。即使模型给出
`school_tier={value:"其他理工",evidence:"其他理工"}` 且引句确实存在于来源，逐字引用只证明
字符串来自简历，不能证明这是学校层级。L0 现在**保留**该候选事实、引句和来源 hash，同时给
`semantic_fact_issues=[{field:"school_tier",reason:"value_not_in_scoring_category"}]`、
`semantic_fact_next_action=review_unmapped_scoring_category`。这不是把它改成合法层级，
也不阻断其他条件已满足的简历候选确认。有效的 `major_category="其他理工"` 仍按专业表处理。

L2 对此类已确认输入继续返回 `partial`：原始学校值仍可读，`sub_school=null`、
`school_score_basis=no_tier_mapping`、总分 null；只持久化 partial 任务，不写入评分画像。
简历诊断报告保留此逐字引句，但该条标记 `scoring_status=unmapped_category`，
并在 `limitations` 说明学校层级类别不可评分；报告不能把引句位置当作评分授权。
`next_action=confirm_semantic_facts` 是机器代码，当前无语义事实修改/续跑入口，
`action_available=false`；不能将其解释为用户现在即可通过页面补填完成。用户仍可从
L0 候选回读原始材料，后续学校类别的评分映射须另行决定，不能把“其他理工”或普通本科
擅自填成一本/二本。

用户已确定人工修正学校评分档位的证据门槛：**只接受可核验的学校官方或教育主管部门材料**。
学生自报、模型推断、一般网页与仅出现学校名称的简历引句均不能批准映射；校方材料若只证明
“普通本科”，却不能唯一落到当前 `school_tier_score` 档位，也仍不得补分。将来若实现审核入口，
须把学校原名、精确档位、官方来源定位/摘录、审核账号、审核时间和适用的 L0 确认版本绑定，
在生成 L2 前回查，并让撤回或换版后的旧画像不再可用。当前没有这个入口，不能把本口径误报为已修复的 L2 恢复能力。

回归用合成的、含错置类别及可评分 D3 自述的 L0 `needs_input` 候选，
先经真实 MySQL 的 `confirm_resume_candidate` 事务确认（仅个人图谱投影替身，不测试 LangGraph/确认 HTTP），再通过
`GET /api/profile/student/candidate/<task_id>` 和 `POST /api/profile/student/pipeline`
验收：前者可读原引句/错误类别标记；后者 HTTP 200 但业务状态 `partial`，权威
`pipeline_task` 为 partial，`student_portrait_4d` 无该学生行，清理后无临时数据。
回放：

```powershell
$env:CPA_TEST_LIVE_MYSQL='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_l2_unmapped_school_tier_mysql.py
```

该测试替代不了真实模型重新生成、用户亲自确认或真实学校层级规则签审；旧
`student_001/task_56edb2e94056` 已不再是当前确认源，只读探针按当前源门禁拒绝，
不复用旧候选或旧 task ID。

该窄修仅在既有 Pydantic 校验与来源绑定之后，增加评分表成员检测及报告投影标记，
没有自建新的 Agent 解析/确认状态机。复核本地 OpenCode
`388406238bd5ca15564a762840a2362c3a45bd9c` 的
`packages/opencode/src/session/prompt.ts:createStructuredOutputTool`（结构校验在接收前），
以及本地 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的
`codex-rs/protocol/src/request_user_input.rs`（结构化候选与用户答复分开）；
两者都不提供本项目的学校评分词表，业务类别校验仍由现有 repo rules 薄适配完成。

### 只读重放

```powershell
$env:PYTHONUTF8='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B backend/tests/fixtures/l2_semantic_evidence_probe.py --student-id student_001 --task-id task_56edb2e94056
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_resume_semantic_fact_transport.py backend/tests/test_domain_semantic_evidence.py backend/tests/test_nsle_profile_adapter.py backend/tests/pipeline/test_portrait_generator.py
```

探针在真实 MySQL 回读当前确认行，校验来源 owner/类型/hash，再调用真实配置模型与 L2 计算器。
它不新建 Pipeline task、不改 profile、不执行用户确认；新候选进入 L2 仍为 unconfirmed/partial，
最后再次读取权威 profile 做相等校验。相关测试 50 passed。
