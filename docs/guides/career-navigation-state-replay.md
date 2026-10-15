# 职途导航：已确认简历与画像任务的重放边界

## 为什么需要此接口

`GET /api/profile/student/candidate/<task_id>` 原先只接受 `needs_input` / `partial` L0，用户确认后任务转成 `completed`，同一深链刷新便得到 409；页面无法恢复已确认资料与后续 L2 状态。这是前端会话状态与权威业务状态断链，不是 PDF 正文少一页。

## 权威读取契约

- 服务端 Principal 决定 `student_id`。跨用户 task ID 一律 404，不读取其他用户简历。
- `needs_input` / `partial` 仍返回未确认候选。`completed` 仅当 L0 结果、当前 `student_profile._resume_confirmation.task_id` 和已确认候选回读一致时返回；被后续简历确认替代的旧 L0 返回 409。
- 已确认响应保留 `candidate_profile`、`source_evidence`，并附 `confirmed: true`、`profile_ready: true`。`portrait_task` 来自同一 owner、且 `result_json.source_confirmation_task_id` 等于该 L0 task ID 的最新 L2 持久任务；未生成则为 `null`。
- `portrait_task.status=partial` 时只展示已有的维度候选，缺失维度与总分继续为 `null`，`portrait_id` 不得伪造。旧 L2 中的 `next_action=confirm_semantic_facts` 只是缺口代码；当前没有语义事实确认写入 API，接口显式返回 `action_available=false`、`resume_state=not_available`，页面不得把它渲染为已可执行的恢复按钮。
- `completed` L2 还需通过任务引用解析器回读画像业务记录并校验 ID、内容与 Principal，才可作为已完成成果返回。进度事件仍由 `/api/pipeline/events/<task_id>` 从 MySQL 重放，不从前端计时器估算。
- `POST /api/profile/student/pipeline` 对已持久化的 `partial` 返回 HTTP 200、`success=true` 且 `data.status=partial`；这只表示请求成功，不表示画像/匹配/报告已完成。真正的鉴权、参数和执行错误继续使用 4xx/5xx。页面必须按业务 `status` 分支，不能以 HTTP 200 自动上屏总分。
- L0 缺页时，`POST /api/profile/student/confirm` 仍需 `acknowledge_partial=true` 的明确用户操作；确认仅覆盖已提取内容。L2 即使四维计算器暂时给出完整分数，也将 `source_pages` 写入缺证字段、综合分置 `null`、状态持久化为 `partial`，不写入 `student_portrait_4d`。同源深链回放保留 `next_action=reupload_resume`、`resume_state=new_upload_required`；简历范围诊断报告独立保留缺页限制。
- D3 缺口不再一律给出不可执行的死路：学生可通过 `POST /api/profile/student/self-report` 提交四项 `强/中/弱` 判断与具体例子，服务端只接受当前 Principal 的 L0 确认任务及乐观并发修订号。自述独立保存在 `student_profile._user_self_report`，不得与 L0 `candidate_profile` 比对时混成简历事实；新 L0 确认会整体替换旧自述。L2 在无原文证据的 D3 项使用它时写入 `provenance.kind=user_self_report`，并记录来源修订号；深链、Agent 资源和正式画像读取均拒绝旧修订的 L2 结果。自述不消除 `source_pages`、`school_tier` 或 `major_category` 缺口。
- 已确认简历没有可核对技能时，L2 不再以异常中断；D2 保留项目等已知事实，但维度分与综合分均为 `null`，`missing_fields` 明示 `skills`。任务持久化为 `partial` 且不写入完整画像表；同源深链可继续查看候选与简历范围报告。恢复路径是补充**原简历**后重新上传和确认，不把职业偏好或职业素养自述当作技能证据或自动计分。
- 技术技能可另作**非评分的方向探索自述**：`POST /api/profile/student/skill-exploration` 按当前 Principal、已确认 L0 task ID 和乐观修订号保存独立 `_skill_exploration`，仅接受最多 12 项单行名称；`POST /api/profile/student/skill-exploration/roles` 不接受客户端临时技能列表，只读取该 owner 的已保存快照调用既有方向推断。响应标 `source_kind=user_self_report`、`scoring_eligible=false`、`exploration_only=true`。同源候选可回放，但 L2、个人图谱和简历诊断均只读简历或各自授权的 D3 资料；更新技术技能自述不使旧画像失效，新 L0 确认清除旧自述。

## 开源参考及适配边界

在修改状态读取前比对了本地 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/session/message-v2.ts`（区分 pending/running/completed/error 的工具状态）及 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 `codex-rs/core/src/thread_manager.rs`（从持久来源恢复而非 UI 内存）。二者均未被复制或引入运行依赖；本项目复用已有 `pipeline_task`、`student_profile`、`require_confirmed_profile`、`resolve_pipeline_task_ref` 和 MySQL 事件日志，薄适配到职业规划业务对象。上游许可证及其余模块见 `../architecture/agent-upstream-adaptation.md`。

本轮缺页门禁复核同一 OpenCode/Codex 快照的状态与恢复边界，但它们均没有“已确认简历缺页能否发布画像”的业务语义；没有复制通用状态机，而是复用本项目 `acknowledge_partial`、`pipeline_task` 的 `partial` 状态和同源回读，在 L2 发布前保持缺页未完成。适配范围仅为简历来源完整性，不改变 Agent 通用数据流。

## 验证

`backend/tests/test_resume_golden_path.py` 覆盖已确认深链重放、同源 partial L2 的已知维度、缺失总分、缺页确认不得发布完整画像、当前简历替代旧候选、跨 Principal 拒绝。真实 MySQL 的一条已确认三页简历回读：L0 `completed`，关联 L2 `partial`，有 6 个缺证语义字段且总分为 `null`；这是领域评分的证据不足，不是页面缺失。个人资料、原文与联系方式不记录在本指南。

宿主机 Flask 重启后，经 `http://127.0.0.1:5174` 开发代理回读同一 L0：HTTP 200、已确认、页 `[1,2,3]`、同源 L2 `partial`、D2/D4 有候选值而综合分仍为 `null`。直接请求后端时，其他 Principal 得到 404、无 Principal 得到 401。后端完整回归为 `618 passed`；前端 `npm run type-check` 与 `npm run build-only` 通过。浏览器控制连接不可用，本次不能把 API/构建结果冒充窄屏视觉截图验收。

