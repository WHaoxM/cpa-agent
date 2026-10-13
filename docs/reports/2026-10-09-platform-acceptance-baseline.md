# 专项验收基线：文档、预算与 Ask 建议

日期：2026-10-09。读者：后端、测试与人工质量验收人员。目的：提供可重复的门槛与明确缺口，**不是专项验收已通过报告**。

前置证据：已读取 `2026-10-09-resume-page-extraction-audit.md` 与 `2026-10-09-agent-document-context-evaluation.md`。历史 OCR 两次结果相似度、一次主模型直连回答，都不能替代人工逐字真值或完整 Ask API 的职业建议验收。

## 本轮新增、实际观察

| 项目 | 方法 | 观察 / 边界 |
| --- | --- | --- |
| DOCX 文本框 | 临时目录生成脱敏 OOXML VML textbox | `BODY_SENTINEL` 与 `SHAPE_SENTINEL` 均进入 `extract_docx_text`。 |
| DOCX 组合形状 | 同一 fixture 外包 `v:group` | 当前库也能提取内部文字；只验证这一种合成结构，不代表 Word 的所有 DrawingML、SmartArt、浮动顺序。 |
| DOCX 嵌图文字 | 仅在 PNG 中绘制合成 `RASTER_ONLY_EVIDENCE`，嵌入 DOCX | 纯文字适配器未识别图像中的字符，明确保留严格 xfail。未跑图像 OCR，不虚称多模态完整。 |
| OCR 字符准确率 | 标准 Levenshtein CER 计分器；合成替换/删除/插入负测 | 算法回归通过；未提供人工逐字真值，故真实 PDF CER **未验收**。插入多时 CER 可大于 1，不应强行夹到 0–1。 |
| 全链 token | 调用清单 + 每次 provider usage + 最终消息 preflight + 回合预算 cap | 检查器拒绝只有最后一次 usage、缺专家调用、未知 fits、未检查最终消息、超总 cap、length 截断。后续已接同步 LLMClient/LangGraph request-local 账本，仍不是后台/直连 SDK 的全链账本。 |
| LangGraph 调用前预算 | 替换网络候选为 spy，调用 `_build_llm_with_tools` 构造的模型，使用未知窗口模型和大消息 | 后续预算切片已在最终消息与工具 schema 预检处拒绝未知/超窗，spy `.invoke` 0 次；原严格 xfail 已转为通过。仅证明该调用边界，不冒称所有 Agent/Experts 路径和真实 provider token 口径均验收。 |
| Ask 双轮职业事实 | 合成两页证据 + 第二轮结构化回答；沿用 authoritative session_id | 后续启动本地 Flask + loopback Dev Principal，真实 `/api/agent/chat` 两轮通过 6 项事实/来源和两条建议形状合同；未人工审阅建议内容。默认不加 `--live` 不联网。 |

原始联合回归（专项 + 图谱相关，预算修复前）：**54 passed，1 skipped，2 xfailed**。最新专项+模型窗口+预算回归为 **34 passed，1 xfailed**，剩余 xfail 是 DOCX 嵌图文字；图谱真实 Neo4j 负测仍需 opt-in。这些数字不代表真实职业建议准确率。

## 脱敏 fixture 与评分规则

新增 `backend/tests/fixtures/platform_acceptance_golden.json` 只含合成教育、项目与城市：第一页 Python/SQL 和无评测指标的 RAG 实验；第二页本科；目标城市成都；未给薪资。参考 URL 使用保留域 `example.invalid`，仅测试来源引用一致性，不是有效招聘来源或实时推荐。

Ask 评分覆盖：末页学历证据、项目未评测的限制、未知薪资不编造、第二轮沿用城市、推荐引用不超出提供来源、两条非空建议的输出形状。输出 `human_review_required=true`，即使结构化契约全部通过也**不接受为职业建议质量合格**。自由文本 `advice` 仍要人工盲评事实性、可执行性、校园招聘现实性和措辞。

本次 2026-10-09 实际请求链路：在本地启动 `mkg` Flask（`CPA_DEV_AUTH=1`），通过 `X-Dev-Student-Id` 注入仅 loopback 测试 Principal；第一轮创建服务器会话，第二轮携带服务器返回的 `session_id`。两轮均返回权威会话，第二轮结构化合同全部为 true；探针只打印两条回复 SHA-256 与布尔指标，不记录材料/回复正文。这里的“第 1/2 页”只是**合成文字在聊天消息中声明的页码**，不是上传文件、真实简历 OCR 或页证据读取；`example.invalid` 不是有效岗位来源。公共 HTTP egress 不暴露内部 `usage_ledger`，故这次 Ask 成功不能用来证明全链 token 账本。

这次双轮测试把合成材料放在第一轮正文；**不是**附件上传→解析→Ask 的完整金路径，也不是页面成果物交接验收。后续须将相同事实映射到受控附件与成果物引用，再测跨页面连续性。

