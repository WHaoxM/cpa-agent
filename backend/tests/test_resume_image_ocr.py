"""L0 raster resume formats must reach genuine OCR, not a text-only fallback."""

from pathlib import Path
from types import SimpleNamespace
import os

import pytest
from PIL import Image, ImageDraw

from app.pipeline.entity_extractor.l0_full_parser import L0FullParser, L0Result


def _synthetic_resume(path: Path) -> None:
    image = Image.new("RGB", (600, 220), "white")
    ImageDraw.Draw(image).text((35, 70), "EDUCATION PROJECT PYTHON", fill="black")
    image.save(path)


def _synthetic_multipage_resume(path: Path) -> None:
    first = Image.new("RGB", (600, 220), "white")
    second = Image.new("RGB", (600, 220), "white")
    ImageDraw.Draw(first).text((35, 70), "EDUCATION", fill="black")
    ImageDraw.Draw(second).text((35, 70), "PROJECT PYTHON", fill="black")
    first.save(path, save_all=True, append_images=[second], duration=100, loop=0)


@pytest.mark.parametrize("extension", ["png", "jpg", "webp", "tiff", "tif"])
def test_declared_raster_upload_formats_reach_image_parser(tmp_path, monkeypatch, extension):
    path = tmp_path / f"fictional-resume.{extension}"
    _synthetic_resume(path)
    parser = L0FullParser()
    seen = []
    monkeypatch.setattr(parser, "_parse_image", lambda name: (
        seen.append(name) or L0Result(True, "EDUCATION", [], {}, "test-ocr")))

    result = parser.parse_file(path)

    assert result.success is True
    assert seen == [str(path)]


@pytest.mark.parametrize("extension", ["png", "jpg", "webp", "tiff", "tif"])
def test_raster_to_pdf_sidecar_uses_real_ocr_result_and_cleans_derived_pdf(
    tmp_path, monkeypatch, extension,
):
    from app.pipeline.entity_extractor import l0_full_parser, pdf_sidecar
    from app.pipeline.entity_extractor.pdf_engines import EngineState, EngineStatus

    path = tmp_path / f"fictional-resume.{extension}"
    _synthetic_resume(path)
    monkeypatch.setattr(l0_full_parser, "get_sidecar_state", lambda: EngineState(
        "sidecar", EngineStatus.READY, "synthetic OCR service"))
    captured = []

    def fake_ocr(pdf_path):
        import pymupdf

        with pymupdf.open(pdf_path) as pdf:
            assert len(pdf) == 1
            assert pdf[0].get_images()
        captured.append(pdf_path)
        return {"success": True, "text": "EDUCATION PROJECT PYTHON",
                "blocks": [{"text": "EDUCATION PROJECT PYTHON", "page": 0}],
                "engine": "mineru", "metadata": {"pages": 1}}

    monkeypatch.setattr(pdf_sidecar, "parse_pdf_via_sidecar", fake_ocr)
    result = L0FullParser().parse_file(path)

    assert result.success is True
    assert result.text == "EDUCATION PROJECT PYTHON"
    assert result.blocks[0].page == 0
    assert result.engine == "sidecar"
    assert result.metadata["source_format"] == extension
    assert captured and not Path(captured[0]).exists()


def test_tiff_alias_uses_same_ocr_path_for_identical_bytes(tmp_path, monkeypatch):
    from app.pipeline.entity_extractor import l0_full_parser, pdf_sidecar
    from app.pipeline.entity_extractor.pdf_engines import EngineState, EngineStatus

    original = tmp_path / "fictional-resume.tiff"
    _synthetic_resume(original)
    alias = tmp_path / "fictional-resume.tif"
    alias.write_bytes(original.read_bytes())
    assert alias.read_bytes() == original.read_bytes()
    monkeypatch.setattr(l0_full_parser, "get_sidecar_state", lambda: EngineState(
        "sidecar", EngineStatus.READY, "synthetic OCR service"))
    calls = []

    def fake_ocr(pdf_path):
        import pymupdf

        with pymupdf.open(pdf_path) as pdf:
            assert len(pdf) == 1 and pdf[0].get_images()
        calls.append(pdf_path)
        return {"success": True, "text": "EDUCATION PROJECT PYTHON",
                "blocks": [{"text": "EDUCATION PROJECT PYTHON", "page": 0}],
                "engine": "mineru", "metadata": {"pages": 1}}

    monkeypatch.setattr(pdf_sidecar, "parse_pdf_via_sidecar", fake_ocr)
    results = [L0FullParser().parse_file(path) for path in (original, alias)]

    assert [result.success for result in results] == [True, True]
    assert [result.engine for result in results] == ["sidecar", "sidecar"]
    assert [result.text for result in results] == ["EDUCATION PROJECT PYTHON"] * 2
    assert [result.metadata["source_format"] for result in results] == ["tiff", "tif"]
    assert len(calls) == 2 and all(not Path(path).exists() for path in calls)


def test_missing_raster_ocr_is_explicit_failure_not_empty_success(tmp_path, monkeypatch):
    from app.pipeline.entity_extractor import l0_full_parser
    from app.pipeline.entity_extractor.pdf_engines import EngineState, EngineStatus
    import pytesseract

    path = tmp_path / "fictional-resume.png"
    _synthetic_resume(path)
    monkeypatch.setattr(l0_full_parser, "get_sidecar_state", lambda: EngineState(
        "sidecar", EngineStatus.DISABLED, "not configured"))
    monkeypatch.setattr(L0FullParser, "_check_yolo", lambda self: (
        (_ for _ in ()).throw(AssertionError("YOLO must not load without OCR"))))
    monkeypatch.setattr(pytesseract, "image_to_string", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(pytesseract.TesseractNotFoundError())))

    result = L0FullParser().parse_file(path)

    assert result.success is False
    assert result.text == ""
    assert "OCR" in result.error
    assert "Tesseract" in result.error