### 2026-10-14 缺页与解析 UI 追加核验

- 先在真实 `5174` 页面用临时学生账号粘贴一份虚构文本简历：L0 四个服务端检查点从 0% 到 100%、候选核对、用户确认、L2 `partial`（2/4 维有候选、综合分 `null`）、简历范围诊断报告 `completed` 均实际出现。原页面仍在真实解析中展示演示分数/岗位/评语的空骨架，候选项目与竞赛也被摊成内部字段串；修改后同一深链的 DOM 回读为四条真实检查点摘要、无演示空骨架。临时账号、任务、报告和上传文件已清除；这次后台标签页的 GSAP 入场动画暂停导致截图透明，**不能**算桌面/窄屏视觉验收。
- 回归：`python -B -m pytest backend/tests/test_resume_golden_path.py backend/tests/test_resume_diagnostic_report.py backend/tests/test_profile_pipeline_input_contract.py backend/tests/test_match_report_source_gate.py -q` 为 **39 passed**；启用 `CPA_TEST_PIPELINE_MYSQL=1` 的 `test_resume_partial_pages_http_mysql.py` 为 **1 passed**。`cpa-xingtu` 的 `npm run type-check`、`npm run build` 和 `npm run test:resume-trace` 均通过（构建仍有既存的大包/lottie eval 警告）。
- 另起加载本轮代码的隔离 Flask `127.0.0.1:5002`，用正式 Cookie 账号及真实 MySQL 对二页、仅一页可读的受控确认夹具调用 `POST /api/profile/student/pipeline`：HTTP 200、业务 `partial`、`missing_fields` 含 `source_pages`、总分 `null`、`student_portrait_4d` 为 0 行；同源 `GET /api/profile/student/candidate/<task_id>` 回放 `new_upload_required`。临时账号、任务与文件均已核对清除；5002 已停止。原有 5001 进程未重启，不能把它误当本轮后端代码验收。当前实库仍缺 `school_tier_review` 与 `alembic_version` 表，L2 依赖兼容分支继续运行；正式迁移仍需维护窗口。

### 2026-10-14 用户自述补充链路

- `test_student_self_report_http_mysql.py` 用真实 MySQL 和 Flask HTTP 路由，以临时 owner 确认 L0，先得到 D3 缺证的 L2 `partial`；提交四项自述后检查数据库 `student_profile._user_self_report` 的来源、修订号和内容，旧 L2 深链不再返回画像；重新跑 L2 后 D3 的四项均带 `provenance.kind=user_self_report`，维度候选 48 分，但学校/专业缺口仍使总分为 `null`、画像表 0 行。重复旧修订 409，撤回后再跑 L2 的 D3 恢复 `null`。再用受控完成画像写入业务表并更新自述，验证旧 `/4d` 成果返回 409、候选深链隐藏旧画像、Agent 任务引用拒绝旧版本；跨 owner 不得修改。测试结束清理任务与资料。该测试使用受控 L0 候选，**没有替代**真实 OCR/模型解析验收。
- `test_student_self_report_contract.py` 核对跨简历来源的自述不能计分，客户端提交的 `score` 不能绕过字段契约。后端全套 `python -B -m pytest backend/tests -q` 为 **1127 passed、104 skipped**；新增两条纯契约用例及实库自述/缺页合计 **4 passed**。前端 `npm run type-check`、`npm run build`、`npm run test:resume-trace` 通过。

### 2026-10-14 浏览器布局与操作复验

