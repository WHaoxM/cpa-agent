# 旧版二进制 `.doc` 简历：Tika 文本层与可恢复边界

## 当前链路与边界

前端和 `POST /api/profile/student/upload?async=1` 均允许 `application/msword` / `.doc`。旧实现把 `.doc` 送到 DOCX 的 Mammoth/python-docx OOXML 路径，真实 OLE2 Word 97 文件返回 `Package not found`。现在 L0 对 `.doc` 单独做 OLE2 签名校验，再由**隔离、限时的 Apache Tika 子进程**提取文本；不调用桌面 Word、不断言 XML/DOCX 能读取二进制格式，也不在 HTTP 上传线程同步解析。

必须由运维预装 Java 17+，并配置 `CPA_L0_DOC_TIKA_JAR` 为已核验、可读的 Tika 独立 JAR 绝对路径。Flask/`cpa.pipeline` worker 重启后配置才会进入当前进程；**只上传文件或只设置变量并不能证明已解析**。未配置、文件格式不符、Tika 失败/超时/空文本/超限均令任务停在失败状态和 0%（若先前没有已提交检查点），不建立候选，不修改已确认资料。错误引导补齐 Java/JAR，或把原文件转存成可选中文本的 DOCX/PDF 再重传。原始上传文件保留于原任务，重新上传创建新任务；不靠旧任务自动重试伪造成功。

Tika `--text` 只给文本投影，不证明页码、图片内文字、嵌入对象或视觉顺序完整。因此成功也始终写 `source_coverage=unverified`、`coverage_reason=legacy_doc_text_only_embedded_media_and_pages_unverified`、`accuracy_verified=false`，并让候选文本块的 `page=null`、`observed_pages=[]`，**不把有正文错报为“已读第 1 页”**。Trace `source_extracted.facts` 展示覆盖范围；候选仍只供人工核对，不自动评分或确认画像。Ask/Agent 附件 ingest 保留同一覆盖标记，文档访问不可报 `complete=true`。需要图片/版面完整审阅时须将原 `.doc` 可靠转成 DOCX/PDF 并重传，按对应 OCR/逐页核对路径处理；转换本身不等于已校验 OCR 准确率。

## 复用与版本

- [Apache Tika 3.3.3 官方下载](https://tika.apache.org/download.html) 与 [CLI 文档](https://tika.apache.org/3.3.3/gettingstarted.html)：本机验证使用 `tika-app-3.3.3.jar`（Apache-2.0，底层微软 Office 二进制解析器来自 Tika/Apache POI）。官方镜像 `https://dlcdn.apache.org/tika/3.3.3/tika-app-3.3.3.jar`，SHA512：`3c6e62e864e32af234832ec6d928bbc58692d3ddd60564cd8c80dee7e161653d6efcba373b6cc9468c778010bea92bdee5ca75ab24f01923480bec1b29b573fd`。JAR 是外置运行依赖，**不入 Git、不请求时下载**；换版本须核验来源、许可证、哈希和回归。
- 本地 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/tool/read.ts` 把 `.doc`/`.docx` 视为二进制输入，不用文本直读；Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 `codex-rs/core/src/tools/` 没有可移植的 Word 解析器。两者只用于明确边界，不引入其 Agent 会话/工具状态机。现有 `app/utils/docx_text.py` 仍仅负责 OOXML DOCX；Tika 是最窄外部解析适配，不另造 OLE 文件格式实现。
- LibreOffice headless 的[官方转换参数](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html) 可作后续高保真 DOCX 转换候选，但本机未安装、此次**未实现**；服务器侧 Word COM 不接入 Celery。

子进程无 shell、无桌面窗口，`-Xmx512m`、90 秒超时、8 MiB 文本上限和 128 KiB 诊断上限；超限或超时主动终止。每次解析有独立临时目录，JVM `java.io.tmpdir` 和 `TEMP/TMP/TMPDIR` 都指向该目录，结束后清理。Tika 错误日志可能包含源文本或本机路径，不能回传到任务、Trace 或浏览器。

## 本机复现与验收

1. 在 D: 放置已核验 JAR 和无隐私的 OLE2 `.doc`。本轮原样使用 [Apache POI 官方 `simple.doc` 测试文件](https://github.com/apache/poi/blob/trunk/test-data/document/simple.doc)，19,456 B、头 `d0 cf 11 e0 a1 b1 1a e1`，只在被 Git 忽略的 `output/doc_probe_simple.doc` 作为本机测试材料；源码/仓库不包含该二进制。修改前 `L0FullParser.parse_file` 回 `success=false, engine=error, Package not found`；修改后真实 Tika 回 `success=true, engine=apache-tika-doc-text`、47 字符、1 个文本块，内容为 `This is a simple file created with Word 97-SR2.`，覆盖仍是 `unverified`。
2. 用权威 `mkg` Python，设置 `TEMP/TMP/TMPDIR` 到 D: `output`，`CPA_L0_DOC_TIKA_JAR` 指向 JAR；运行 `python -B -m pytest -q backend/tests/test_resume_legacy_doc.py`。本机带 `CPA_TEST_REAL_LEGACY_DOC=1`、`CPA_TEST_LEGACY_DOC_SAMPLE=<上述文件>` 时 9 passed，含真实 Tika。普通 CI 未提供 JAR/文件时只跳过真实用例，失败/限额/清理用例仍必须通过。
3. Docker MySQL/Redis 已就绪时，另设置 `CPA_TEST_LIVE_MYSQL=1`、`CPA_TEST_REAL_LEGACY_DOC_HTTP=1` 并运行 `python -B -m pytest -q backend/tests/test_resume_legacy_doc_http_mysql.py`。本机 1 passed：隔离账号经真实 socket HTTP 上传 OLE2、MySQL 意图与真实 Java/Tika 文本抽取，再通过 owner 的候选、状态和 Trace HTTP 回读；`source_extracted` 为 25%、最终任务为 `needs_input`，来源覆盖未验证、`observed_pages=[]`；仅结构/实体模型为替身，不能推断真实模型解析质量。同一测试撤去本测试进程的 JAR 配置再上传，返回失败/0%，无 `source_extracted`、候选 HTTP 为 409、无画像修改。测试精确删除本次账号、任务、队列、上传副本，不触碰用户任务或服务进程。

正式 Flask/worker 带 JAR 路径启动后，还需通过前端代理上传**合成简历内容**并检查 owner 的候选和 Trace；本测试的 Apache POI 示例不是简历，结构模型替身不构成真实简历语义验收。多 Agent 百分比仍是持久检查点，不表示文本来源已完整。
