# 技能识别：从词典兜底到带证据的模型候选

日期：2026-10-09。范围仅为 `LLMSkillExtractor → EntityPipeline / 旧 Agent helper`；不是“全平台硬编码清零”声明。

## 复用依据与边界

- 本地 OpenCode commit `388406238bd5ca15564a762840a2362c3a45bd9c`，`packages/opencode/src/session/prompt.ts:createStructuredOutputTool`：用 Schema 验证模型结果，而非猜测/修复语义。
- 本地 Codex commit `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`，`codex-rs/core/src/client_common.rs:Prompt.output_schema/output_schema_strict`：输出契约进入请求。
- 直接复用已安装 Pydantic 2.10.6 的 JSON Schema 生成和严格全量 JSON 校验，以及现有 `LLMClient.chat` 的 provider、上下文预算、turn usage、输出截断检查。没有复制上游实现或另造 Agent runtime。
- Flask 当前同步 LLMClient 已有认证、预算及 provider 配置，不另建一个绕过它的 LangChain ChatModel。模型不支持 JSON Schema 时明确失败，不偷偷切词典。

## 已替换

1. 删除 3000 字输入头截断。完整文本进入已有预算入口；超窗应报错并重试，不声称全文读取。
2. 删除硬/软技能关键词覆盖、技能白名单“幻觉识别”、名称→类别词表、掌握程度→抽取置信度映射。
3. 删除空结果/失败→词典抽取。空 skills 是有效结果；模型/Schema/证据错误是 `SkillExtractionError`，不能混淆。
4. 删除自动别名改写和写回 YAML。结果标记 `llm_candidate`，不是公共技能 ID 或用户确认。
5. 删除同一行出现→自动造共现关系。模型仅返回有原文证据的关系，并校验端点属于本次 skills。
6. 删除 regex JSON 提取/尾逗号修复，避免把截断响应修成看似完整的结果。

保留的确定性校验：Schema 类型/枚举/范围、关系端点完整性、evidence 为输入的精确连续子串。证据子串匹配是来源约束，不是关键词语义识别；它也不能证明模型对否定或语义一定理解正确。confidence 为模型自评，不能当作职业能力评分。

## 验证

在仓库根目录运行：

```powershell
$env:PYTHONUTF8='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_semantic_skill_extraction.py backend/tests/config/test_prompt_loader.py
```

结果：40 passed。覆盖长文本尾部完整传入、分类不被 Python 关键词逻辑覆盖、否定输入的模型空结果不被词典推翻、非法/截断 JSON、无源证据、错误关系端点、超预算错误传播、关闭 LLM 明确失败、失败不进入图谱写入。

一次真实配置 provider 请求使用合成文本：

> 本人从未使用过 Java。项目中使用 Python 编写数据清洗脚本，协助三位同学分工完成交付。

结果：Python（hard）及团队协作（soft），均带准确的输入原文 evidence；没有 Java，relations=[]。这只证明当前 provider 接受该 Schema 且这个样本行为正确，不证明真实简历召回率、全文利用率或稳定质量。没有以这条请求写入用户画像。

## 剩余语义识别与后续边界

| 位置 | 仍存在的机制 | 应如何处理 |
|---|---|---|
| `nsle_pipeline.py:_phase1_parse` | 标题关键词决定教育/经历等块类型 | 改为保留解析器版面块 + 全文语义结构化；不能先按关键词丢块。当前 canonical 简历结构与此旧分支需分别验收。 |
| `nsle_pipeline.py:_rule_based_extract`、`skill_extractor.py` | 旧词典抽取分支 | 其他调用方仍需逐一迁移；本切片不再从 LLMSkillExtractor 进入。 |
| `portrait_generator.py` | 专业字符串判文/理科 | 从确认的结构化教育事实/有来源专业分类解析，未知保持未知。 |
| `matcher.py` | 关键词计数推沟通、抗压、领导力并加分 | 从用户确认且可引用的经历提取评价候选；事实识别不能混进确定性评分公式。 |
| `normalization_agent.py` | 别名文件读写、regex 解析模型 JSON | 不能让模型候选自动成为公共规范；应经有版本目录候选及确认。 |
| `entity_normalizer.py` | 相似度阈值自动登记别名、分类错误兜底“其他” | 区分已审核目录精确 ID 查询与模型建议；相似并不等于同义。 |
| `resume_structure.py` | 描述截断、缺字段默认值、合并失败只取一个 partial | 后续应显式覆盖范围、缺失字段和 partial，不能将失败当全文结构完成。 |
| 旧 Agent helper | 捕获技能抽取异常后继续返回已有实体 | 需把阶段失败纳入主 Agent outcome，不能用日志代替用户可见状态。 |

不应删除：Principal/资源归属检查、MIME/协议/格式解析、来源 ID 和 Schema 验证、冻结领域约束。把这些改为模型判断会破坏安全和可复现性。

当前 EntityPipeline 的历史路径仍有直接图谱写入、graph_saved 计数字典而非权威读回、证据未随业务实体持久化的问题。新技能对象是候选，不代表该旧路径已具备用户确认及可靠持久化闭环。