- `backend/tests/manual/career_navigation_browser_probe.py` 另起当前代码的 Flask 5002、Vite 5175 与隔离 Headless Chrome，在真实登录会话、真实 MySQL、受控已确认 L0 候选下打开 `cpa-xingtu` 深链。原来的两个 `watch` 在任何后端画像出现时自动进入沉浸布局，CSS 同时把左栏设为 `opacity:0`、`pointer-events:none`，导致补充自述表单存在却不可见。现改为右侧结果就位而左侧核对与补充操作保留，只有用户点击全屏按钮才隐藏左栏。
- 浏览器实际选“有具体经历”、填写沟通实例、点击“保存自述并重新评估”后，数据库自述修订号为 1，当前 L2 回放的 D3 项来源为 `user_self_report`。桌面 1440px 测得左栏宽 480px、表单宽 397px、无自动沉浸；移动 390px 测得表单宽 338px，左栏全文高度 1730px、外层工作区可滚动，不再困在约 250px 的内部小滚动区。原移动头部逐字竖排问题也在本轮修正。截图保存在 `output/career-nav-browser-check/`；临时账号、任务、画像与报告已清理。这个浏览器探针仍使用受控 L0 候选，**不证明 OCR/模型端到端解析或所有 mock 业务页面已达标**。
- 同一浏览器探针还复现了旧任务无 Agent 轨迹时，右侧原先错误显示“等待接收 / 0% / 四位 Agent 全等待”，与“简历已确认”矛盾；现改为“历史轨迹未留存 / 无可回放检查点”，不补画不存在的过程。浏览器 DOM 回读通过，当前任务的资料状态仍由 `candidate` 与 L2 业务记录决定。
- 浏览器截图还发现：受控简历没有竞赛、科研、成绩或带队材料时，D4“成长潜力”被服务端算成 `0`，页面同时声称“未知项不会当零分”，形成可见的自相矛盾。L2 计算器现将这种无证据情况表示为 `dimension_score=null`、`missing_fields=growth_evidence`，前端显示“待核对 / —”及补传原简历的说明；已有竞赛等可引用依据时仍计算候选分。`backend/tests/pipeline/test_portrait_generator.py` 为 **33 passed**；同一真实 Flask HTTP + MySQL + 浏览器探针再次通过，核对 L2 返回 `null`、页面为“—”，并完成一次自述提交和 D3 来源回读。缺失材料不能由自述自动补齐，也不会发布完整总分。
- 对旧持久 L2 的 `D4=0` 且依据列表为空的组合增加读取门禁：当前简历仍可回放，但旧画像/Agent 引用不再当成现行评分成果，页面提示“重新生成”。浏览器探针在受控 owner 的真实 MySQL 行上临时构造该旧结果，确认 `candidate` 返回 `portrait_needs_refresh=true`、`portrait_task=null`；点击页面重算后回读新 L2，D4 为 `null`、提醒消失，夹具最后清理。纯契约与路由测试见 `test_student_self_report_contract.py`、`test_resume_golden_path.py`。此前跑完的后端全套为 **1130 passed、104 skipped**；随后增加的旧画像门禁另有聚焦 **61 passed**，未把两次结果混称为一次全套。
- 最终同一浏览器探针还等待了真实报告生成并回读 `status=completed`；提交自述后再次读取仍是同一份简历报告，证实自述不会篡改简历范围成果。职业偏好从深链恢复、在页面改选后触发第二次 `infer-role`，但 L2 task ID 与 `student_profile` 中的事实均未因偏好而变化。最后一次全套后端回归为 **1131 passed、104 skipped**，前端 `type-check`、`build-only`、`test:resume-trace` 均通过；报告链路用真实模型，L0 仍是受控候选，不能把它当作真实 OCR/模型上传验收。

### 2026-10-14 真实文件到页面的端到端核验

- `backend/tests/manual/career_navigation_l0_probe.py --pdf --browser` 使用**虚构人物**生成单页可提取文字 PDF，经真实登录 Cookie、Flask HTTP、Redis 派发、独立 `cpa.pipeline` Celery worker、真实模型、Neo4j 与 MySQL 完成 L0。回放 17 条阶段轨迹，任务从 `pending` 到 `needs_input`；`candidate` 有 6 段可核对原文，而确认前 `student_profile` 为 0 行。浏览器深链实际展示学校、项目、技能与逐页证据，用户点击确认后数据库档案绑定同一 L0 任务；自动 L2 为 `partial` 而非虚构总分，简历范围诊断报告为 `completed`，方向建议含独立的前端偏好。此 PDF 是文字型文件，不代表扫描件 OCR；全部验收夹具账号、任务、上传文件与报告已清理。
- 另将隔离队列的 worker 暂停，用同一 PDF 上传，真实 HTTP 任务 22 秒后仍为 `pending`、`current_attempt=0`、轨迹只有 `file_received`。此前页面会无限显示普通“处理中”；现在连续观察到尚未接单 20 秒后，页面明确提示任务已保存、可刷新继续、无需重复上传以及需检查解析服务。截图 `output/career-nav-browser-check/l0-worker-waiting-notice.png`。启动匹配队列的 worker 后，**同一任务编号**继续到 `needs_input`，确认和后续页面链路仍完成；没有丢弃上传或伪造成功。该提示是基于可读轨迹的前端等待观察，不声称诊断出 worker 的具体故障原因。
- 本轮前端 `npm run type-check`、`npm run build-only`、`npm run test:resume-trace` 通过（保留既有 lottie eval / 大包警告）。实际当前宿主的原 5001 后端仍未配套运行 `cpa.pipeline` worker；本次端到端核验使用独立 5002/5175 与隔离队列，不能把它误写为原 5001 已自动具备后台处理能力。生产/本机长期运行仍须按 README 分别管理 Flask、worker 和恢复扫描 Beat。

### 2026-10-14 扫描件 OCR 到浏览器的补验

- 复用上述虚构简历，先渲染成图片再嵌入 PDF，探针在上传前断言该 PDF **没有可提取文字层**。另起当前代码的隔离 Flask 5002、`cpa.goal.ocr` worker 与真实 MinerU Sidecar（`/v1/health` 返回 `ocr_pdf=true`；后端/worker `CPA_L0_PDF_ENGINE=auto`、`CPA_L0_PARSE_SIDECAR_URL=http://127.0.0.1:9380`），使用 `backend/tests/manual/career_navigation_l0_probe.py --scanned-pdf --browser` 完成真实 HTTP→OCR→LLM→MySQL→浏览器核对→点击确认→L2/简历报告。权威候选明确 `source_evidence.engine=sidecar`、页码 `[1]`、10 段 OCR 证据、结构校验 `ok=true`，学校与专业和虚构原文一致；未确认前档案为 0 行。截图为 `output/career-nav-browser-check/l0-scanned-pdf-confirmation.png` 与 `l0-scanned-pdf-confirmed.png`。这证明**该单页虚构扫描件**完整可用，不证明所有版式/语言的 OCR 字错率；画像仍因其他证据不足保持 `partial`。
- 当前机器 C 盘空间为 0 时，一次重复上传在 multipart 阶段收到 HTTP 500、服务端记录 `OSError [Errno 28] No space left on device`。这是运行环境容量问题，不是 OCR 解析结果；未创建任务，也未把它算作解析失败。将本轮隔离 Flask、worker、Sidecar 和探针的 `TEMP/TMP/TMPDIR` 指向有空间的仓库 `output/` 后，同一类型扫描件再次完整通过。**正式部署仍须为上传临时目录预留空间**；不能依赖前端重试解决磁盘耗尽。

