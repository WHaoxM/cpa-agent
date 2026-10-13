# L2 学校类型与行为评价候选契约

## 目的与边界

模型负责从材料提出评价，用户不必逐项发明强/中/弱答案；但模型评价不伪装为确认事实。
本切片只包含领域生成器、Pydantic schema、repo 量表和只读重算探针。
没有扩展确认 API、没有修改前端或为用户执行确认，也没有改动岗位/匹配发布链路。

## 两条独立证据通道

- **D1 客观属性**：`school_type` 是带来源引句的学校办学类型观察，与 `school_tier` 分开。
  普通本科、公办、省属不是一本/二本等招生批次，学校名称不用于关键词匹配。
  当前分数表没有这种学校属性的授权映射，因此 `sub_school=null`，`school_score_basis=no_tier_mapping`。
  人工审核学校类型也不会自动补出招生批次或学校得分。
- **D3 模型评价**：`portrait_assessment.assessment` 的四项各含 `level|null`、`confidence`、
  `rationale`、`evidence[{source_id,page,quote}]`、`situation/action/outcome`。
  `confidence` 是模型对判断的把握，不是人格能力分、不当作统计校准概率。
  缺行为资料必须 null，不能判弱；抗压有值必须具备压力情境、个人应对和结果三类原文引用。
  代码只校验结构、引用和门禁，情境是否真正证明抗压仍是待审的语义判断，不能声称 schema 证明能力。

`provenance.kind=model_assessment`，记录实际 provider/model、量表版本、简历全文 SHA256、
完整来源列表 SHA256；`candidate_sha256` 绑定整份候选。外部学校来源的 `source_id` 使用实际 URL、
`page=null`，不会把学校网站引用写成简历页事实。简历 source_id 使用稳定标签 `resume`，
输入来源另附 owner 核验后的 `source_task_id`，整体来源摘要覆盖该绑定。

## 确认与计分

现有已确认原文自述仍能按旧表计分，输出 provenance 标为 `confirmed_self_description`；
模型候选不写入 `semantic_facts`、不覆盖已确认自述。

模型经 `PortraitAssessmentSelection` 严格 Pydantic 校验，仅选择服务端生成的 evidence ID；
服务端按本次冻结块索引回填原文 quote/page/block_id/quote_sha256，再验证 `PortraitAssessmentProposal`。
ID 绑定简历全文摘要、来源、源任务、页码、块 ID 和文本；跨快照/外来 ID 不可解析。
个人行为引用只能来自 resume 类型块，不能借学校网页凑证据；模型无法输出 quote 或 confirmation 字段。
无证据、引用错页、provider 截断、预算拒绝或不合法结构，均返回显式 `partial` 和重试/审阅动作；不回退关键词评分。
所有来源原文整体进入现有 LLMClient；超窗由现有客户端拒绝，不静默剪切。

计分要求受信服务端 profile 快照中的 `_portrait_assessment_confirmation` 包含：

```json
{
  "source_task_id": "同一 _resume_confirmation.task_id",
  "candidate_sha256": "用户所审具体候选的摘要",
  "confirmed_by": "服务端可信主体",
  "confirmed_at": "服务端确认时间"
}
```

该字典不是鉴权机制。未来接线必须从 Principal 确认 owner、读取服务端保存的候选与来源快照，
逐页重新验证，并在真实人工提交后原子落库；绝不能从请求 JSON 接受这个字典。
本切片没有此类 API，也没有自动填入确认标记。仅旧 `_resume_confirmation` 不批准新评价；
来源/候选/量表变化立即使旧 approval 不适用。

即使评价经人工审核，来源不足项仍为 null；D1 无映射和 D3 未知项继续使画像 `partial`，
总分 null。审核代表认可该评价可使用，不把评价变成客观心理测量事实。

## 复用依据

