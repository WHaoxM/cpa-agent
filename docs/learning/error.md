1. 错误诊断：为什么会出现 parse_error？
报错 Extra data: line 36 column 2 是因为模型进入了“自回归幻觉循环”。


现象：模型在输出完第一个完整的 JSON 后，没有停止，而是接着输出了第二个 JSON 的开头 。

原因：0.6B 模型的 EOS（停止符）预测能力较弱，或者在长文本 Prompt 下失去了对输出长度的控制。

后果：下游系统的 JSON 解析器（如 json.loads()）在读取完第一个闭合花括号后，发现后面还有字符，直接报错崩溃。

2. 逻辑层溃败：严重的“幻觉”与“降级”
从提取的内容来看，模型出现了典型的 语义漂移：


学历虚构：模型将“多模态课程知识库”和“鉴定推荐项目”识别成了“硕士学位” 。实际上你是一名本科在校生，模型由于无法理解“项目经历”与“教育经历”的 Block 边界，为了强行填充 Schema 中的字段而进行了胡乱关联。


关系断裂：它将“人工智能协会副部长”归类到了 work_experience（工作经历）中 。这在你的 v1.1-Relational Schema 中属于典型的实体归属错误。


结构化降级：虽然它识别出了技能词汇，但由于你使用的 Prompt 是基础版 Schema，模型完全丢失了你最核心的 parent_entity_id 逻辑 。

3. 与 SOTA 榜单的落差分析
参考你之前发现的阿里榜单数据，Qwen3-0.6B 在 RealResume 数据集上的 F1 值高达 0.964。之所以在你这里“翻车”，是因为：

版面复杂度：榜单测试通常使用清洗过的短文本，而你的简历包含复杂的项目描述和嵌套的职责。

Schema 适配度：榜单模型通常针对特定的简单字段（姓名、学校、公司）进行过过度拟合（Overfitting）。一旦面对你这种带有“Agent、知识图谱、LangGraph”等前沿技术栈的简历，模型就会因缺乏领域知识而产生混乱。


引入 Constrained Decoding (约束解码)：

不要让模型自由输出 JSON。

方案：使用 Outlines 或 Instructor 库。通过正则表达式或 JsonSchema 强制约束模型在每一个位置只能生成符合格式的 Token。这能 100% 解决 Extra data 和 parse_error 问题。

分块抽取策略 (Chunking Strategy)：

不要一次性把整份简历丢进去。

方案：先用一个微小的模型识别出 block_type（如：这块是教育，那块是项目），然后再分 Block 调用 0.6B 模型进行内部分析。这样可以规避“把项目当学位”的低级错误。

微调 (SFT) 是唯一出路：

目前的测试证明，Zero-shot（零样本）下 0.6B 模型无法理解你的 v2.8.0 契约。

你需要手动标注 50-100 份符合 v1.1-Relational 逻辑的 JSON 样本，对 Qwen3-0.6B 进行针对性微调，教会它什么叫 parent_entity_id。

---

## Bug #2: MinerU v2 嵌套格式解析失败 → physics_result.json 返回 0 block（已修复）

**日期**: 2026-04-02  
**严重性**: P0（阻断整条管线）  
**根因**: `physics_analysis.py` 的 `_extract_text()` 只读取顶层 `text/content/value`，无法解开 MinerU v2 的嵌套结构 `content.title_content[].content` / `content.paragraph_content[].content`  
**影响**: `full_text_coverage = 0.0`, `block_count = 0`，整条管线无输出  
**修复方案**: 在 `phase1_physical_layer.py` 中新增 `parse_mineru_v2_item_text()` 函数，递归解开嵌套格式；同时合并 `block_router.py` 的 `item_text()` 逻辑作为规范解析器  
**修复文件**: `mvp/entity_extractor/phase1_physical_layer.py`  
**验证**: 修复后解析出 13 blocks，分类覆盖率 76.9%

---

## Bug #3: YOLOv10 ONNX 坐标格式误判 → 检测结果 0 区域（已修复）