### 2026-10-14 上传临时空间修正

- 上述真实 500 的最早失败点是 `request.files` 触发 Werkzeug multipart 临时溢写，而非后续 `file.save`；路由连任务编号都未产生。复用 Flask 3.1.3 / Werkzeug 3.1.8 的 `Request._get_file_stream` 扩展点，仅将 `/api/profile/student/upload` 的溢写目录改到 `Config.UPLOAD_FOLDER` 所在卷，保持原 500 KiB 内存阈值、50 MB 请求上限和 Werkzeug 原解析器，**没有另造 multipart 解析器**。其他上传入口仍使用各自原有路径。`request.files` 或持久文件写入遇到 `ENOSPC` 现在返回 HTTP 507、`upload_storage_full`、明确“尚未创建解析任务”；持久写入留下的半截文件只清理本次随机路径，不删除已提交任务文件。
- `backend/tests/test_resume_upload_failure_cleanup.py` 的真实 HTTP 路由测试用受控 `OSError(ENOSPC)` 分别覆盖 multipart 溢写、持久文件半写入和指定溢写目录，`6 passed, 1 skipped`；未运行真实 MySQL 可选测试。加载新代码的隔离 Flask 5002 在**未重定向系统 `TEMP`**的条件下再走一次虚构扫描 PDF→Sidecar OCR→LLM→真实 MySQL→确认，仍得 `needs_input`、`engine=sidecar`、正确学校/专业和 10 段证据；这证明新临时流未破坏该真实路由，不等于所有上传入口或磁盘容量场景均已验收。一次旧进程 C 盘为 0 的失败与新进程的成功之间，C 盘容量曾恢复约 23 MB，不能把后一轮成功单独当成“零剩余空间仍可运行”的证据；隔离路由的目录选择由 HTTP 测试明确核验。
- 开源适配记录：先检查项目既有 `profile.py`/`admin.py` 上传行为，核对本地 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/tool/truncate.ts` 与 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的文件临时路径相关模块；二者不承担本服务的 Flask multipart 解析。直接使用已安装 Werkzeug 3.1.8 `werkzeug.wrappers.Request._get_file_stream` 与 Flask 3.1.3 `Flask.request_class` 扩展接口，仅做路径适配和业务错误映射。上游官方文档：[Werkzeug Request](https://werkzeug.palletsprojects.com/en/stable/wrappers/)、[Flask 子类化](https://flask.palletsprojects.com/en/stable/patterns/subclassing/)；适配边界是职业导航 L0 上传，不改 Agent 通用媒体/任务状态机。
- 该路由改动后，以 `mkg` 环境从仓库根目录运行全套 `python -B -m pytest backend/tests -q`：**1134 passed、104 skipped、7 warnings**（4 分 8 秒）。扫描件浏览器探针和这套 pytest 是不同验收：前者走真实 OCR/模型/DB/页面，后者守住错误映射和其余后端回归，不能互相替代。

### 2026-10-14 无技能条目的部分画像修正

- 先用 `test_student_no_skills_http_mysql.py` 的受控、已确认 L0 候选复现：单页资料包含学校、专业、学位和项目，但 `skills=[]`，实际 HTTP L2 返回 **409**（`已确认简历没有技能，请补充后重新确认`），用户不能从该响应获得可回看的部分画像。这不是解析服务故障，而是画像证据缺口。
- 修正后，同一真实 Flask HTTP 路由与 MySQL 请求返回 HTTP 200、业务 `partial`；`D2_skill.dimension_score=null`、综合分 `null`，项目数量和项目证据仍在候选中。权威 `pipeline_task` 行为 `partial`、`student_portrait_4d` 无该 owner 行，同源 `candidate/<task_id>` 回放同一任务并给出 `reupload_resume`。前端明确写出“技能待核对”，仍允许查看已知内容、补充独立标识的职业素养自述或重新上传原简历。测试夹具的 L0 候选受控且 Principal 由测试注入；这条用例**不替代**真实登录、OCR 或模型抽取验收。
- `mkg` 环境的聚焦 MySQL HTTP + 画像计算测试 **35 passed**；后端全套 `backend/tests` **1135 passed、105 skipped、7 warnings**。`cpa-xingtu` 的 `npm run type-check`、`npm run build-only`、`npm run test:resume-trace` 全部通过（构建仍有现存 lottie `eval` 和大包警告）。
- 另在当前 Flask 5002、Vite 5175 与隔离 Chrome 的真实登录会话中运行 `backend/tests/manual/career_navigation_browser_probe.py --no-skills`：页面回放同一受控 L0/L2，标题为“画像候选已生成”，右侧职业技能显示 `—`，左侧列出“简历技能的可核对依据”及重新上传说明，左栏仍可见；自动触发的真实模型简历诊断报告最终为 `completed`。截图 `output/career-nav-browser-check/desktop-no-skills-partial.png`。这是**受控已确认候选**的浏览器/报告核验，不是本轮重新跑 OCR；夹具账号、任务、报告均由探针清除，隔离进程已停止。

### 2026-10-14 技术技能自述仅用于方向探索

- `test_student_skill_exploration_http_mysql.py` 先以受控已确认 L0 的无技能简历复现新接口不存在的 HTTP 404，再添加独立、按 owner 和确认任务绑定的 `_skill_exploration`。真实 Flask HTTP + MySQL 验证：保存 `Vue`、`TypeScript` 后，`student_profile.skills` 仍为空；同源 L2 深链继续回放原任务，重新运行 L2 的 D2 与综合分仍为 `null`，个人图谱快照 ID 不变，简历诊断草稿不含这两项自述。方向推断接口拒绝临时客户端技能列表，只从持久快照读取；修订号冲突、跨 owner、撤回和新简历确认清除旧自述均得到相应结果。该测试的方向推断函数受控替身只证明数据边界，不证明图谱目录的候选质量。
- `backend/tests/manual/career_navigation_browser_probe.py --skill-exploration` 另用正式登录 Cookie、真实 MySQL、Neo4j 与 `infer-role` 在页面上输入、保存并回放同一两项技术技能。真实目录给出“前端开发/全栈开发”的**自述探索**方向，匹配的目录技能名称与原始自述分开标记；刷新仍显示已保存的自述，原 L2 task ID 不变、技能维度 `—`，真实模型简历诊断报告 `completed`。1440px 桌面与 390px 移动页面均实际渲染且无横向溢出；截图 `output/career-nav-browser-check/desktop-skill-exploration.png`、`mobile-skill-exploration.png`。浏览器 L0 是受控已确认候选，**不是这轮重新上传扫描件**；夹具账号/任务/报告已清理。
- 回归：启用 `CPA_TEST_PIPELINE_MYSQL=1` 的聚焦测试 **16 passed**；随后 `mkg` 环境后端全套 `backend/tests` **1142 passed、106 skipped、7 warnings**。前端 `npm run type-check`、`npm run build-only`、`npm run test:resume-trace` 均通过，仍有既存 lottie `eval` / 大包警告。保存技术技能自述不触发 L2 重算，与 D3 职业素养自述的计分契约明确分离。

### 2026-10-14 当前工作台状态与部分画像可读性首切片

- **旧行为复现：**默认 5001 Flask、5174 Vite、已运行的 `cpa.pipeline` worker/Beat 对虚构文本 PDF 完成异步 L0（17 个阶段、6 段页证据），浏览器确认后 L2 为 `partial`、简历诊断为 `completed`。但首页仍写“你的能力轮廓，正在形成”“候选画像 · 未发布”，用 1/4 评分维度、综合分“待核对”三张 KPI 卡占据首屏；这把可读的当前结果表现成未完成。后端先前也没有同时回读三种状态的工作台端点。
- **新读面：**`GET /api/profile/student/workbench` 按当前登录 Principal 读取 L0、当前确认简历、同源 L2 与简历报告。普通未知维度的 L2 `partial` 映射为可读的 `ready_with_unknowns`，四维缺证为 `null`、`scoring_eligible=false`、不生成通用综合分；报告独立显示 `ready/partial/not_started/stale`。较新导入尚未确认时 `based_on_previous_upload=true`，不会把旧画像标成新版本结果。原任务和评分门禁不改变。
- **回放：**用 `mkg` Python、真实 MySQL/Redis/worker/Beat、PyMuPDF 和真实模型，先以隔离 Flask 5002/Vite 5175，随后以重新启动的默认 Flask 5001/正式 Vite 5174 各运行 `backend/tests/manual/career_navigation_l0_probe.py --pdf --browser`（环境变量 `CPA_L0_PROBE_BASE`、`CPA_L0_PROBE_FRONTEND_BASE` 分别指向所测端口，Chrome CDP 9223）。两次聚合响应均为 `portrait=ready_with_unknowns`、`resume_diagnostic=ready`；浏览器 1440px/390px 均显示“当前可了解的能力信息”、已知/未知/下一步，未出现“综合竞争力”，无横向溢出。最终默认地址截图 `output/career-nav-browser-check/l0-text-pdf-confirmed.png` 与 `l0-text-pdf-confirmed-mobile.png`；夹具自动清理。另有 `CPA_TEST_PIPELINE_MYSQL=1` 的 `test_student_workbench_http_mysql.py`，通过真实 HTTP+MySQL 检查跨 owner 隔离、持久化 L2 partial 和新导入标记；它的 L0 候选受控，不冒充 OCR 验收。
- **当时未完成、现已推进：**这一节记录 2026-10-14 的 P0 截面，当时还没有 P2 自动编排。2026-10-15 的 P2 首切片见下节；仍缺 P1 可版本化、独立 ID 的部分画像快照与 P3 可展开的真实工具输入输出事件。右侧现有 L0 轨迹仍仅是白名单阶段元数据。详见 `docs/specs/student-workbench-state-contract.md`。
- **回归：**`mkg` 全套 `python -B -m pytest backend/tests -q` 为 **1142 passed、107 skipped、7 warnings**；额外启用 `CPA_TEST_PIPELINE_MYSQL=1` 的新增 HTTP/MySQL 用例 **1 passed**。前端 `npm run type-check`、`npm run build` 与 `npm run test:resume-trace` 均通过（6 项断言）；构建仍有既存 lottie `eval` 与大包提醒。默认 Flask 5001 已重启加载新路由，正式 Vite 5174 保持运行；隔离 Flask/Vite/Chrome 已关闭。

### 2026-10-15 确认后画像和简历诊断的持久后台接续

- **故障复现：**真实 HTTP+MySQL 在确认 L0 后、没有浏览器额外 POST 时，先前工作台的画像与诊断都为 `not_started`；学生离开页面不会自动生成。首次新代码的浏览器回放又发现事件流切换停止了两成果的聚合刷新，导致数据库已有报告、页面却一直显示分析中。把 L0 事件轮询与成果刷新改为独立定时器后重放通过。
- **新路径：**确认事务同时持久化 `L2_generate_portrait` 和 `R1_resume_diagnostic` 两个 owner/确认版本绑定的确定性任务及各自 dispatch；提交后只投递任务 ID。Celery claim/租约和 Beat 默认每 60 秒的恢复扫描（对最近 30 秒刚投递的任务暂不重派）负责未接单任务，旧 worker 的过期 claim 不能写新的画像或报告。自述修订仅新增 L2 任务；自述只用于方向探索或有来源区分的 D3 候选，不自动补入简历技能评分。`workbench.analysis_run` 只汇总后台执行，`run` 仍为 L0；两成果独立读取，报告 partial 不强迫画像等待。
- **迁移与运行：**先按 `docs/runbooks/mysql-migrations.md` 在维护窗口运行 `backend/` 下的 `python -B -m alembic -c alembic.ini upgrade head`，确认 head 为 `0006_student_analysis_dispatch`；然后启动 Flask、`cpa.pipeline` worker 和 Beat。旧 `init_mysql.sql` 冻结，不再追加结构。仅供历史兼容的 `python -B -m app.services.pipeline_portrait_jobs --install`、`python -B -m app.services.resume_diagnostic_jobs --install` 不是新部署的统一迁移方式。缺 worker 时工作台会保留处理中的真实任务 ID，而非假装已完成。
- **重放：**在真实服务全部运行且使用虚构测试账号时，从仓库根目录运行 `D:\Miniconda\miniconda3\envs\mkg\python.exe -B backend/tests/manual/career_navigation_l0_probe.py --pdf`；此模式确认后不打开页面、不发 L2/报告 POST，等待 MySQL 成果及两个 dispatch attempts。加 `--browser` 则由正式 `cpa-xingtu` 页面完成确认并验证桌面/390px 回读。可设置 `CPA_L0_PROBE_BASE`、`CPA_L0_PROBE_FRONTEND_BASE` 指向目标端口；浏览器需隔离 Chrome CDP 9223。实际默认 Flask 5001/Vite 5174 + MySQL/Redis/Neo4j + worker/Beat + 真实模型、PyMuPDF 的两次回放均成功：L0 17 阶段、6 页块证据，画像 `ready_with_unknowns`、报告 `ready`，attempt 各 1；截图 `output/career-nav-browser-check/l0-text-pdf-confirmed.png` 和 `l0-text-pdf-confirmed-mobile.png`。文字 PDF 不证明扫描 OCR；探针按自身 ID 清理账号、文件、报告、任务。
- **回归与范围：**`CPA_TEST_PIPELINE_MYSQL=1` 的 `backend/tests/test_student_workbench_http_mysql.py` 为 **1 passed**，以受控候选验证 owner 绑定、broker 失败后持久意图仍在、恢复扫描重派、重复消费 no-op、成果/旧版本状态；`CPA_TEST_LIVE_MYSQL=1` 的迁移测试为 **4 passed**（空表与旧显式安装表两种路径）。完整后端测试最初因误改冻结基线 SQL 为 **1141 passed、107 skipped、1 failed**；恢复基线并增 `0006` 后完整复跑为 **1142 passed、107 skipped、7 warnings**。前端 type-check、`test:resume-trace`、build 通过。worker 异常终止/租约过期、真实模型预算/截断、扫描 OCR、旧画像历史回看和逐条实际工具事件仍未验收。隔离 Chrome 已关闭，默认 Flask 5001、Vite 5174、worker/Beat 保持运行。

### 2026-10-15 新画像快照与旧版只读

- **起因与修复：**先前 L2 `partial` 可阅读但没有独立成果 ID；新简历确认后旧任务的 `candidate` 路由按当前门禁返回 409，页面无法温和回看历史。现在 L2 在权威终态事务里只追加 `student_portrait_snapshot`；新部分画像同样有稳定 `snapshot_id`、来源版本、允许 `null` 的四维内容与 SHA-256 摘要，旧评分表不回填或虚构零分。工作台当前版本、历史列表与单项读取分开；旧版 `is_current=false`、不参与当前评分。
- **迁移/验收：**Alembic 新 head 为 `0007_student_portrait_snapshot`，冻结基线 SQL 不变；正式实例仍按维护窗口迁移，本机运行库只用显式安装器做开发验证。真实 HTTP+MySQL 用例经同一确认下两次 L2、新简历确认和新版 L2 共留 3 份快照，原版本可读但不再是当前，跨 owner 404、篡改快照 409；`CPA_TEST_PIPELINE_MYSQL=1` 的用例 **1 passed**。临时真实 MySQL 的 Alembic 清洁/旧显式表升级与重复执行 **4 passed**；默认后端全套 **1142 passed、108 skipped、7 warnings**（opt-in 用例另跑）。
- **真实页面回放：**重启默认 Flask 5001 与 `cpa.pipeline` worker，Beat 继续运行；以虚构文字 PDF 重跑 `backend/tests/manual/career_navigation_l0_probe.py --pdf --browser`，经真登录、模型、Redis、MySQL、Neo4j/worker 后，L0 17 阶段、画像 `ready_with_unknowns`、诊断 `ready`，快照表/HTTP 版本列表/单项哈希均核对通过，桌面真实页面展开历史区域，390px 无横向溢出。截图 `output/career-nav-browser-check/l0-text-pdf-history.png`；测试账号/文件/行由探针清理，隔离 Chrome 随后关闭。此回放仅证明文字层 PDF，不代表扫描 OCR；旧历史评分行安全回填和诊断历史仍待做。

### 2026-10-15 桌面阅读轴与结果内容呈现

- **旧行为：**1440×900 真结果页把画像和补充表单硬塞进 `72vh/28vh` 两个独立滚动区，屏幕中段出现横向断层；切报告还会自动进入沉浸模式、隐藏补充入口。结果底部另有学生不需要的“技术任务记录”和任务 ID；35 个原始技能词以大标签墙展开，4 项项目和 8 条竞赛在“查看其余”之间交错，同名竞赛显得重复。
- **新行为：**真实成果主区只有一条纵向滚动轴，先看当前画像，再顺序进入“继续探索与补充”；右侧真实检查点独立可读且默认只显示四步摘要，逐条记录按需展开，只给中文业务名称和安全摘要，不呈现内部类名/阶段代码/JSON/任务 ID。项目按原顺序完整展示；竞赛同名只做显示层分组，保留不同级别和原文条数并提示核对；技术词精确同词合并并标注提及次数，默认只预览 8 个，展开后可全部回看。解析、确认、L2 评分输入及后端记录均未因显示分组而改写。
- **重放：**在默认 Flask 5001、Vite 5174、MySQL/Redis/Neo4j、worker/Beat 与 Chrome CDP 9223 已运行时，设置 `CPA_L0_PROBE_BASE=http://127.0.0.1:5001`、`CPA_L0_PROBE_FRONTEND_BASE=http://127.0.0.1:5174`，以 `mkg` 运行 `backend/tests/manual/career_navigation_l0_probe.py --pdf --browser`。真实文字 PDF 的 L0 17 阶段、异步 L2 与报告完成；浏览器断言单一主滚动、结果和操作区顺序相接、右轨固定、切报告不丢自述入口，且右轨文本不含内部类名/阶段代码/任务 ID。截图 `output/career-nav-browser-check/l0-text-pdf-confirmed.png`、`l0-text-pdf-next-steps.png` 与 `l0-text-pdf-report.png`。另设置 `CPA_BROWSER_PROBE_BASE=http://127.0.0.1:5174`，运行 `backend/tests/manual/career_navigation_browser_probe.py --dense`：受控候选携带对应原文证据块，4 项项目、8 条竞赛分成 5 组、35 条技术词分成 34 个不同原词；桌面与 390px 无横向溢出，截图 `desktop-dense-stories.png`、`desktop-dense-skills-collapsed.png`、`mobile-dense-skills-expanded.png`。两探针都按自己的临时 owner 清理；后者仅是排版夹具，**不证明**真实模型能准确抽取复杂简历中的全部项目/竞赛。
- **回归：**前端 `npm run type-check`、`npm run build-only`、`npm run test:resume-trace` 均通过；构建仍有原有 lottie `eval` 与大包提醒。最近 Agent 画像读取改动的 `test_agent_business_resources.py`、`test_pipeline_reference_handoff.py`、`test_experts_authoritative_evidence.py` 为 35 passed。默认全套 1142 passed / 108 skipped 来自本次前端改动之前，不能据此宣称之后的所有后端路径已重新验收。页面没有改动正式评分/岗位匹配门禁；同名竞赛是否不同届次仍需用户对照原件，不能由显示层判断。