## 可重复执行

从仓库根目录，使用 `mkg`：

```powershell
$env:PYTHONUTF8='1'
& 'D:/Miniconda/miniconda3/envs/mkg/python.exe' -B -m pytest backend/tests/test_platform_acceptance_baseline.py -q -rx

# 默认不联网，返回 explicit_live_opt_in_required。
& 'D:/Miniconda/miniconda3/envs/mkg/python.exe' -B backend/tests/fixtures/platform_acceptance_probe.py ask

# 服务启动且测试身份已认证后；只从指定环境变量取 token，不读取仓库密钥。
& 'D:/Miniconda/miniconda3/envs/mkg/python.exe' -B backend/tests/fixtures/platform_acceptance_probe.py ask --live --base-url http://127.0.0.1:5001 --token-env CPA_ACCEPTANCE_TOKEN

# 或仅在 CPA_DEV_AUTH=1 且 loopback 的本地调试服务，使用专用合成测试身份；不要用于非本机服务。
$env:CPA_ACCEPTANCE_DEV_STUDENT_ID='acceptance_synthetic_20261009'
& 'D:/Miniconda/miniconda3/envs/mkg/python.exe' -B backend/tests/fixtures/platform_acceptance_probe.py ask --live --base-url http://127.0.0.1:5001

# 外部人工逐字真值 / 待验收 OCR；输出严格及 NFC 去空白两种 CER，不打印内容。
& 'D:/Miniconda/miniconda3/envs/mkg/python.exe' -B backend/tests/fixtures/platform_acceptance_probe.py ocr <human-reference.txt> <ocr-hypothesis.txt>

# 推荐按页人工转录：两个目录各放同名 page_001.txt、page_002.txt ...；缺页/空真值直接拒绝。
& 'D:/Miniconda/miniconda3/envs/mkg/python.exe' -B backend/tests/fixtures/platform_acceptance_probe.py ocr-pages <human-reference-dir> <ocr-hypothesis-dir>

# 从权威运行 trace 导出的调用账本；当前未自动生成，不要用合成账本冒充真实预算证据。
& 'D:/Miniconda/miniconda3/envs/mkg/python.exe' -B backend/tests/fixtures/platform_acceptance_probe.py budget <turn-usage-ledger.json>
```

脚本不打印简历、原始回答或 token。Ask 只记录回答哈希、受限布尔评分和错误类型；若 HTTP 不可达、未授权、会话不连续，输出 `not_accepted`，而不是填 0 分或使用 mock 代替。运行会生成合成测试会话，应由测试账号按现有会话管理流程清理。

逐页 OCR 真值应从扫描图像由人工独立转录，并保留页边界、标注者与复核记录；不能拿另一版简历、pypdf 空文本、旧 MinerU 输出或重复 OCR 的相似度充当真值。`ocr-pages` 只实现已用 Levenshtein CER 的逐页聚合，严格版保留空格，归一版为 NFC 去空白；按真值字符数加权，而不是平均每页 CER。本机扫描简历两页的原生文本仍为 `0/0`；旧持久化块仍为 `40/0`（声称总块数 68），因此旧块产物不可用于逐页真值对照。当前**没有人工逐字真值**，真实简历 CER 仍未验收。

## 账本要求与未闭合项

`expected_call_ids` 必须来自可信运行 trace，不由待评分模型自行声明；包含 router/lead、所有专家与工具后续模型调用、失败/重试/failover 尝试。每次记录 stage、provider、model、provider 原始 prompt/completion/total tokens、最终消息 preflight、finish_reason；回合另给 token cap。仅检查末次 `LLMClient.last_usage` 无法证明累计预算。

后续已接同步 LLMClient/LangGraph 的 request-local usage 账本，但检查器尚未与**完整**运行时事件自动对接，也没有证明 fork 后台、直连 SDK 与失败请求的调用清单完整；它仍是全链验收门槛，不能把局部预算修复称为完成。多模态实际计费、供应商 tokenizer 差异、stream usage 和失败重试成本还需真实 trace。工具 schema 已纳入 LangGraph 预检，但未证明其估算与每个 provider 的 tokenizer 一致。

人工验收后续顺序：

1. 固定至少几类脱敏真实样本和逐页人工真值，按日期/学历/职责字段分别算召回、CER；图像文字与复杂 DrawingML 独立列覆盖。
2. 记录完整 Agent/Experts 调用账本；调用前拒绝边界的替身负测现已通过，下一步测真实 provider 超窗、重试、fork 与直连 SDK。
3. 启动受认证的完整 Ask API 跑双轮合成问题，再增加真实附件、提供可信 JD、矛盾材料与跨页面 artifact refs；固定 provider/model/prompt 版本与实际 usage。
4. 职业建议人工盲评与链接时效单独验收，不将“返回严格 JSON / 使用最后一页 / OCR 一致”当作产品体验完成。