**日期**: 2026-04-02  
**严重性**: P1  
**根因**: SmartResume 的 YOLOv10 ONNX 模型输出格式为 **(cx, cy, w, h, score) normalized [0,1]**，而非标准 YOLOv10 的 `(x1, y1, x2, y2, score)` 像素坐标  
**诊断方法**: 检查 raw detections 发现某些检测框的 `column[3] < column[1]`（即 "y2 < y1"），证明不可能是 xyxy 格式  
**修复方案**: 在 `yolo_layout_detector.py` 的 `detect()` 中，先将 normalized (cx,cy,w,h) 乘以 640 转为像素坐标，再 cxcywh→xyxy，最后去除 letterbox padding 并缩放回原图  
**修复文件**: `mvp/entity_extractor/layout/yolo_layout_detector.py`  
**验证**: 修复后检测到 5 个 layout regions，坐标正确覆盖简历页面各区域

---

## Bug #4: Phase 3 UncertaintyMeasure hardcoded confidence=0.8（已修复）

**日期**: 2026-04-02  
**严重性**: P2  
**根因**: `phase3_reasoning_core.py` 的 `ReasoningCore.infer()` 返回 `"confidence": 0.8, "is_high_uncertainty": False`，完全没有使用 `UncertaintyMeasure` 类  
**修复方案**: 使用 `model.generate(output_scores=True, return_dict_in_generate=True)` 获取每个 token 的 logits，计算真实 Shannon entropy，聚合为序列级置信度  
**修复文件**: `mvp/entity_extractor/phase3_reasoning_core.py`

---

## Bug #5: Phase 3→4 Schema 断层 → total_entities=0（已修复）

**日期**: 2026-04-02  
**严重性**: P0（所有实体丢失）  
**根因**: ReasoningCore 输出 task-specific JSON（如 `{"skills": [...]}`），但 LogicGuard.sanitize_output() 期望 `{"entities": [...], "relations": [...]}`。两者结构完全不匹配，LogicGuard 返回空  
**修复方案**: 新增 `output_adapter.py`，在 Phase 3 和 Phase 4 之间做格式转换：task-specific KV → 契约 entities/relations。LogicGuard 只做验证不做转换  
**修复文件**: `mvp/entity_extractor/output_adapter.py` + `nsle_pipeline.py`

---

## Bug #6: 块分类 76.9% → 实体名标题被判为 OTHER（已修复）

**日期**: 2026-04-02  
**严重性**: P0  
**根因**: classify_heading() 只做关键词匹配（"教育经历"→EDUCATION），但中文简历的二级标题是实体名（"四川轻化工大学"、"省重点人工智能实验室"），不包含章节标签关键词  
**修复方案**: 三级分类策略 — (1) 一级章节标签匹配 (2) 实体名标题分类（heading+content 双信号：大学→EDUCATION, 实验室→PROJECT_EXPERIENCE） (3) content-based fallback  
**修复文件**: `mvp/entity_extractor/phase1_physical_layer.py` (apply_section_scope_inheritance + _classify_entity_heading)  
**验证**: 覆盖率 76.9% → **100%**

---

## Bug #7: PII ADDRESS 正则误杀 "省重点实验室"（已修复）

**日期**: 2026-04-02  
**严重性**: P1  
**根因**: ADDRESS 正则 `[\u4e00-\u9fa5]{2,6}(?:省|自治区)...` 太宽泛，"省重点" 中的 "省" 触发匹配  
**修复方案**: 在 "省" 后添加负向先行断言 `(?![\u4e00-\u9fa5]*(?:重点|建设|发展|级别|级|属|厅|委|办))`  
**修复文件**: `mvp/entity_extractor/dictionaries/pii_patterns.py`

---

## Bug #8: SkillMatcher 单字符 "C"/"R" 误匹配（已修复）