### 2026-10-15 结果页去执行记录与重复技术词计分纠正

- **可复现的旧行为：**`_calc_d2_skill` 对相同 `React` 出现一次/两次分别给 `skill_count=1/2`、技能子分 2.8/5.6；真实登录、MySQL 和 `POST /api/profile/student/pipeline` 的受控密集夹具中，原简历 35 条（其中同词一次重复），旧 L2 也返回 `skill_count=35` 并称“掌握 35 项技能”。前端虽只显示 34 个不同词，后端仍按重复提及计分。桌面截图还显示完成后的“本次简历分析的执行记录”长期占据右栏，挤压画像与补充操作。
- **修正边界：**只在 L2 D2 计算时按去除两端空白后的精确名称去重，技能数量、展示清单和深度子分一致；目标岗位覆盖率仍用既有逐要求项规则，`React`/`ReAct` 不做模糊合并，原 `student_profile.skills` 和 L0 原文不变。证据文案改为“简历提及 N 个不同技术词”，不将“提及”说成“掌握”。真实结果页居中连续阅读，画像、报告、补充内容保留，执行记录侧栏和结果态“过程”入口不再出现；**正在解析时**保留实际四步检查点和移动过程入口，但不向学生展开逐事件日志或历史尝试。原始轨迹仍由受鉴权后端接口保留供排障。
- **重放：**先运行 `mkg` 的 `backend/tests/pipeline/test_portrait_generator.py -q -k duplicate_skill_mentions`，旧实现应失败 `2 != 1`；改后该文件 **35 passed**。重启 Flask 5001 后，设置 `CPA_BROWSER_PROBE_BASE=http://127.0.0.1:5174`，运行 `backend/tests/manual/career_navigation_browser_probe.py --dense`：HTTP 返回和 `pipeline_task.result_json` 都为 34 个不同词，权威 `student_profile.profile_json` 仍有原始 35 条；浏览器桌面只预览 8 个、可展开全部 34 个，390px 不横向溢出，结果页没有执行记录侧栏。夹具账号、任务和资料自动清理。`backend/tests/manual/career_navigation_browser_probe.py` 普通受控夹具也通过自述修订、真实报告、旧版画像刷新和桌面/390px 表单；它直接植入已确认 L0，因此显式点击报告生成，不假装经过自动确认派发。
- **真实输入交叉核验：**默认 Flask 5001/Vite 5174、MySQL/Redis/Neo4j、`cpa.pipeline` worker/Beat、模型与隔离 Chrome 下运行 `backend/tests/manual/career_navigation_l0_probe.py --pdf --browser`：虚构文字 PDF 的 L0 共 17 个实际阶段、6 段证据，浏览器核对页只有四步解析进度、没有逐事件/历史日志；用户确认后两项后台任务 attempt 各 1，画像 `ready_with_unknowns`、报告 `ready`；1440px/390px 主阅读和报告入口通过，截图 `output/career-nav-browser-check/l0-text-pdf-confirmation.png`、`l0-text-pdf-confirmed.png`、`l0-text-pdf-next-steps.png`、`l0-text-pdf-report.png`。首次隔离 CDP 新标签未前置，后台计时器节流造成页面还显示“后台整理中”而后端已经 ready；探针现在打开标签后 `Page.bringToFront`，同流程重放通过。此核验是文字型 PDF，不代表扫描 OCR 或复杂真实简历的抽取准确率。
- **回归/限制：**设置 UTF-8 与 `TEMP/TMP/TMPDIR` 到仓库 `output` 后，`mkg` 全套 `backend/tests -q -x` 为 **1143 passed、108 skipped、7 warnings**；前端 `type-check`、`test:resume-trace`、`build-only` 通过。未设置临时目录的一次全套测试发生环境性多处失败/错误，不作为代码回归结论。D2 仍沿用原冻结权重，重复词更正不等于核实每个工具是否为真正掌握的能力；同名竞赛是否为不同届次、技能别名合并及真实岗位匹配仍待单独证据和产品契约。

