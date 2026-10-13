# DOCX 图片 OCR 与真实简历 CER 审阅闭环

2026-10-10 更新。后端和 OCR Sidecar 保持宿主机 `mkg`，不增加容器 worker。

## 复用边界

修改前核对了本地参考：

- OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`，MIT，`packages/opencode/src/session/prompt.ts`：附件与正文分开表达，不能用正文抽取成功推导二进制附件已理解。
- Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`，Apache-2.0，`codex-rs/protocol/src/models.rs` 的 `ContentItem::InputImage`：图片有独立类型；本项目没有照抄模型协议，把 OCR 作为持久化文件产物接入已有 ingest。
- 实际复用 Mammoth **1.12.0**（BSD-2-Clause）的 OOXML 文本框/分组文本框抽取；python-docx **1.2.0**（MIT）的 OPC relationship、页眉页脚、Paragraph API；PyMuPDF **1.24.14**（AGPL-3.0）的图片转 PDF；现有 `pdf_sidecar.parse_pdf_via_sidecar` 的 MinerU OCR 客户端。
- CER 采用已安装的 RapidFuzz **3.14.3**（MIT）`distance.Levenshtein.distance`，不新增编辑距离算法。独立评测环境需安装该版本；它不是新增的线上必需依赖。

上游依据：[Mammoth](https://github.com/mwilliamson/python-mammoth)、[PyMuPDF Document 转换 API](https://pymupdf.readthedocs.io/en/latest/document.html)。PyMuPDF 是项目已有 AGPL 依赖，不能因为使用薄适配就消除其许可证义务。

## 真实数据流

`media.ingest.extract_text_from_path` 的 DOCX 分支与 `L0FullParser._parse_docx` 共同调用 `utils.docx_text.extract_docx_with_media`：

1. Mammoth 提取正文及文本框，python-docx 补全默认/首页/偶数页页眉页脚及其文本框。
2. 遍历 OPC package 中的图片 parts（不处理 `docProps` 缩略图），逐个通过 MuPDF 转换为临时 PDF，送既有 OCR Sidecar；临时文件由 `TemporaryDirectory` 回收。
3. OCR 文本作为带 source part 标记的补充保存进同一全文，`docx_media` 记录每个 part 的引擎、状态、字数。不伪造 DOCX 页码或视觉阅读顺序。
4. 失败、无文字响应、OCR 禁用、嵌入 OLE/图表明确记录 `source_coverage=unverified`、`coverage_issues`、`retryable=true`。已有 FileStore 的 partial/upgrade 路径消费该标记。`processed` 只表示已处理，没有逐字准确率保证，`accuracy_verified=false`。

这不是 Word 版面渲染器。WMF/EMF 是否可转取决于 MuPDF；不支持时保持 partial。没有 OCR 文字的照片也保守保留未核验状态，不能把没有返回文本假称“确认无文字”。浮动元素与正文的视觉穿插顺序、图表语义、任意 SmartArt 尚未覆盖，不称 DOCX 全格式清零。

## 实际 Sidecar 验收

```powershell
$env:PYTHONUTF8='1'
$env:CPA_L0_PARSE_SIDECAR_URL='http://127.0.0.1:9380'
& D:/Miniconda/miniconda3/envs/mkg/python.exe -B backend/tests/fixtures/docx_live_ocr_probe.py --output backend/uploads/acceptance/docx-20261010
```

实际 `result.json`：Agent ingest 与 L0 均成功，正文 / grouped VML textbox / 栅格文字三个断言分别全部 `true`；图片 part 的 `engine=mineru`，OCR 返回 25 字。此探针使用合成 DOCX，**不是**真实简历 CER，也不是 HTTP/Celery 进程重启验收。生产宿主机进程需重启后再验证上传链。

## 真实简历人工 CER

```powershell
& D:/Miniconda/miniconda3/envs/mkg/python.exe -B backend/tests/fixtures/resume_cer_review.py prepare --source backend/uploads/agent_files/file_b18893f2001540539287570fbf16a199.pdf --output backend/uploads/acceptance/cer-review-20261010 --ocr
```

实际完成两页渲染、两页独立请求 OCR。审阅包保存在忽略的 uploads 下，包含 `images/page_NNN.png`、`ocr/page_NNN.txt`、**空白** `human/page_NNN.txt` 与 `manifest.json`，正文不进入仓库或控制台。若目录已存在，prepare 拒绝覆盖，避免破坏人工标注。

人工从原图独立转录到 human 文本，不以 OCR 修订稿替代独立转录。另一名复核者检查行序、标点、空白和易混字符后，在 manifest 填入不同的 `annotator`、`reviewer`，每页 `reviewed=true` 和最终文本 SHA-256 `reference_sha256`。这是一项显式人工声明，不是身份认证系统。

```powershell
& D:/Miniconda/miniconda3/envs/mkg/python.exe -B backend/tests/fixtures/resume_cer_review.py score --source backend/uploads/agent_files/file_b18893f2001540539287570fbf16a199.pdf --output backend/uploads/acceptance/cer-review-20261010
```

score 校验原 PDF SHA、页集合、图像/OCR/人工文本 SHA、复核状态，使用严格 CER 和 NFC 去空白 CER，并按参考字符数加权。任何未复核、文件改动、缺页/多页、空参考都不评分。当前真实输出：`not_accepted / independent_human_review_missing / cer=null`。**仍须真实人工标注才能交付真实简历逐字准确率，不能由模型填写两个人名制造通过。**

`test_resume_cer_review.py` 验证缺复核拒绝、已知编辑距离与标注后篡改拒绝；这些是合成合同测试，不是人工质量证明。