- 本地 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`（MIT），
  `packages/opencode/src/session/prompt.ts:createStructuredOutputTool`：schema 验证后才接收结果。
- 本地 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`（Apache-2.0），
  `codex-rs/protocol/src/request_user_input.rs`：结构化问题/答复与模型结果分离。
- 运行时直接复用项目已有 Pydantic 2.10.6、LLMClient JSON Schema 协议、provider usage 和 dispatch budget。
  没有移植 TypeScript/Rust Agent loop，没有新增通用确认状态机。字段语义、量表与映射拒绝是本项目领域薄适配。

schema 放在 entity_extractor 内，与画像领域同址；未通过 `app.schemas.__init__` 引入不相关 EmailStr 依赖。

## 回放

```powershell
$env:PYTHONUTF8='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_portrait_assessment_contract.py backend/tests/test_domain_semantic_evidence.py backend/tests/pipeline/test_portrait_generator.py
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B backend/tests/fixtures/l2_behavior_assessment_probe.py --student-id student_001 --task-id task_56edb2e94056 --official-school-url 'https://sxtjxy.suse.edu.cn/2026/0628/c1117a211750/page.htm'
```

探针核验 MySQL 源任务 owner/type、当前已确认任务和重新解析全文 hash；再从任务中读取所有页证据。
外部学校 URL 由运行者显式选择，不按学校名猜站点或自动判“权威”。模型按完整来源生成候选，
候选只放入进程内副本用于回算，结尾再次读取权威 profile，校验完全未变。
本探针不创建任务、不更新画像/简历、不确认、不发布报告。模型输出及重新抓取网页可能随回放变化，
比较实际 provenance/hash，不把不同候选当同一版本。

已通过 93 项相关测试，包含旧事实契约、画像、确认金路径、原子事实传输与规则 loader 回归；真实 provider 结果以本轮探针为准。

### 2026-10-10 实际只读结果（未通过候选验收）

源 `task_56edb2e94056`，全文 4069 字、3 页，重新解析 SHA256 为
`cd31df1d440de5630f494b44346faff7834262cf3cf63a7b44f8dd8bb35257be`，与确认快照一致。
三轮真实 qwen-plus 均 `finish_reason=stop`、`context_fits=true`，总 tokens 依次为
6776、6302、6327。第一轮证据校验失败后增加逐字段匿名诊断；后两轮证实
`assessment_quote_not_in_source_page`，最后一轮已用稳定 `resume` 标签且学校来源及部分职责引用成功，
但其余职责/团队引句在任何输入来源中都不存在，因此不是只有页码或 source_id 的问题。

全部候选被拒绝，没有把正确的学校引用与错误的行为引用拼成“验收通过”。
L2 `partial`，学校与 D3 分数 null，总分 null，三次回读权威 profile 均未改变、`writes=0`。
停止同类在线重试，没有降低引用门禁。学校类型与招生批次的规则缺口独立存在，
即使模型成功输出候选也不能用普通本科补出二本。

因此本次完成的是**候选/确认/来源契约和负向运行验证**，不是有效模型候选或正式 L2 完成验收。
后续已完成 evidence ID 结构性适配，量表版本升级为 `behavior-evidence-id-v2`。
模型只选 ID，服务端按已核验块回填原文；不做模糊匹配、不让模型写 quote/page/hash。
本地测试覆盖块重复、负页码、外来文本、外来 owner/page/content 的 ID、学校块越界用于行为、
抗压三类情境引用缺失以及候选不能自动计分。ID 只能证明来源，不证明所选块的语义真的支持抗压；
该语义仍需模型提出、真实人工审核，不能以校验 ID 代替评估。

收口时尚未进行 evidence ID 版本的真实模型调用；不能将此前三轮旧 quote 协议的运行结果视为新版通过。
可用 `--validate-only` 对真实源块验证 ID，不调用模型、不写数据库。
本轮实际 `--validate-only` 已通过：同一源任务 3 页、28 块、28 个唯一 ID，
`model_calls=0`、`writes=0`、`authoritative_profile_unchanged=true`。
