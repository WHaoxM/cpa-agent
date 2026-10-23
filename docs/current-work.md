# 当前工作入口（2026-10-23）

2026-10-23 **Ask 离线复核完成，本轮未改运行时代码**：补 100 轮/200 条合成消息与旧卡片恢复/第二、第三项重复读取验收，更新旧“16 条触发摘要”测试为“消息数不触发，需真实 token 压力”；四组主回归及额外 7 个预算边界用例通过。预算用例使用既有 UTF-8 保守估算器做测试专属注入，不验证真实 tokenizer parity；宽预算整套未重跑。旧用例曾触碰受限 Redis；修订后以假 SessionManager 隔离。真实前端/截图、数据库持久恢复、Tavily、跨进程 HTTP 幂等和旧事件取消隔离仍未测。逐项约定、五组完整命令结果、UI 输入/操作/预期及未来截图点见[Ask 离线验收清单](reports/2026-10-23-ask-offline-acceptance.md)。

2026-10-23 **QA 数据库连接仍不可达；未改库或尝试 Docker**：使用 `mkg` 解释器和项目配置对 `localhost:13306` 做只读连接/元数据查询，返回 `pymysql.OperationalError` 错误码 `2003`；未获得 schema 或授权信息。`Get-Service` 查询被 Service Control Manager 拒绝（提示可能需要额外权限），普通进程枚举未看到 `mysqld`/`mariadbd`。因此尚不能确认是服务未运行还是执行环境 socket 限制，`cpa_agent_qa` 未创建，后续需受支持的本机 MySQL 管理连接；没有重试 Docker/替换通道。

2026-10-23 **Tavily 本机密钥安全桥接不可用**：本机 Edge 标签清单没有 Tavily 页面，工具清单没有浏览器认证或云端到本地密钥安全传递工具；根 `.env` 的 `TAVILY_API_KEY` 仅以布尔方式核验为未配置。为核验截图能力，在新授权后仅重试创建 `about:blank` Edge 页，仍被工具拒绝：`Capability is not available: visibility`。没有读取云端 key、没有密钥外传、未联网调用、未生成截图；原先隐藏输入脚本保留待本机安全输入能力恢复后使用。

2026-10-23 **Agent 浏览器 QA 数据隔离前置条件**：本机 `.env` 实查为 `MYSQL_HOST=localhost`、`MYSQL_PORT=13306`、`MYSQL_DATABASE=cpa_agent`，因此当前连接是正常项目库，未用于此次回放。安全准备方案是在同一 MySQL 实例新建空 schema `cpa_agent_qa_<唯一后缀>`，只建立合成测试身份和数据；应用身份仅获该 schema 的运行时 DML 权限，迁移身份另获该 schema 内 DDL 权限。由数据库管理员预建 schema/账号后以 `MYSQL_DATABASE` 指向它；不导入 `cpa_agent`，不向应用身份授予全局 `CREATE DATABASE`。迁移专项测试会自行创建随机数据库，需另用隔离管理员环境或跳过，不以正常库执行。

2026-10-23 **Tavily 本机密钥输入已准备、尚未执行**：后端从仓库根 `.env` 读取 `TAVILY_API_KEY`；该文件由 `.gitignore` 忽略且未被 Git 跟踪，`.env.example` 只保留空占位。`backend/scripts/ops/configure_tavily_api_key.ps1` 会先验证这两项，再通过 `Read-Host -AsSecureString` 接收输入、只改该配置行并不回显值；未输入密钥、未联网调用、未重启服务。用户最小动作是在本机仓库 PowerShell 中运行该脚本并在隐藏输入提示粘贴 key；需在现有执行策略允许时运行，不用策略绕过参数。后端重启后才能载入。

