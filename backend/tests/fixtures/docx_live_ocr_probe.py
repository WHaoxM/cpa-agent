"""Opt-in real sidecar probe; writes only synthetic inputs and count/boolean evidence."""
import argparse
import io
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from docx import Document
    from docx.oxml import parse_xml
    from PIL import Image, ImageDraw, ImageFont
    from app.services.agent_runtime.media.ingest import ingest_file
    from app.pipeline.entity_extractor.l0_full_parser import L0FullParser

    args.output.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_paragraph("BODY_SENTINEL")
    shape = '<w:pict xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:v="urn:schemas-microsoft-com:vml"><v:group><v:shape><v:textbox><w:txbxContent><w:p><w:r><w:t>GROUPED_TEXTBOX_SENTINEL</w:t></w:r></w:p></w:txbxContent></v:textbox></v:shape></v:group></w:pict>'
    doc.add_paragraph().add_run()._r.append(parse_xml(shape))
    image = Image.new("RGB", (1200, 200), "white")
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 48)
    ImageDraw.Draw(image).text((30, 65), "RASTER ONLY EVIDENCE 2468", fill="black", font=font)
    content = io.BytesIO()
    image.save(content, "PNG")
    content.seek(0)
    doc.add_picture(content)
    path = args.output / "synthetic.docx"
    doc.save(path)
    artifacts, meta, ok = ingest_file(str(path), path.name, purpose="chat_attachment")
    text = next(a["body_text"] for a in artifacts if a["kind"] == "full_text")
    l0 = L0FullParser().parse_file(path)
    expected = ["BODY_SENTINEL", "GROUPED_TEXTBOX_SENTINEL", "RASTER ONLY EVIDENCE 2468"]
    result = {"ingest_ok": ok, "ingest_expected_present": [s in text for s in expected],
              "l0_ok": l0.success, "l0_expected_present": [s in l0.text for s in expected],
              "meta": meta, "synthetic_not_real_resume_cer": True}
    (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if ok and l0.success and all(s in text and s in l0.text for s in expected) else 1


if __name__ == "__main__":
    raise SystemExit(main())