### 2026-10-15 已确认简历方向建议改读服务端来源

- **旧链路与复现：**`cpa-xingtu` 真实结果页把候选技能数组从浏览器发送到通用 `POST /api/career/infer-role`，但该接口不读取当前登录账号或 L0 确认版本。只改请求中的技能，`Python` 与 `TypeScript` 在同一运行后端分别给出不同的方向；这适合通用探索，不足以把响应标作“已确认简历技能的方向建议”。
- **新链路：**页面对已确认简历改用 `POST /api/profile/student/confirmed-resume/roles`，只发送当前 L0 `task_id` 和可选兴趣偏好。后端按 Principal 调 `require_confirmed_profile`，从权威 `student_profile.skills` 推断，前后检查确认版本与技能列表，返回 `source_kind=confirmed_resume`、`exploration_only=true`、`scoring_eligible=false`。请求夹带客户端 `skills` 直接 400；旧版本和跨 owner 不得调用推断。通用 `/api/career/infer-role` 保留给其他非权威探索场景，技术技能自述继续走独立 `_skill_exploration` 路径，偏好不写回简历或 L2。
- **验收：**先新增 `CPA_TEST_PIPELINE_MYSQL=1` 的 HTTP/MySQL 回归，用真实 L0/`student_profile` 行证明新路由不存在时返回 404；实现后与方向推断和自述隔离测试合计 **26 passed**，检查伪造技能被拒、只读取 owner 的两项已确认技能、旧确认引用与跨 owner 拒绝。发现本机 5001 同时残留两个 `run.py` 进程后，逐一核对并停止、重新启动为唯一 Flask 实例；`cpa.pipeline` worker 在无 pending/running 任务时也重启加载当前代码。随后正式 Cookie 登录、真实 MySQL 的浏览器夹具验证页面只请求新路由，切换偏好会重新请求且 L2 task ID 不变，直接夹带伪造技能 400、无技能字段时回读确认来源与两项技能。真实虚构文字 PDF 再经异步 L0→浏览器确认→后台画像/诊断→方向建议，画像 `ready_with_unknowns`、报告 `ready`，两项 dispatch attempt 各 1；当次文档引擎为 Sidecar。夹具自动清理。
- **回归与边界：**设置 UTF-8 和仓库 `output` 临时目录后，默认后端全套 `backend/tests -q -x` 为 **1143 passed、109 skipped、7 warnings**；前端 `type-check`、`test:resume-trace`、`build-only` 通过（既存 lottie `eval` 和大包警告仍在）。这保证“简历技能”建议的输入来源，不证明目录岗位真实开放或匹配分已可发布。该接口只给职业方向探索，不参加 D2 或正式 L3/L4 评分；若确认版本在推断中改变，响应拒绝而非给旧结果。

