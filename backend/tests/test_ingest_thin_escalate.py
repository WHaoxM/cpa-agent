"""Thin light PDF extract must escalate — education often sits at the end."""

from app.services.agent_runtime.media.ingest import light_extract_too_thin


def test_head_only_skills_is_thin():
    text = (
        "钟梓珉\n2006/11\n13330772777\nhosheil@outlook.com\n"
        "https://neutronm.store\nhttps://github.com/whaoxm\n"
        "专业技能\n· 熟悉 RAG/GraphRAG 与 Agent 开发\n· Prompt/Context\n"
    )
    assert light_extract_too_thin(text, ext="pdf") is True


def test_full_resume_with_education_not_thin():
    text = (
        "钟梓珉\n专业技能\nRAG Agent\n"
        + ("项目细节。" * 80)
        + "\n教育经历\n四川轻化工大学\n计算机科学与技术\n"
        + ("奖项。" * 20)
    )
    assert "教育经历" in text
    assert light_extract_too_thin(text, ext="pdf") is False


def test_short_pdf_always_thin():
    assert light_extract_too_thin("hello skills github.com", ext="pdf") is True


def test_docx_short_not_forced_thin():
    # Non-PDF short text is not subject to the PDF thin heuristic
    assert light_extract_too_thin("短文本", ext="docx") is False