def test_yolo_fallback_uses_packaged_detector_and_ndarray(tmp_path, monkeypatch):
    import numpy as np
    from app.pipeline.entity_extractor.layout import yolo_layout_detector

    path = tmp_path / "fictional-resume.png"
    _synthetic_resume(path)

    class Detector:
        def __init__(self, _model_path):
            pass

        def detect(self, pixels):
            assert isinstance(pixels, np.ndarray)
            assert pixels.shape == (220, 600, 3)
            return [SimpleNamespace(bbox=(0, 0, 600, 220))]

    monkeypatch.setattr(yolo_layout_detector, "YOLOLayoutDetector", Detector)
    parser = L0FullParser()
    monkeypatch.setattr(parser, "_ocr_image", lambda _image: "EDUCATION PROJECT PYTHON")

    result = parser._parse_image_yolo_ocr(str(path))

    assert result.success is True
    assert result.text == "EDUCATION PROJECT PYTHON"


@pytest.mark.parametrize("size,frames", [((9000, 9000), 1), ((100, 100), 33)])
def test_oversized_raster_rejected_before_ocr_memory_allocation(monkeypatch, size, frames):
    class FakeImage:
        width, height = size
        n_frames = frames

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(Image, "open", lambda _path: FakeImage())
    from app.pipeline.entity_extractor import l0_full_parser

    monkeypatch.setattr(l0_full_parser, "get_sidecar_state", lambda: (
        (_ for _ in ()).throw(AssertionError("OCR must not start"))))

    result = L0FullParser()._parse_image("synthetic.png")

    assert result.success is False
    assert result.text == ""
    assert "OCR 上限" in result.error


@pytest.mark.parametrize("extension", ["webp", "tiff", "tif"])
def test_multipage_image_sidecar_does_not_drop_later_frame(tmp_path, monkeypatch, extension):
    from app.pipeline.entity_extractor import l0_full_parser, pdf_sidecar
    from app.pipeline.entity_extractor.pdf_engines import EngineState, EngineStatus

    path = tmp_path / f"fictional-multipage.{extension}"
    _synthetic_multipage_resume(path)
    monkeypatch.setattr(l0_full_parser, "get_sidecar_state", lambda: EngineState(
        "sidecar", EngineStatus.READY, "synthetic OCR service"))

    def fake_ocr(pdf_path):
        import pymupdf

        with pymupdf.open(pdf_path) as pdf:
            assert len(pdf) == 2
            assert all(page.get_images() for page in pdf)
        return {"success": True, "text": "EDUCATION\nPROJECT PYTHON",
                "blocks": [{"text": "EDUCATION", "page": 0},
                           {"text": "PROJECT PYTHON", "page": 1}],
                "engine": "mineru", "metadata": {"pages": 2}}

    monkeypatch.setattr(pdf_sidecar, "parse_pdf_via_sidecar", fake_ocr)
    result = L0FullParser().parse_file(path)

    assert result.success is True
    assert result.metadata["pages"] == 2
    assert [block.page for block in result.blocks] == [0, 1]


@pytest.mark.parametrize("extension", ["webp", "tiff", "tif"])
def test_multipage_image_without_sidecar_cannot_claim_first_frame_as_complete(
    tmp_path, monkeypatch, extension,
):
    from app.pipeline.entity_extractor import l0_full_parser
    from app.pipeline.entity_extractor.pdf_engines import EngineState, EngineStatus
    import pytesseract

    path = tmp_path / f"fictional-multipage.{extension}"
    _synthetic_multipage_resume(path)
    monkeypatch.setattr(l0_full_parser, "get_sidecar_state", lambda: EngineState(
        "sidecar", EngineStatus.DISABLED, "not configured"))
    monkeypatch.setattr(pytesseract, "image_to_string", lambda *_args, **_kwargs: "EDUCATION")

    result = L0FullParser().parse_file(path)

    assert result.success is False
    assert result.metadata["page_count"] == 2
    assert result.text == ""
    assert "Sidecar" in result.error


@pytest.mark.skipif(os.getenv("CPA_TEST_REAL_IMAGE_OCR") != "1", reason="opt-in real MinerU Sidecar")
@pytest.mark.parametrize("extension", ["png", "tiff", "tif"])
def test_real_sidecar_reads_synthetic_chinese_english_image(tmp_path, monkeypatch, extension):
    from PIL import ImageFont
    from app.pipeline.entity_extractor.pdf_engines import reset_engine_states

    image = Image.new("RGB", (1100, 1500), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 46)
    for index, line in enumerate((
        "张同学 合成测试简历", "教育经历 测试大学 计算机科学", "项目经历 数据分析系统",
        "Python SQL Vue Docker", "2024 2025 Internship Project",
    )):
        draw.text((65, 100 + index * 160), line, fill="black", font=font)
    path = tmp_path / f"synthetic-no-personal-data.{extension}"
    image.save(path)
    monkeypatch.setenv("CPA_L0_PARSE_SIDECAR_URL", "http://127.0.0.1:9380")
    reset_engine_states()
    try:
        result = L0FullParser().parse_file(path)
        assert result.success is True, result.error
        assert result.engine == "sidecar"
        assert result.metadata["pages"] == 1
        assert result.blocks
        assert "Python" in result.text or "python" in result.text.lower()
    finally:
        reset_engine_states()
