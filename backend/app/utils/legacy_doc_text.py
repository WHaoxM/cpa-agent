"""Bounded Apache Tika adapter for legacy OLE2 Word documents.

The Tika application is an operator-provided, pinned artifact.  Never download
or start desktop Office from a request/worker, and never claim that Tika's text
view includes embedded images or reliable Word page provenance.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time


OLE2_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 128 * 1024
TIMEOUT_SECONDS = 90


class LegacyDocError(RuntimeError):
    """A recoverable extraction failure to surface to the upload task."""


def extract_legacy_doc_text(path: str) -> tuple[str, dict]:
    source = Path(path)
    with source.open("rb") as stream:
        if stream.read(8) != OLE2_MAGIC:
            raise LegacyDocError("文件不是旧版二进制 .doc；请转存为 DOCX 或 PDF 后重传")

    jar_setting = os.environ.get("CPA_L0_DOC_TIKA_JAR", "").strip()
    jar = Path(jar_setting) if jar_setting else None
    if not jar or not jar.is_file():
        raise LegacyDocError(
            "旧版 .doc 解析组件未配置或不存在；请配置 CPA_L0_DOC_TIKA_JAR，"
            "或转存为 DOCX/PDF 后重传"
        )
    java = shutil.which("java")
    if not java:
        raise LegacyDocError("旧版 .doc 解析需要 Java；请安装 Java 17+ 或转存为 DOCX/PDF 后重传")

    # The host chooses TEMP/TMP/TMPDIR. Each run gets a private Java profile
    # and output file; no document body is put in logs or command arguments.
    with TemporaryDirectory(prefix="cpa-legacy-doc-") as temp_dir:
        output = Path(temp_dir) / "extracted.txt"
        diagnostic = Path(temp_dir) / "diagnostic.log"
        env = os.environ.copy()
        env.update(TEMP=temp_dir, TMP=temp_dir, TMPDIR=temp_dir)
        command = [
            java, "-Xmx512m", f"-Djava.io.tmpdir={temp_dir}",
            "-jar", str(jar), "--text", str(source),
        ]
        try:
            with output.open("wb") as stdout, diagnostic.open("wb") as stderr:
                process = subprocess.Popen(
                    command, stdout=stdout, stderr=stderr, env=env,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                deadline = time.monotonic() + TIMEOUT_SECONDS
                while process.poll() is None:
                    if output.stat().st_size > MAX_TEXT_BYTES or diagnostic.stat().st_size > MAX_DIAGNOSTIC_BYTES:
                        process.kill()
                        process.wait()
                        raise LegacyDocError("旧版 .doc 解析输出超出安全上限；请拆分或转存后重传")
                    if time.monotonic() > deadline:
                        process.kill()
                        process.wait()
                        raise LegacyDocError("旧版 .doc 解析超时；请转存为 DOCX/PDF 后重传")
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        pass
        except OSError as exc:
            raise LegacyDocError("旧版 .doc 解析器启动失败；请检查 Java/Tika 配置后重试") from exc
        if output.stat().st_size > MAX_TEXT_BYTES or diagnostic.stat().st_size > MAX_DIAGNOSTIC_BYTES:
            raise LegacyDocError("旧版 .doc 解析输出超出安全上限；请拆分或转存后重传")
        if process.returncode != 0:
            # Tika stderr can contain user text and internal paths; do not leak
            # it into a task error or frontend trace.
            raise LegacyDocError(
                f"旧版 .doc 解析器失败（退出码 {process.returncode}）；"
                "请转存为 DOCX/PDF 后重传"
            )
        try:
            text = output.read_text(encoding="utf-8-sig").strip()
        except UnicodeDecodeError as exc:
            raise LegacyDocError("旧版 .doc 解析器返回非 UTF-8 文本；请转存为 DOCX/PDF 后重传") from exc
        if not text:
            raise LegacyDocError("旧版 .doc 未提取到正文；可能是扫描或加密文件，请转存为可选中文本的 DOCX/PDF 后重传")
        return text, {
            "engine": "apache-tika-doc-text",
            "source_coverage": "unverified",
            "coverage_reason": "legacy_doc_text_only_embedded_media_and_pages_unverified",
            "accuracy_verified": False,
            "retryable": True,
            "reading_order": "tika_text_projection",
        }