### 2026-10-15 用户可读画像与原始线索分层

- **现场差异：**用户截图仍是旧的“技术任务记录”、35 个标签词墙及交错展开的项目/竞赛。当前 Vite 5174 实际服务的源码已不含“技术任务记录”或旧“简历提取的技能”标签；前一轮已去掉结果侧栏，但密集项目/奖项/技术词仍占正文。旧截图不能覆盖当前运行面；若浏览器仍显示旧版，需确认打开的是 5174 正式页面并刷新。
- **本次显示层调整：**项目按原序保留；竞赛同名记录不再将不同级别并排画成多个获奖徽章，而是写成“简历中同名记录 N 条／提取到的级别”，提示核对届次或奖项。技术词收进默认关闭的“核对简历中的技术词”，正文不再自动展开 34 个原词；提及次数仍可按需查看。概览改说“简历提及不同技术词”，不称“掌握 N 项技能”。不改 L0 原文、已确认资料、L2 评分或正式方向匹配。
- **回放：**`CPA_BROWSER_PROBE_BASE=http://127.0.0.1:5174` 下以 `mkg` 执行 `backend/tests/manual/career_navigation_browser_probe.py --dense` 成功；受控登录 + MySQL 候选夹具仍为 4 项项目、8 条竞赛合 5 个名称、35 条词合 34 个不同原词。浏览器断言结果页没有“技术任务记录”、清单默认收起、展开后全部可读、同名竞赛的两个级别在一条可核对文案里，390px 无横向溢出。截图更新于 `output/career-nav-browser-check/desktop-dense-stories.png`、`desktop-dense-skills-collapsed.png`、`mobile-dense-skills-expanded.png`。前端 `npm run type-check`、`npm run build-only` 通过，构建仍有既存 lottie `eval` 与大包提醒。此夹具证明呈现，不证明真实复杂简历的自动抽取准确率；原文级别本身仍须与原件核对。