**日期**: 2026-04-02  
**严重性**: P1  
**根因**: Aho-Corasick 子串匹配对单字符无边界保护，"R" 匹配 "Learning" 中的 r，"C" 匹配 "LangChain" 中的 C  
**修复方案**: 按长度分组 — 长度 <3 的技能词用带边界保护的正则（要求前后有空格/标点），长词用 AC 算法  
**修复文件**: `mvp/entity_extractor/skill_matcher.py`

---

## Bug #9: LogicGuard 日期正则过严 → "2024/11 - 至今" 被拦截（已修复）

**日期**: 2026-04-02  
**严重性**: P0（导致 b_12 等块 INVALID，实体丢失）  
**根因**: `ConstraintMatrix.date_format = r"\d{4}-\d{2}(?:-\d{2})?"` 只认 hyphen 分隔的 YYYY-MM 格式，不兼容 slash (`2024/11`)、dot (`2024.06`)、中文 (`2024年6月`)、泛时间词 (`至今/现在/present`)  
**影响**: DATE_RANGE 实体全部被 LogicGuard 过滤，导致块变为空实体触发 SFT gate 兜底，最终标记 INVALID  
**修复方案**: 替换为泛化日期验证 — 3 条宽松正则（年份+多分隔符 / 纯年份 / 终止词），只要包含 4 位年份或时间终止词即合法  
**修复文件**: `mvp/entity_extractor/phase4_logic_guard.py`  
**学习点**: 企业级日期解析必须“宽进严出”—— 输入层包容多格式，输出层再标准化

---

## Bug #10: PROJECT_NAME 长度限制 15 字符 → 中文项目名被截断（已修复）

**日期**: 2026-04-02  
**严重性**: P1（知识图谱主键缺失）  
**根因**: `validate_entity()` 中 `len(span) > 15` 拒绝超长项目名，但中文项目名如 "多模态课程知识库与 Learning Agent 系统"(21字符)很容易超限  
**影响**: heading 注入的 PROJECT_NAME 被 LogicGuard 截掉，下游知识图谱产生“孤儿节点”（技能/角色无法挂载到项目主键）  
**修复方案**: 长度限制 15 → 50  
**修复文件**: `mvp/entity_extractor/phase4_logic_guard.py`

---

## Bug #11: 管线顺序错误 → Stateful Context 无法挂载 fallback 实体（已修复）

**日期**: 2026-04-02  
**严重性**: P0（b_40 等纯技能块 7 个 SKILL 全部变 INVALID）  
**根因**: Stateful Context 状态机在 SFT gate 之前执行。当 LLM 空输出时，状态机看到 0 个 entities，无法继承锚点。SFT gate 再填充 fallback 实体，但此时继承窗口已关闭  
**影响**: fallback 实体（如 7 个 SKILL）无法从上下文继承 PROJECT_NAME/COMPANY 锚点，全部变成孤儿节点，触发 INVALID  
**修复方案**: 重排管线顺序 — LogicGuard → **SFT gate** → **Stateful Context**。这样状态机能看到 fallback 实体并挂载继承锚点  
**额外优化**: SFT 双层门控 — `is_valid=true`（知识图谱可用）+ `_sft_eligible=false`（禁止进入 SFT 训练池）  
**修复文件**: `mvp/entity_extractor/nsle_pipeline.py` + `phase4_logic_guard.py`  
**学习点**: 管线中的“填充”和“继承”是两个独立关注点，必须按 **填充先、继承后** 的顺序执行

---

## Bug #12: data_factory.py L147 re.escape 括号不匹配（已修复）

**日期**: 2026-04-04  
**严重性**: P0（SyntaxError，NSLEPreExtractor/ConsensusGuard/DataFactory 全部无法实例化）  
**根因**: `re.compile(rf"...{re.escape(suffix}|{suffix}")` 缺少右括号 `)`，且正则本身逻辑有误  
**修复方案**: 修正为 `re.compile(rf"([\u4e00-\u9fa5]{{2,15}}{re.escape(suffix)})")`  
**修复文件**: `mvp/entity_extractor/self_learning/data_factory.py`

---