2026-10-23 **Agent 自然语言续接已接入前端 store 和助手主页面**：当前 run 的普通文字按 `run_id + contract_revision` 调用 followup；仅服务端 `create_new_task` 分类才新建 run。202/未决请求复用原 body 与幂等键，保存意图停在精确版本的显式确认；续接消息在同一标签刷新后从 `sessionStorage` 恢复。`node --test tools/test-agent-task.mjs tools/test-agent-run-control-api.mjs tools/test-agent-run-control-panel.mjs` **36 passed**，`npm run type-check` 通过。没有真实浏览器登录、真实模型或安全合成账号回放：`cpa_agent` 尚未确认为隔离库；续接展示记录也尚未进入服务端聊天历史。见[AI 助手 owner](frontend/cpa-xingtu/pages/ai-assistant.md#当前任务的自然语言续接2026-10-23)。

2026-10-23 **Ask 摘要压缩已改为最终请求 token 压力触发**：使用完整模型预检中的消息、注册工具 schema、可用输入上限和输出预留；75% 已知预算触发摘要，估算未知不伪造压力。低压力时结构化会话状态可独立推进，摘要失败不推进摘要游标；Ask 不再走旧的字符数压缩。专测 `10 passed`。预算账本合并测试 `21 passed, 7 failed`，失败项尝试下载未缓存的 tiktoken 文件后被本机代理 socket 限制拒绝（`127.0.0.1:7897`, `WinError 10013`），未重试。未验证真实模型/数据库/UI；细节见[预算与 Ask 压缩 owner](guides/agent-call-budget-ledger.md#ask-compaction-pressure-2026-10-23)。

2026-10-23 **Ask 历史连续性与受控搜索切片推进中**：新增有原文引用校验的结构化会话状态、owner/session 绑定的旧消息/组件快照按需回查；Tavily API 适配现已接入服务端 allowlist，最多每 Ask 工具循环 3 次 basic 搜索并在 provider 错误后熔断。相关窄回归 `122 passed, 1 skipped`，使用假模型/假 provider/MySQL stub；真实 key、联网调用与当前前端页面截图未验证。接续[Agent 读取业务资料指南](guides/agent-business-resources.md#2026-10-23-ask-历史卡片的后续引用)；不要把此切片称为 100 轮真模型或真实 UI 验收完成。

2026-10-23 **共享调用预算预留已实现，Agent worker 接线待核**：`budget.py` 现能按持久任务传入的总模型/工具调用上限和阶段前已用计数，在 provider dispatch / `ToolNode.invoke()` 前预留预算；并为 Ask loop 在真实待执行工具批次前增加同一防线。针对 helper 的合成 provider/工具预算回归 **7 passed**，Ask/上下文/业务资料/turn-contract 定向集 **101 passed**。没有调用真实模型或 provider；Agent 的每阶段 worker 是否把 owner-bound MySQL 已用计数传入账本、MySQL/Celery 实际预算与真实前端验收仍待接线和回放，详见[Agent 持久运行指南](guides/agent-run-persistence.md#单任务模型工具调用额度的共享接入契约2026-10-23)。

这是一页**工作状态导航**，不是新的架构权威或完成证明。状态随工作树和运行环境变化；开始任务时先重新核对 `git status`、实际代码与服务。长期边界仍以 [`AGENTS.md`](../AGENTS.md)、[`CONTEXT.md`](../CONTEXT.md)、[ADR](adr/) 为准；运行行为高于文档描述。

2026-10-23 **持续上下文已从附件恢复扩展到本人原话接续与独立文档检查点，尚非成熟长期压缩**：正常真实页面执行多种追问、跨页、重登、刷新及长材料0→4000→8000→9527续读；历史输入digest／owner／文件／provider用量已核对，最终89项回归无跳过。撤掉误判学生合法表述的模型语义裁判；版本保护按实际委派收紧，未触碰另一任务定位代码，原页面设计未改。原纠正可恢复，但答复仍出现“未解决→尚未定位原因”的无据细化，不能称内容准确性全部通过；自动压缩、真实耗尽后的接续及更换资料后的纠正作用域仍待验收。接续[AI助手owner](frontend/cpa-xingtu/pages/ai-assistant.md#任务中的纠正与续读2026-10-23)与[同一报告证据](reports/2026-10-22-unified-task-orchestration-replay.md#纠正接续与文档续读2026-10-23)，不另开Guide。正常0020／Redis／原业务数据保留，仅空闲重载API与worker；最后空闲、测试schema已清理，未发送的用户输入页保留。

2026-10-23 **未发送附件刷新恢复与旧简历误读已在正常5174闭环重放**：沿用任务context/handoff持久保存待发送选择，入场核对后才消费；原助手/首页侧栏设计保留。真实DOCX发送前刷新、旧任务PDF恢复/移除、重登、同文件跨页追问及干净任务重放已执行；追加修复Lead误转探索及无方向写空探索。33项最终后端＋73项相邻、47项前端、类型/构建通过。正常Flask/worker空闲重载，0020、Beat/Vite和原数据保留；一次缓存导入失败由原Beat恢复，一次错误转场及多出的合成探索仍留作失败证据，不清历史或强改租约。只从[原报告补记](reports/2026-10-22-unified-task-orchestration-replay.md#未发送附件恢复2026-10-23)与[AI助手契约](frontend/cpa-xingtu/pages/ai-assistant.md#未发送附件恢复2026-10-23)接续；长期压缩/真实建议质量不在本轮完成结论。

2026-10-23 **正文与下游继续收尾：真实内容验收尚未完成**。现行analysis20／个人解读4分开已明确片段和待澄清问题，逐项引用不借别的动作／项目扩职责；同源解读与自述可实际回读，原设计不改。追加修复重复输入／相互矛盾JSON示例和原问题literal去重，不加cap、生成次数或删坏段落。独立真实HTTP复现并修复Ask连续性投影student_id未定义（失败后同路由1 passed）；来源与会话241 passed／2 skipped、隔离MySQL5 passed、前端26 passed／构建通过，均不代替真模型质量。最后冻结3b6341a881fe首稿Anchor形状不合格，唯一纠正再次截断，run失败、result_json=null；最新职责澄清到退出重登未到达，不能引用早期技术通过替代。21个指定schema、自有服务／文件／浏览器及Redis15已清，10个冻结目录无私有.env残留；正常0020未迁移或重启。后续先观察并复现截断候选，不原样重发挑答案。只从[原报告最终检查](reports/2026-10-22-real-page-experience-repair.md#最后一次逐句检查当前回归与未完成边界)和原页面owner接续，不新增Guide。

2026-10-23 **画像／简历解读／岗位报告阅读分层已落地**：保留原书架、纸质阅读器与花形图，采用总览＋单章完整阅读；已修正职途导航窄屏纵向居中导致报告开头不可达。隔离真实页面的阅读／追问／重登／权限／撤回及触控模拟通过，正常0020数据另只读复查、未迁移或重启共享服务。接续[阅读契约](frontend/cpa-xingtu/pages/my-reports.md#总览与章节阅读2026-10-23)、[简历章节契约](frontend/cpa-xingtu/pages/career-navigation.md#简历解读的阅读分层2026-10-23)与[原修复记录新增证据](reports/2026-10-22-real-page-experience-repair.md#画像与报告总览章节阅读2026-10-23)。两次完整固定资料回放及清理有记录；不代表真实模型措辞、真机或全部异常矩阵通过，原失败记录保留。

2026-10-23 **攀岩路径职责收紧已完成固定旅程对照，非普遍建议质量通过**：原设计保留，原文片段由服务端回填，不能借“独立查询”扩大前端职责；报名不混成到场，无据可选延伸删掉并标partial，团队职责未说明时只澄清。隔离head、真实worker／当前模型从职途导航完成起点→补充→下一步→重登，逐句核对本轮主线准确且有具体用途；五样本包含partial／仅澄清，不算五份个人分析。旧解读只回看，不自动再生成。接续[页面owner](frontend/cpa-xingtu/pages/career-report.md#个人解读内容契约第2版2026-10-23)及[同一报告的原始失败与最终核对](reports/2026-10-22-career-exploration-practice-replay.md#职责不扩大下一步说人话2026-10-23)。本任务未迁移或重载正常环境，正常库只读0020；隔离资源清理，DB12其他键不动，最终使用空DB15。正常共享写入、真机与真实学生理解研究未验收，少量例子和文案仍可精练。

2026-10-23：用户授权的后端开发公共参考链已在正常本机落地：0017→0020备份/恢复演练迁移，1官网岗位事实＋1明确粗估薪资＋8技能/8真实课程/7学习顺序，真实Neo4j与固定本地BGE-M3/Milvus24项投影及幂等重放通过。原账号重登、实际检索→课程详情→返回/刷新回访通过；原个人业务行和上传保留，重登仅更新账号活动。正式岗位评分目录仍0。接续[owner](guides/career-market-seed-boundary.md#版本化后端开发参考链2026-10-23)与[完整证据/限制](reports/2026-10-23-backend-development-pathway-replay.md)，不把此切片扩写为整站或生产完成。Milvus当前为Lite单进程文件锁，显式重投影前先停持锁服务。

2026-10-23 **统一任务已复现阻断修复，隔离后半段已回放；后经授权接通正常5174**：无据学历、候选内部字段和提交材料误称已核验已有窄修复；探索／复盘按阶段装配与一次有界结构整理保持原权限及30k账本。隔离完成确认、同方案保存、练习附件、复盘和重登；决定实际认证提交但不是本 Agent 点击，不编造轨迹。正常库只读核对0020、空闲重载Flask／worker并恢复5174，Beat和旧数据保留，未迁移／清Redis；既有合成账号验证新附件、跨页追问和刷新恢复，另修复了刷新默认打开最旧任务。接续[架构](architecture/platform-agent-capability-architecture.md#2026-10-22统一任务适配隔离验收非正常环境已发布)与[同一报告正常接通补记](reports/2026-10-22-unified-task-orchestration-replay.md#获授权接通正常5174与刷新跳任务修复)。本机gitignored开关已启用，仓库缺省仍关闭。首轮冷重载期间未发送附件焦点丢失仍保留为未定因失败；通用长期压缩／内容质量／移动专项不在通过结论。隔离资源已清理；早期源码残留被宿主拒删，不写整轮清理全通过。

2026-10-22 **首页接着学已接入5174**：原学习进展区独立续接最多三门本人未全部标记的课程，不改变简历首屏主次或原设计。隔离及正常既有合成账号完成标记、返回首页、续学、退出重登、继续／取消，无刷新；原课程故障／权限主线另已重放。正常仍0017，仅Flask从54524重载至52352；原账号成果保留，新合成章节记录有意保留。接续[首页owner](frontend/cpa-xingtu/pages/home-center.md#首页接着学2026-10-22)及[证据](reports/2026-10-22-home-learning-continuation-replay.md)。用户本人账号、原生200%缩放、真机与全站质量不在通过结论；CSS放大检查与其缺陷修复单独记录，不冒充原生缩放。

2026-10-22 为后续真实录屏，用户已确认向本机正常库追加 **8 份官方公共学习资料、24 章**，原课程/章节逐行保留、学习记录计数未变；真实5174代理搜索/详情与外站链接已回查。本人账号尚未确认，未改密码/注册/修改私人资料，完整页面与录屏未验收；3个官方岗位研究候选未发布，公开岗位仍为空。接续[学习中心owner](frontend/cpa-xingtu/pages/learning-center.md#已核对公开资料的补充)及[追加证据](reports/2026-10-22-course-learning-return-replay.md#正常公共资料追加2026-10-22)，不把本机数据追加当作线上部署或整目录真值签审。

2026-10-22 **首页接着做已实现并从5174回放**：原山水／深色AI卡／路径／三栏保留，首屏按简历权威状态续接，另有已保存草稿入口，最近进展区分简历确认、诊断及原课程／岗位报告。隔离与正常真实页面完成编辑保存、候选核对、确认、新结果、退出重登，无刷新。接续[首页owner](frontend/cpa-xingtu/pages/home-center.md#隔天登录从首页接着做)与[日期化证据](reports/2026-10-22-home-resume-continuation-replay.md)，不另建Guide。正常库仍0017，仅Flask重载至54524，原worker／Beat／Vite和存储保留；合成owner经本人操作更新当前资料，原账号／不可变成果／原上传校验保留。完整键盘、原生缩放、真机、所有异常组合及其他agent的0018均未纳入通过结论。

2026-10-22 花形画像→具体诊断→本人草稿→重新确认已接到 **5174正常入口**：按用户授权停写备份、临时恢复和迁移演练后，正常库从0014顺序升级至0017，Flask／worker／Beat已重载，Vite及存储服务保留。原账号、业务成果和原上传文件核对保留；新合成账号在正常页面完成编辑、重登、新确认及新结果回访，账号和新成果也保留。接续[原工作台旅程](guides/backend-user-journey-acceptance.md#真实前端看懂画像补好一段简历后重新确认)、[制作页合同](frontend/cpa-xingtu/pages/resume-builder.md#从诊断承接本人草稿)、[页面状态读取合同](frontend/cpa-xingtu/pages/career-navigation.md#真实页面信息层级修复-2026-10-22)和[状态读取修复证据](reports/2026-10-22-real-page-experience-repair.md#首次任务状态读取恢复补记2026-10-22)。首读暂不可用退回上传区的问题已修复；最终代码的真实5174无注入上传→本人核对→确认→退出重登同task读回和有注入status/candidate故障自动恢复均已通过，不刷新。底层初次网络失败原因仍未知；因两次新注册收到429未继续注册，最终主线复用既有合成账号，不触及原正式账号。原账号/所有成果均保留，原点阵、全键盘／真机和普遍模型质量仍未验收。并行agent的独立CareerAnalysis页面改动保留，未纳入本轮验收。

2026-10-22 职业方向探索继续深化为**原页内 Agent 陪伴**，默认仍是原岗位舆图，不再整页替换。隔离真实 Vue／持久 QA worker／当前模型已完成讨论、澄清、同方案确认、练习附件、复盘、本人决定和切号重登；从[职业分析契约](frontend/cpa-xingtu/pages/career-analysis.md#原页内探索陪伴2026-10-22)与[同一报告补记](reports/2026-10-22-career-exploration-practice-replay.md#原页内-agent-陪伴从静态填表改为讨论)接续，不新建 Guide。正常库现场为 `0017_resume_draft_guidance`（此前 0014 是历史状态），本轮未升级或重启共享服务；新陪伴需 0018 与相关服务重载，当前不能声称正常入口已上线。原地图完整绘制、建议质量及真机仍未验收；有一个早期失败遗留的独立 Chrome 缓存目录未清除，精确位置见报告，不把最终回放清理成功扩写成所有历史失败也全部清理。

2026-10-22 画像阅读后的补充／回访已接通：原阅读器按缺口进入既有自述或简历核对区，后台状态与保存分开，历史只读且当前资料两端重核。隔离真实账号／worker旅程、重登／切号及正常账号只读均已回放；接续[页面契约](frontend/cpa-xingtu/pages/my-reports.md#从画像找到下一步)和[原报告补记](reports/2026-10-22-student-resume-builder-replay.md#从画像阅读到补充资料并回访)。本轮未升级正常库或改保留账号成果；隔离资源已清理。后续专项已复现外部字体 CSS 阻断 HTML 解析的登录空白，并以非阻塞加载修复；26次独立配置页面检查及其中4轮真实登录／退出通过，失败和边界见[同一报告的冷加载补记](reports/2026-10-22-student-resume-builder-replay.md#登录页冷加载空白诊断与修复)。最早那次无网络记录的失败仍保留，不倒推其唯一根因；不与新职业探索或职途导航视觉工作合并。

2026-10-22 技能自评真实保存与回访已执行：先复现提交失败仍显示本地结果，再接服务端版本化题包、本人快照、未知选项、幂等保存与最近记录回读。真实 Vue 验证重登、题包变化、丢失响应、并发重试与账号隔离；接续[用户旅程指南第九条](guides/backend-user-journey-acceptance.md#第九条旅程学生完成技能自评离开后回来查看自己的回答)及[日期化证据](reports/2026-10-22-self-reflection-return-replay.md)。本轮隔离资源已清理；收尾共享库现场为 `0014`，本轮未升级，正常使用新模式需另行迁移至 `0015_self_reflection`。自述不等于认证，不自动改变简历/画像/匹配；题目质量、真机移动与旧命名熟练度推荐真实 SQL 专项未验收。

2026-10-22 普通课程学习已接入真实 HTTP：目录搜索/分类、章节和材料、本人已学标记、重登恢复及失效清除已完成真实 Vue 回放。进度不预置；打开材料不写学习记录，标记不等于能力认证。接续[用户旅程指南第八条](guides/backend-user-journey-acceptance.md#第八条旅程学生选择课程记录章节进度离开后回来继续)及其[日期化证据](reports/2026-10-22-course-learning-return-replay.md)，不要向岗位探针继续堆分支。隔离库/Redis/目录/进程已清理，共享开发库仍为 `0010`；移动端、真实材料质量、全站 Mock 与计划旅程完整重跑未验收。

2026-10-22 “没有现成简历”分支已接入账号草稿与只读智能填充。随后按用户授权完成正常开发库备份、恢复演练和 `0010→0014` 升级，正常真实前端 `http://127.0.0.1:5174/`、Flask/worker/Beat 已启动；新合成账号从页面完成草稿重登恢复、真实填充、提交确认、分析及再次登录读回。原有业务行/上传文件核对未变；本次账号和成果有意保留，不按隔离回放清空。从[原旅程指南](guides/backend-user-journey-acceptance.md#真实前端没有现成简历从整理经历到确认分析)和[同一报告的正常开发环境补记](reports/2026-10-22-student-resume-builder-replay.md#正常开发环境接通与完整页面回访)接续。下方“共享库仍为0010”仅是此前各轮的日期化状态，不代表本次升级后的现场；服务非系统自启，接续时仍需重新检查。

2026-10-21 首次简历使用主线已从空账号真实 Vue 注册/文件上传开始回放，修复菜单回访恢复、无原文年级、关联未就绪的诚实状态、报告内部字段及确认按钮样式变量；独立回放覆盖进度读取故障与重试。只从[用户旅程指南的首次使用入口](guides/backend-user-journey-acceptance.md#真实前端新学生第一次上传简历离开后回来继续)与[同一工作台报告补记](reports/2026-10-21-student-workbench-journey-replay.md#真实前端首次使用补记)接续。Neo4j 开启后已补齐现有审核公共种子和索引，并重放真实检索/模型关联提议，旧图摘要未变；人工审阅、个人图投影、整体模型质量与移动端仍未验收，不据此扩大为整站完成。

2026-10-21 岗位对照持续使用：首次报告后更新简历、重登恢复、重新确认并生成/找回新报告已通过真实 Vue 回放；修复迟到旧响应恢复旧分数，旧报告内容与原收藏快照保持不变。接续[用户旅程指南第二条](guides/backend-user-journey-acceptance.md#第二条旅程学生从已确认简历到岗位决策)与[重新评估证据](reports/2026-10-21-student-resume-reassessment-browser-replay.md)，不新增场景编号。上传目录隔离已修正，正式回放环境已清理；首次失败的两个合成上传文件因宿主拒绝删除仍留存，位置见报告，不宣称全部收尾通过。真实模型/建议质量与 Celery 投递未验收，其他 agent 的首次使用/账号切换证据仍独立维护。

2026-10-21 账号切换阻断已闭合：真实 HTTP 重现并修复旧请求恢复 Redis 会话，真实 Vue 重现并修复迟到身份覆盖及认证缓存等待；账号/岗位来源隔离窄回归 34 项及并发 HTTP 2 项通过，标准空库下的 CSB 发布→学生浏览→管理员重登撤回→学生回访完整通过。只看[用户旅程指南第六条](guides/backend-user-journey-acceptance.md)与其[最新日期化证据](reports/2026-10-21-job-source-to-student-catalog-replay.md#退出与账号切换修复复验)接续；收藏旅程按下一段与第七条的独立证据维护，不由本轮官方来源旅程代为验收。

2026-10-21 当前前端旅程工作：管理员导入原路径已复验；学生收藏、退出重登找回、跨账号隔离及撤回后历史回看已在隔离 MySQL/Redis 和真实 Vue 页面完成。本轮修正管理员“不限专业”显示、跨资料确认项残留及收藏日期截断；证据和命令从[用户旅程指南第六、七条](guides/backend-user-journey-acceptance.md)进入。结论仅覆盖合成资料的真实页面与持久化链路，不代表真实招聘数据或建议质量通过。共享 `cpa_agent` 开发库仍为 Alembic `0010`，本轮没有升级它。

Q1/Q3/Q4 的逐项证据、未达门槛和外部停止项见[下一阶段验收对账](reports/2026-10-17-next-stage-acceptance-audit.md)；它是有日期的报告，不代替现场复核。

## 先按目的读取

2026-10-22 画像补充旅程的五项体验修复后，又完成版本纸签、窄屏首屏入口及关闭／退出收尾。专项12次与完整真实worker旅程通过；已区分移动目标未命中的驱动失败、重复关闭及旧动画回调，不唯一倒推最早无轨迹的失败。只接续[同一报告收尾补记](reports/2026-10-22-student-resume-builder-replay.md#画像回访三项收尾)。业务验证通过，但早期一个Chrome临时目录被宿主拒删、DB10在逐轮清理后被后续键占用，整轮清理不能写全通过；正常账号只读，本轮未执行正常库升级。

2026-10-22 “我的报告”已恢复原双书架和抽书阅读，接本人画像快照与有效岗位报告；画像历史、容量分页、直达／重登、追问和来源失效已回放。正常环境只读验证原账号一份画像与零份岗位报告；隔离环境覆盖双架正向及权限。接续[页面契约](frontend/cpa-xingtu/pages/my-reports.md)和[原报告的双书架补记](reports/2026-10-22-student-resume-builder-replay.md#我的报告双书架恢复与真实阅读)，不与学习中心任务合并。删除、导出、成长分析未实现；一次旧 live 回归误用默认库的隔离偏差及清理须按报告追溯，不能宣称本轮正常库全程无临时写入。

**2026-10-22 页面复审修复已实施：**学习中心恢复原分类／图表／封面卡语法，树图用全目录数量，弦图明确待就绪；简历建议、过程及诊断正文／依据已分层。原课程与首次简历真实 Vue 主线、正常资料只读复查及具体边界见[统一修复证据](reports/2026-10-22-real-page-experience-repair.md)，原复审失败仍保留。用户另行批准仅重启正常 Flask，`5174` 原入口已实际回查，worker/Beat、账号与成果保留；没有升级正常库或导入资料。两份官方文档资源只在隔离库验证，正常五条旧资料乱码的清单仍待人工确认。最后一个 Chrome 临时目录被宿主策略拒绝清理，业务回放通过但整轮收尾未全通过；路径与后续动作见报告。原生缩放、真机、材料／建议质量未验收，不因此扩大新场景。

本轮课程回放另保留一次进度写入前会话401的未定因失败；换空专用 Redis 的完整重放通过不能代替定因。后续接手先看报告失败记录，不放宽认证或自动重发掩盖问题。

**职途导航视觉纠偏部分完成（2026-10-22）：**网格／纸纹、中央舞台、深色过程栏和本地图标已复验，认可的制作页保留。Git 续查与三代历史页面实际录屏未定位原点字矩阵波动；此前误用的星图圆形扩散纹已撤下，不能写成恢复通过。接续[原报告的续查记录](reports/2026-10-22-real-page-experience-repair.md#git-历史续查纠正波纹误认)；不新开场景、不回退真实链路、不接入无调用证据的旧 Canvas 组件。

续查已扩到历史解析／结果、全局背景及真实worker完整旅程；旧 `InkWash` 首页晕染和 `D3FlowerChart` 结果风力均有调用证据，但未认定为点阵，未改前端视觉。实际录屏、权威来源、28项相邻回归与清理从[同一报告的状态／全局续查](reports/2026-10-22-real-page-experience-repair.md#解析中结果页与全局背景续查)接续；恢复仍未完成。

本轮各次隔离清理时Redis为空，但最终DB14再次出现归属未明的键，未读值／未删除；后续不要复用或清空DB14，应先确认空库独占。完整证据在同一补记和本机最终审计JSON，不把整个逻辑库“当前全空”当作已保证。

| 要做什么 | 最短入口 | 不要误用 |
|---|---|---|
| 在主仓改后端 | [`AGENTS.md`](../AGENTS.md) → [`README.md`](../README.md) 的“运行环境”一节 → 相关 [`CONTEXT.md`](../CONTEXT.md) 段落 | 不从旧报告推断今天的路由和服务状态 |
| 本机启动、部署或恢复 | [`DEPLOY.md`](../DEPLOY.md) → [MySQL 迁移手册](runbooks/mysql-migrations.md)；镜像加固提案见 [`production-image-hardening.md`](specs/production-image-hardening.md)，本机回放见 [Q1 证据](reports/2026-10-17-q1-hash-image-offline-replay.md) | 提案、Compose 静态检查和 `/health` 均不是上线批准 |
| 学生工作台与简历链路 | [产品目标](specs/student-workbench-experience.md) → [状态/迁移契约](specs/student-workbench-state-contract.md) → [最近一次后端验收](reports/2026-10-13-secondary-backend-acceptance.md) | 目标契约、旧测试计数不等于当前真实服务已通过 |
| 前端页面与接口 | [前端文档入口](frontend/cpa-xingtu/README.md) → 独立仓库 `cpa-xingtu/README.md` | 主仓文档不代替前端仓库的实际路由；Mock 页面不是真实后端验收 |
| Agent 通用能力改造 | [上游复用记录](architecture/agent-upstream-adaptation.md) → [平台能力架构](architecture/platform-agent-capability-architecture.md) | 本地 `opencode/` 是参考快照，不是本项目运行时依赖 |
| 知识产权材料 | `ip-materials/README.md` | 申请稿、竞赛材料不作为现行代码与权属事实的替代 |

完整文档分层和历史材料入口见[文档地图](README.md)，不必每次通读其“Recommended Active Set”。

旧版根目录 `CODE_WIKI.md` 与四份早期日志/数据库设计稿已保留在 [`archive/2026-10/`](archive/2026-10/)；它们只供追溯，不再与现行运行指南并列。

## 当前工作流与核验点

以下是从 2026-10-17 工作树和文档得到的**盘点**，不是对他人未提交改动的归属判断或优先级授权。

| 工作流 | 观察到的状态 | 下一核验点 |
|---|---|---|
| 生产部署与恢复 | `main` 基线之上仍有未提交部署候选；[隔离卷恢复实证](reports/2026-10-17-q1-isolated-volume-recovery.md)已回读三种数据库和文件标记。[新哈希锁镜像离线复现与回放](reports/2026-10-17-q1-hash-image-offline-replay.md)从 23 文件白名单干净源码 `7353dd7`、固定基础层和 149 个已验 wheel 构建：两次断网/无缓存 OCI 归档在禁用 `.pyc`、固定时间戳后逐字节相同；镜像 `72ca...` 在全新恢复 `g` 上验证迁移、HTTP/worker/Beat、只读根、显式受信本机 HTTPS 及正式账号 owner-bound MySQL 收藏结果。随后把 `--no-compile` 单独冻结为干净候选 `91b6e45`，新镜像 `75a...` 再次两次逐字节复现，且在另一全新恢复 `h` 重放同样的本机窄链，七组部署专项 **50 通过、4 跳过**。另在旧镜像的独立副本 `e` 通过简历 **202 入队、本人追踪 200、他人 404**，但未启动 worker，任务仍 pending。 | 离线派生 Dockerfile/轮子集未公开分发；`h` 仅是本机显式受信的恢复回放，不是外部可信分发或完整 L0。真实旧卷内部 owner、registry、正式可信域名/证书和完整业务成果仍是门槛。 |
| 学生工作台 | 最近提交 `a8ef1f1` 涉及持久分析和部署检查；[状态契约](specs/student-workbench-state-contract.md)记录 P0、P2、P1 首切片及旧数据迁移、P3、故障矩阵等缺口。 | 具体任务开始时重新核对路由、迁移、worker/Beat 与 owner-bound 成果，不直接沿用文档中的历史测试数字。 |
| Agent 运行时实验 | 22 个未跟踪模块已在[逐项取舍报告](reports/2026-10-17-q3-agent-candidate-disposition.md)逐文件对照现行实现与本机上游快照；22/22 继续隔离，不删除、不整包接线。此前默认 HTTP 多轮只用确定性 Supervisor/存储替身回放 `1 passed`，不是真模型验收。 | 若出现具体默认路径缺陷，才按上游复用、owner/预算/工具结果和真实请求验收决定薄适配；先确认文件归属才可归档。 |
| 前端 | `cpa-xingtu/` 是独立 Git 仓库，本地主仓不跟踪其源码；[Q4 分流回放](reports/2026-10-17-q4-frontend-worktree-review.md)核对了 13 个未跟踪文件，并在独立前端分支修正一条 Mock 岗位介绍串用前端文案的实际页面缺陷。 | Mock 与后端 HTTP 分开验收；13 个旧稿/截图/脚本仍待归属。该仓本地没有 `AGENTS.md`，不擅自补造规则。 |
| 竞赛与知识产权交付 | 根目录竞赛文档和 `ip-materials/` 属另一条交付线；后者有自己的说明及分件入口。 | 与代码发布分开管理版本、权属与对外承诺；不按“根目录杂物”自动移动。 |

## 待办队列（建议处理顺序，非已获授权的产品优先级）

| 序号 | 状态 | 可独立领取的下一步 | 完成判据 / 当前阻断 |
|---|---|---|---|
| Q0 工作区保护 | 整理范围已核验；后续持续执行 | 每次切换工作流先看主仓及独立前端仓的 Git 状态，按下文分组领取，禁止对未归属内容做 `git clean`、重置或混合提交。 | 五份旧文档的旧路径移除与新归档必须成对看待；整理提交不代表其他工作流已获准入主线。 |
| Q1 部署候选 | 原冷快照已恢复到七个全新目标 project；`g` 的 `72ca...` 和 `h` 的**新干净来源镜像 `75a...`**各自在只读根/零 capability 下完成五类 marker、迁移、HTTP/worker/Beat、本机证书显式受信的 HTTPS 正式账号 owner-bound 收藏写删。139 项锁的目标平台 wheelhouse、真实离线 hash-mode 安装和两次字节级构建通过；`e` 的 L0 仍只验到 pending 入队。最小修正已独立冻结为 `91b6e45` 并推送到 `codex/q1-hash-locked-local-acceptance` 审阅分支，七组专项 50 通过、4 跳过。 | 按[新镜像回放](reports/2026-10-17-q1-hash-image-offline-replay.md)保持新候选的代码/技术文档独立审阅；盘点真实旧卷内部 owner、验证完整 L0 最终成果；待服务器/registry/可信 HTTPS 确定后做外部验收。 | 本机离线 Dockerfile/wheelhouse 被忽略且无可信分发；只调整测试卷顶层不能保证旧文件/WAL 可写；收藏结果、Beat 空队列与 pending 入队不能替代完整业务链或生产发布。 |
| Q2 学生工作台 | 待现场重验 | 选一条有用户结果的路径，从 HTTP 入口到持久状态重放；随后按[状态契约](specs/student-workbench-state-contract.md)逐项领取缺口。 | 真 MySQL/Redis/Neo4j、worker/Beat、归属回读和失败收束均须标清是否实际可用；不继承旧报告测试数字。 |
| Q3 Agent 候选模块 | 22 件已逐项分流，当前均继续隔离；归属未确认 | 按[Q3 报告](reports/2026-10-17-q3-agent-candidate-disposition.md)保护原件；只在出现具体默认路径缺陷时领取单项适配，不以草案清单整包实施。 | 尚无候选接线/真实模型验收；自动写词典、进程内“持久”状态与不完整工具/文件边界禁止原样接入。 |
| Q4 前端独立仓 | 13 件已分流；一条 Mock 缺陷已在独立分支修正 | 按[Q4 报告](reports/2026-10-17-q4-frontend-worktree-review.md)认领旧设计、G6 截图及两个脚本；尤其先定三类缺失岗位介绍的正确数据合同，处理已跟踪包入口指向未跟踪且红灯的检查脚本。 | 前端分支 `codex/q4-role-intro-mock-boundary` 的浏览器回放和构建只验 Mock；不并入主仓，也不宣称 HTTP/正式账号验收。 |
| Q5 竞赛 / 知识产权 | 待权属与版本确认 | 按 `ip-materials/README.md` 分件检查源码快照、作者/协议和对外材料；根目录四件竞赛文件单独定归属。 | 不把校稿 PDF、竞赛描述或未冻结工作树当作可提交事实；不替他人决定申请主体。 |

**Q1 验收后清理：**按用户要求，8 个已停机且标签逐项核对的隔离 Docker project 现已移除（43 容器、69 卷、8 网络），旧镜像与旧源码副本、重复 OCI 包/下载分段也已清理。验收结论保留在报告中，**现场不再有这些测试卷**。本机仅留[最终复验材料清单](reports/2026-10-17-q1-hash-image-offline-replay.md#本机临时产物清理验收完成后)及 WSL 已验冷快照；下一次回放必须新建 project，不能复用 `h` 的历史状态。原业务卷和归属不明文件未动。

Q1–Q5 是不同交付单元；本页只提供入口和停机条件，不自动开启部署、重构或对外递交。

[续审报告](reports/2026-10-17-residual-worktree-review.md)记录的是当时的 **11 个已跟踪修改、37 个可见未跟踪文件**及分组意见。2026-10-17 18:24（上海时间）再次核对，主仓为 **11 个已跟踪修改、48 个可见未跟踪文件、暂存区空**；增加了哈希锁候选等新工作和来源未明的小文件，非技术交付及真实简历审计材料仍按本机 exclude 隐藏但保留。独立前端仓仍为 **13 个未跟踪文件**。下方 114 文件表是此前提交前的完整盘点快照，不能与当前数字直接相减推断文件被删除；接手时应重新运行盘点。

## 未跟踪文件分流（主仓，2026-10-17 提交前快照）

以下按 `git -c core.quotePath=false ls-files --others --exclude-standard` 的**实际文件**计数，不按 `git status` 中折叠的目录条目计。提交前盘点可见 **114 个文件，12 组恰好覆盖 114 个**；分类是处理建议，不证明作者或允许提交。整理分支提交后，归档和本页等将不再计入未跟踪数；接手时须重新盘点。

| 范围 | 文件数 | 类别与处理 |
|---|---:|---|
| `docs/archive/2026-10/*` | 5 | 本轮已核对正文的旧文档归档；**保留并成对核验旧路径**，不清理。 |
| `docs/current-work.md` | 1 | 本轮当前入口；保留，后续按日期刷新。 |
| `docs/reports/2026-10-17-*-triage.md` | 2 | 本轮 Agent/部署分流证据；保留为限时报告。 |
| `docs/reports/2026-10-10-*` | 2 | 既有真实 PDF/语义探针证据；保留审计，不提升为现行运行保证。 |
| `backend/production_wsgi.py`、`backend/scripts/ops/*`、四个 `backend/tests/test_{production_wsgi,release_preflight,volume_archive,volume_snapshot}.py`、`docker-compose.prod.host-proxy.yml`、`docs/specs/production-image-hardening.md` | 10 | 主工作树原件继续保留；已从白名单独立冻结为 Q1 干净候选并推送审阅分支，**只通过本机窄验收，尚非正式发行**；不与整理分支混合提交。 |
| `backend/app/services/agent_runtime/{agent_loop_integration,session_compaction,session_input,session_runner,tool_registry}.py`、`backend/app/utils/` 中 17 个未跟踪 `.py` | 22 | Agent 基础能力实验；保持隔离，按 [专项核查](reports/2026-10-17-untracked-agent-modules-triage.md)逐一取舍，不整包接线。 |
| `.opencode/MIGRATION_PLAN.md` | 1 | 旧迁移草案；留作候选思路，不作为执行指令或现行架构。 |
| `.ulpi/design/{DESIGN,landing-revamp}.md` | 2 | 落地页设计稿；待前端归属确认，不搬入后端主线。 |
| `ip-materials/*` | 62 | 内部申请材料、LaTeX 原稿、分件/汇总 PDF 与清单；按其 README 单独管理版本和权属，不整目录递交或当缓存清理。 |
| 根目录 `作品简介.docx`、`作品设计文档.pdf`、`必看-2026移动云杯AI_Coding参赛材料.md`、`附件2：2026移动云杯AI_Coding赛道_参赛作品.docx` | 4 | 竞赛资料；归属和对外版本待确认，保留原位。 |
| `workspace/default/branding/logo.png` | 1 | 暂缓 workspace 样例/素材；保留，不宣称 live branding 功能。 |
| 根目录 `query`、`r.name.includes('jsdelivr')).length` | 2 | 提交前快照中内容分别为 `Docker Desktop Service`、`result: None` 的疑似调试残留；**来源不明，保留待认领**。同日又持续出现相似小文件，见下方动态状态；不回改本表 114 件的历史计数。 |

另有已按规则忽略的本机参考/产物：独立 `opencode/`、Tika JAR、Celery Beat 状态、`backend/archive/` 和本机旧 `docs/archive/2026-07/`。它们**不在上述 114 个可见文件之内**；忽略仅表示不进入主仓待办，不等于可以删除。独立 `cpa-xingtu/` 的未跟踪文件也不计入主仓。

**提交后的本机保护补充：**按用户澄清，项目技术文档可单独提交；竞赛/知识产权等非技术交付文件不得随之提交或推送。本机 `.git/info/exclude` 现仅在本机隐藏 `ip-materials/`、根目录四件竞赛文件、暂缓 branding 素材及两份含真实简历审计信息的 2026-10-10 报告；文件仍在原位，排除规则不随 Git 克隆分发。上表保留的是设置排除前的完整盘点快照；不要因它们不再出现在普通 `git status` 中就以为已删除，亦不要对这些路径使用 `git add -f`。

## 文件区域：先定性，再处理

- **当前源码/配置**：`backend/`、根目录 Compose/环境模板、`docs/`。改动需按各自测试和真实路径核验。
- **独立仓库/上游参考**：`cpa-xingtu/`、`opencode/`。不要把它们并入主仓 Git 状态或当成本项目已运行代码。
- **历史与运行产物**：`archive/`、`output/`、模型缓存及 Celery beat 状态文件。除上文已核验归属的 Q1 临时副本外均保留现状；后续清理仍须核对引用、运行进程与恢复需求。
- **暂缓能力**：`workspace/default/branding/` 不是当前承诺的 live workspace 功能；边界见 [`CONTEXT.md`](../CONTEXT.md)。
- **待分类小文件（动态）**：根目录原有 `query`、`r.name.includes('jsdelivr')).length`；2026-10-17 16:15–16:49 又至少出现八件以 JS 表达式片段或中文词命名的小文件，例如 `x.outerHTML.slice(0`、`x.innerText)`、`x.href).slice(0`、`x.innerText.trim()`、`({type`、`x.getAttribute('placeholder')).join('`、`x.textContent.includes('新建工单')).length`、`报名`。抽查内容只有 `result: None` 或空文件，产生者/用途未确认，且同日有继续新增迹象；不得因表面像调试输出而删除或提交。接手时重新盘点，历史表的 2 件不代表当前总数。

**本轮终审的动态对账：**主仓此时可见未跟踪实际文件 **48 件**，分为 22 个隔离 Agent 候选、12 件仍在主工作树的 Q1 源码/测试/锁/技术提案原件、`.opencode` 草案与 `.ulpi` 设计稿 3 件、根目录来源不明的小文件 **11 件**。后者比上面的早期抽查又多出 `x.textContent.trim()`；继续保留待认领，不为凑数删除。另有已跟踪修改 11 件、暂存 0 件；受本机 exclude 保护的竞赛/IP 与真实简历材料**不计入这 48 件**且仍在磁盘上。独立前端仓此时可见未跟踪 **13 件**、已跟踪修改和暂存均为 0；这些是动态快照，接手时重新运行盘点。

## 第二轮分流（不等于删除）

- 已确认 `opencode/` 是独立、干净的上游参考仓库（本地 commit `388406238b`）；将它从主仓 Git 待办中排除，仍可按[上游复用记录](architecture/agent-upstream-adaptation.md)读取。
- 已确认 `backend/celerybeat-schedule.*` 是 Beat 本机状态文件，`models/tika/tika-app-3.3.3.jar` 是本机第三方解析组件；两类改由 `.gitignore` 排除，不改变文件本身。
- `.opencode/MIGRATION_PLAN.md` 是一份“迁移高级功能”草案，其“待执行”表格不能替代当前已采用的开源优先与薄适配边界；22 个新模块也不因为与草案同名就自动进入主线。
- `.ulpi/design/`、`ip-materials/`、竞赛文档、未跟踪的部署源码/测试和两份报告仍保持可见；它们是待确认的设计/交付/工作成果，不按缓存忽略。
- 两个根目录小文件内容分别为 `Docker Desktop Service` 和 `result: None`，疑似调试输出；已确认内容但未确认产生者，暂不移除。

## 第三轮归档与交接提醒

- `CODE_WIKI.md`（2026-06 生成）、早期 `dev_log.md`/`bug_log.md`、两份旧数据库设计稿已移入 `docs/archive/2026-10/`，并在新位置加归档说明。逐行核对了五份归档正文与 Git 中原版：除新增说明外，正文完全一致。
- 根目录中英文 README 已把旧 Code Wiki 从“现行运行指南”改为历史入口；`docs/README.md` 不再把被忽略的本机 `.trae/` 规则当作可分发的 schema 权威。`jd_cleaner_usage.md` 因相关工具仍有代码引用，暂留原位待单独验证。
- 为使这五份新归档能被 Git 看见，`.gitignore` 的 `archive/` 改为只忽略根目录 `/archive/`；此前未跟踪的 `docs/archive/2026-07/` 与 `backend/archive/` 仍单独保持本机忽略，不借本轮整理意外加入。提交前 Git 把旧路径显示为删除、新归档目录显示为未跟踪；这两侧需成对处理，不可对新归档执行 `git clean`。

## 本轮整理边界

提交前核验快照中主仓位于 `main`：**13 个已跟踪修改、5 个旧路径删除、46 个未跟踪状态条目（展开为上表 114 个文件）、0 个暂存文件**；独立 `cpa-xingtu/` 仓另有 **11 个未跟踪状态条目**，不计入主仓。初次盘点是 13 个修改/47 个未跟踪条目；整理新增归档和报告，同时将一个后端旧归档目录恢复为本机忽略，不能比较两个数字来推断丢失文件。未跟踪目录在 `git status` 中按一个条目计，接手时必须重新运行。五个旧路径显示为删除、新归档目录显示为未跟踪，只是当时尚未暂存的移动，不代表正文被丢弃。本轮未删除原文内容或重置其他工作。后续每条工作流应单独核验、单独收束；完成证据放对应 guide/runbook/report，本页只保留简短状态与入口。
