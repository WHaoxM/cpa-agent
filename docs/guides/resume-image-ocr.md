# 图片简历 OCR：上传到 L0 候选的真实边界

## 当前路径

`POST /api/profile/student/upload?async=1` 接受 PNG/JPG/JPEG/BMP/WebP/TIFF，并先把 owner-bound 原文件、MySQL 任务与派发意图持久化。`DocumentAgent` 再由 `L0FullParser` 解析；只有提取到非空正文和块，才提交 `source_extracted=25%`，后续结构、实体、证据 Agent 才能接力。候选只到 `needs_input`，**不会自动覆盖已确认学生资料**。失败任务为 `failed`、保留 0% 和 `failed` Trace，`GET /api/pipeline/status/<task_id>` 提供错误；修好 OCR 后应重新上传，不把同一失败任务伪装成已完成。

图片首先走已配置且 `/v1/health` 实际宣称 `ocr_pdf=true` 的 PDF OCR Sidecar：

1. PNG/JPG/JPEG/BMP/TIFF 用 PyMuPDF 1.24.14 转成临时 PDF；本机该版本不能打开 WebP，因此 WebP 用 Pillow 解码，保留所有帧后生成多页 PDF。临时 PDF 放在原上传同卷的隔离目录，成功或失败都删除；原上传仍保留供任务审计/重试。
2. 用现有 `parse_pdf_via_sidecar` 契约做真正 OCR，保留块页码并把原图片格式、页数写入 L0 元数据。**不能**把转换 PDF 后的 PyMuPDF 文本层（通常为空）当成 OCR 成功。
3. Sidecar 未就绪时，单帧图可回退已安装的 YOLO 版面检测 + Tesseract `chi_sim+eng`；YOLO 只定位区域，不识字。Tesseract 可执行程序或语言包缺失、OCR 为空、图片无法解码，均明确失败。多页 TIFF/动画 WebP 若没有可用 Sidecar，不能只识别首帧并声称已完成。

解码前按 Pillow 元数据拒绝超过 32 页/帧或累计 4,000 万像素的图像，避免小压缩文件触发无界 OCR 内存开销；返回明确的压缩/拆分重试提示，不截取前 32 页假装全文。

本机 `mkg` 环境有 `pytesseract` Python 包，但实测 `tesseract.exe` 不在 PATH；在未启动 Sidecar 的状态，PNG/JPG 的 OCR 失败是**真实能力缺口**，不是前端进度问题。此前上传白名单虽接受 WebP/TIFF，`parse_file` 却返回“不支持格式”；YOLO 导入用了不存在的顶层 `layout` 包，并把 PIL 对象传给要求 `numpy.ndarray` 的检测器。两处均已按当前包结构修正；缺 Tesseract 时还会在加载 265 MB YOLO 模型**之前**返回可重试错误，YOLO 区域本身不算 OCR 成功。单元测试先以合成图片复现旧分支（7 个断言失败），再确认四种格式、多帧覆盖、临时文件清理、无 OCR 不伪成功。真实 socket HTTP + MySQL/Redis 账号测试验证上传→转换适配→任务→候选/失败→Trace；日常回归中 OCR 返回文本和结构模型为**替身**，不证明真实 OCR 准确率。

## 开源/现有能力复用记录

- OpenCode 本机快照 `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/image/image.ts`、`session/message-v2.ts` 显式解码/规范化图片并区分 provider 可接收的格式；Codex 快照 `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 `codex-rs/core/src/image_preparation.rs` 把图片准备失败视为显式状态。它们处理**模型图片输入**，不是简历逐页 OCR，不能直接移植其会话/图片状态机到 Flask。
- 本项目已有 `backend/app/utils/docx_text.py::_ocr_embedded_image` 的“Pillow/PyMuPDF 图片→PDF→真实 MinerU Sidecar”同类薄适配，以及 `backend/app/pipeline/entity_extractor/pdf_sidecar.py` 的 OCR 引擎来源/空结果校验；本改动复用同一服务契约，不引入第二套 OCR 队列、模型或通用多模态运行时。MinerU 3.0.7 与模型/许可边界见 `docs/architecture/agent-upstream-adaptation.md`。

## 本机真实 OCR 验收

在独立终端/隐藏进程启动真实 Sidecar 前，先确认 `D:` 有足够空间、可用内存与模型完整，强制把 `TEMP/TMP/TMPDIR` 指到 `D:/AGeneral Workspace/cpa-agent/output`，`MODELSCOPE_CACHE` 指到 `D:/AGeneral Workspace/cpa-agent/models/mineru`。运行 `D:/Miniconda/miniconda3/envs/mkg/python.exe -B backend/scripts/parse_sidecar_mineru.py`；先看 `http://127.0.0.1:9380/v1/health` 是否 `status=ready,ocr_pdf=true`。Flask 和 `cpa.pipeline` worker 均须以 `CPA_L0_PARSE_SIDECAR_URL=http://127.0.0.1:9380` 启动新进程；只启动 Sidecar 而未重启 worker，不会改变已缓存的 disabled 能力状态。不要修改账号密钥 `.env`，也不要在用户现有任务上试跑。

用隔离测试账号上传一张无隐私中文/英文合成图，轮询 owner 的 `/api/pipeline/trace/<task_id>` 与 `/api/pipeline/status/<task_id>`：真实 Sidecar 来源、`source_extracted` 25%、最终候选 `needs_input`、源页数和块应与事实一致；另一个账号必须 404。若模型、OCR 或队列故障，保留失败与重试路径，不声称图片解析能力已验证。

2026-10-14 本机实测：D: 临时目录与模型缓存、独立 Sidecar PID `95476`，`/v1/health` 回 `ready,ocr_pdf=true`。`CPA_TEST_REAL_IMAGE_OCR=1` 的合成中文/英文 PNG 经真实 MinerU OCR 的 L0 直接测试通过（1 页、非空块，含 Python；约 26.8 秒）。同开关下隔离账号的**真实 socket HTTP 上传 + MySQL 任务 + L0 DocumentAgent + 真 Sidecar OCR + 候选/Trace**通过（约 8.5 秒）；另一个账号读取该 Trace 为 404。结构/实体模型仍为测试替身，故只证明 OCR→业务交接，不证明真实模型结构质量。第二次隔离上传在强制无 OCR 能力时为 `failed,0%`，无候选，也未改已确认资料。Sidecar 日志位于忽略的 `output/sidecar_image_probe.stdout.log` / `output/sidecar_image_probe.stderr.log`，该进程尚未代替 Flask/worker 配置；正式运行面需带 URL 重启后另验。