## Bug #13: resume_generator.py 缺少 Tuple 导入（已修复）

**日期**: 2026-04-04  
**严重性**: P0（NameError）  
**根因**: `ResumeValidator.validate()` 返回类型标注为 `Tuple[bool, str]`，但 `from typing` 未导入 `Tuple`  
**修复方案**: 在 typing 导入中添加 `Tuple`  
**修复文件**: `mvp/entity_extractor/self_learning/resume_generator.py`

---

## Bug #14: student_profile_schema.json 字段名前导空格（已修复）

**日期**: 2026-04-04  
**严重性**: P0（字段永远取不到值）  
**根因**: `" GPA"` 有前导空格，`data.get("GPA")` 永远为 None，JSON Schema validation 也无法正确匹配  
**修复方案**: `" GPA"` → `"GPA"`  
**修复文件**: `mvp/entity_extractor/self_learning/student_profile_schema.json`

---

## Bug #15: competition_data_generator.py batch_job 模式无 generator（已修复）

**日期**: 2026-04-04  
**严重性**: P1（NameError）  
**根因**: `batch_job` 进入 `else` 分支但没有实例化 generator，运行时 `NameError: name 'generator' is not defined`  
**修复方案**: 新增 `elif args.mode == "batch_job"` 分支，实例化 `JobPostingGenerator`  
**修复文件**: `mvp/entity_extractor/self_learning/competition_data_generator.py`

---

## Bug #16: data_factory.py _call_api 重试不降级 provider（已修复）

**日期**: 2026-04-04  
**严重性**: P1（多 provider 降级形同虚设）  
**根因**: 每次重试都走同一个 provider 分支（if/elif 结构，第一个匹配即 return/throw），异常重试后仍进入同一个 provider  
**修复方案**: 提前构建 `available_providers` 列表，重试时按 `attempt % len(available_providers)` 轮换  
**修复文件**: `mvp/entity_extractor/self_learning/data_factory.py`

---

## Bug #17: competition_data_generator.py 多行 JSON 解析失败（已修复）

**日期**: 2026-04-04  
**严重性**: P1（LLM 返回多行 JSON 时 `generate_batch` 返回空列表）  
**根因**: 按 `\n` split 后逐行 `json.loads()`，多行 JSON 每行都不是完整 JSON → 全部 catch → 返回 `[]`  
**修复方案**: 先去除 markdown 包裹，再尝试 JSONL 逐行解析；失败后 fallback 到整体 `json.loads()`（兼容 JSON 数组和单对象）  
**修复文件**: `mvp/entity_extractor/self_learning/competition_data_generator.py`

---

## Bug #18: data_factory.py extra_entities 恒为空集（已修复）

**日期**: 2026-04-04  
**严重性**: P1（质检统计不准确）  
**根因**: `consensus_entities - api_spans` 中 `consensus_entities = api_spans & rule_spans`，交集减去超集恒为空  
**修复方案**: 改为 `api_spans - consensus_entities - conflict_spans`，正确表达"API 独有的可疑实体"  
**修复文件**: `mvp/entity_extractor/self_learning/data_factory.py`

---

## Bug #19: competition_prompts.py 四维度权重与冻结 Schema 不一致（已修复）

**日期**: 2026-04-04  
**严重性**: P1（训练数据与冻结规则偏差）  
**根因**: 匹配示例中权重为 0.3/0.35/0.2/0.15，但冻结 Schema 为 0.25/0.35/0.20/0.20  
**修复方案**: `basic_requirements.weight` 0.3→0.25，`development_potential.weight` 0.15→0.20  
**修复文件**: `mvp/entity_extractor/self_learning/competition_prompts.py`

---

## Issue #3: data_factory.py company_suffixes 重复元素（已修复）

**日期**: 2026-04-04  
**严重性**: P2  
**根因**: `"有限公司"` 在列表中出现两次，导致重复匹配  
**修复方案**: 移除重复项  
**修复文件**: `mvp/entity_extractor/self_learning/data_factory.py`