# Ask 证据范围：章节计数直答与未通过验收的双审实验

2026-10-10。章节计数窄路径已通过下述实测；通用复杂分析仍未通过事实性验收。

## 当前最窄修复：同一次 selector 决定语义类型

复用既有 `_select_plan` JSON 输出，增加 `answer_kind=section_count|analysis`，没有增加分类调用。仅问单一章节记录数，以及这个计数本身能/不能证明什么时为 section_count；要求项目质量、能力优势、贡献或岗位建议的实质分析必须 analysis。未知/缺失字段、超过 selector 2000 字输入范围均降为 analysis。不按中文关键词或正则分类。

服务端只接受目录 `section_details` 已登记章节的标题；要求单一 source、同版本、完整 coverage、整数 total、returned==total、无截断/续页/不可用且无本轮附件。满足后不调用回答模型，直接用权威版本/标题/条数输出，并明确只证明章节记录数，不证明原 PDF 抽取完整、能力贡献或岗位匹配；公开 `grounding.status=source_count`。partial、多源、无标题或不确定分类均保持原路径，不能据此假称全部回答已 grounded。

真实 HTTP 当前报告同题：200，**3.281 秒，1 次 qwen-plus 调用，631 tokens**，finish_reason=stop，chapter:experience 6/6，tools=[]，path=ask_section_count。回答：

> 当前版本（task_b13910b97b4c）的「项目与实践经历」章节共有 **6 条记录**。这个数字只说明该章节在当前版本中存有多少条记录，且本轮已读完这个章节。它不证明原始 PDF 已被完整、准确地抽取，也不证明其他章节完整；更不能单凭条数证明你的能力、个人贡献、项目质量或岗位匹配程度。评价这些方面需要逐项核对相应经历和成果证据。无需重复上传当前已能读取的资料。

对比下文原路径 19.015 秒/3718 tokens，未加审核即可避免该计数问题的过度推断。测试 session_38fd748855e1 DELETE 200。43 条相关专项回归通过。

负面对照问题：“这份报告的项目与实践有几条？请逐项分析我的项目质量、个人优势，并建议适合的岗位。”真实 HTTP 200、33.468 秒、632+3934=4566 tokens、2 次调用，path=ask_llm、grounding=not_checked；**没有误走纯计数支路**。该模型正文仍出现“闭环思维扎实”“远超单纯调用API”等无证据能力推断，并将 6 条章节记录中的 4 个项目当作“项目与实践共4项”；这只是分类边界通过，不是复杂分析事实性通过。测试 session_b7edc90359f3 DELETE 200。另旧报告复测仍为 409 RESOURCE_VERSION_CONFLICT，无模型调用，session_2c02a6b4dec6 DELETE 200。

## 复用与范围

沿用 `quality_gate.py` 入口、现有 LLMClient strict JSON、上下文预检、全链 token ledger 和 30000 默认 turn cap；参考已检视的 OpenCode `388406238b` session/prompt.ts 结构化输出失败边界及 Codex `a06545b` MCP schema 边界。没有新增 Agent 框架，也没有中文关键词事实性分类器。

仅覆盖 Ask 无附件、EvidencePack.items 非空的答复出口；附件、Experts、Agent 未接该实验审核。默认关闭 `CPA_ASK_GROUNDING_REVIEW_ENABLED`，公开 `grounding.status=not_checked`，不额外调用模型。设为 true 后最多两次、每次 1800 output tokens：审核原稿，不支持时给出修订，再独立核验修订；失败/预算不足/截断均阻断原稿并返回 partial。`model_checked` 仅代表模型审核，不是事实验证。审核开启不是推荐默认配置。

原始模型分析不再通过 thinking_steps 绕过检查；替换为简短服务端执行摘要。证据页完整只指选中章节，不代表原 PDF 完整。

## 真实 HTTP 反例与成本

POST `http://127.0.0.1:5001/api/agent/chat`，开发 Principal `golden_ae0b35b3e11a`，body：

```json
{"message":"这份报告的「项目与实践」章节共有几条？这个数字能证明什么、不能证明什么？","interaction_mode":"ask","content_parts":[{"type":"resource_ref","uri":"career://me/resume-reports/resume_8e63591a219c742972c7126a3cfc4263"}]}
```

真实来源 `chapter:experience`、版本 `task_3d3b73045f24`，coverage 6/6，tools=[]。初稿的分析错误声称“这6条已覆盖全部原始内容……没有截断或缺失”，以及把条数延伸为工程能力/组织经历意义。两次语义审核仍未接受修订，最终 `blocked/unsupported_after_revision`、outcome partial。最终展示文本：

> 已读取当前引用的资料，但本轮回答未通过证据范围校验，因此没有展示可能过度推断的结论。资料读取范围不等于原文件完整性，也不能单凭条目数量证明能力或实际贡献。你无需重复上传同一资料；可以重试，或将问题缩小到具体记录。

HTTP 200，62.485 秒；5 次真实 qwen-plus 调用总 tokens 551、3078、3323、2869、2572，合计 12393；新增审核两次共 5441 tokens。该延迟与最终有用性不能通过验收，因此默认禁用，不能将阻断当作高质量回答。实验 session_b2e8ee980938 已经 DELETE 清理。

## 版本冲突

旧报告 `resume_782c4ce3533875c4e1b4b3941eecd058` 实测因 student_001 当前确认版本已是 `task_b13910b97b4c` 而拒绝；确认时间 2026-10-10T21:33:15，变更来源未查明。拒绝本身正确，之前异常泄漏成 HTTP 500。现 Ask API 将 ResumeReportStale 映射为 409 `RESOURCE_VERSION_CONFLICT`，next_action=refresh_resource_reference；不会自动以新报告替换旧引用。未改真实用户画像/确认数据。

## 后续门槛

最终加载默认禁用代码后再次真实 HTTP：旧报告 409（0.031 秒）；新有效报告 `resume_1ac0d7ae7997bf6f42831bd38694503d` 返回 200（19.015 秒），version `task_b13910b97b4c`，chapter:experience 6/6、tools=[]、grounding=not_checked/checks0。仅两次 qwen-plus 调用 548+3170=3718 tokens，无审核额外调用。模型仍将数量解释为“你有意识地梳理并呈现了多元实践，覆盖技术开发、系统设计、团队协作与组织经验”，因此**默认回答质量仍未通过**。执行摘要已不再包含原始模型推理。测试会话 `session_fda05f3c587d` DELETE 200；31 条相关回归通过。

纯计数/范围问题现已按顶部小节实现。复杂能力分析仍需来源级声明、评价集及成本验收；单个正确的计数答复不代表通用 groundedness 已解决。
