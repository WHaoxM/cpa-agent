"""NS-LE entities → Portrait student_profile adapter (no re-extract / no regex)."""

from app.pipeline.entity_extractor.nsle_pipeline import entities_to_student_profile
from app.pipeline.entity_extractor.resume_structure import (
    validate_structure_truth,
    _normalize_profile,
)


def test_map_llm_entities_to_portrait_profile():
    entities = {
        "basic_info": {
            "name": "钟梓珉",
            "phone": "13330772777",
            "email": "a@b.com",
            "school": "四川轻化工大学",
            "major": "计算机科学与技术",
        },
        "education": [
            {
                "school": "四川轻化工大学",
                "major": "计算机科学与技术",
                "degree": "本科",
                "gpa": 3.92,
                "rank_percent": 1,
            }
        ],
        "experience": [
            {
                "name": "职导星图",
                "position": "核心开发者",
                "type": "project",
                "description": "LangGraph Agent + GraphRAG",
                "is_team": True,
            },
            {
                "company": "实验室",
                "position": "科研成员",
                "type": "internship",
                "internship_months": 3,
                "description": "实习",
            },
        ],
        "awards": [{"name": "全球校园AI算法精英大赛", "level": "国家级一等"}],
        "certificates": [{"name": "CET-6"}],
        "research": [{"name": "具身智能体", "level": "省级"}],
        "leadership_roles": ["人工智能协会学科竞赛部副部长"],
    }
    p = entities_to_student_profile(entities)
    assert p["name"] == "钟梓珉"
    assert p["school"] == "四川轻化工大学"
    assert p["gpa"] == 3.92
    assert p["rank_percent"] == 1.0
    assert p["internship_months"] == 3
    assert any(c["level"] == "国家级一等" for c in p["competitions"])
    assert any("职导星图" in x["name"] for x in p["projects"])
    assert p["research"][0]["name"] == "具身智能体"
    assert p["source"] == "nsle_llm"


def test_no_invented_gpa_when_llm_omits():
    p = entities_to_student_profile(
        {"education": [{"school": "某大学", "major": "CS", "degree": "本科"}]}
    )
    assert "gpa" not in p


def test_adapter_does_not_parse_duration_string():
    """Duration strings are not regex-parsed; months must be numeric from LLM."""
    p = entities_to_student_profile(
        {
            "experience": [
                {
                    "company": "某司",
                    "type": "internship",
                    "duration": "3个月",
                    "description": "实习",
                }
            ]
        }
    )
    assert p.get("internship_months", 0) == 0


def test_validate_structure_truth_field_presence_only():
    ok = validate_structure_truth(
        {
            "school": "四川轻化工大学",
            "major": "计算机科学与技术",
            "competitions": [{"name": "大赛", "level": "国家级"}],
        }
    )
    assert ok["ok"] is True

    bad = validate_structure_truth({"school": "", "major": "", "projects": []})
    assert bad["ok"] is False
    assert "missing_school" in bad["issues"]
    assert "missing_major" in bad["issues"]


def test_normalize_profile_from_canonical_llm():
    p = _normalize_profile(
        {
            "name": "钟梓珉",
            "school": "四川轻化工大学",
            "major": "计算机科学与技术",
            "internship_months": "6",
            "competitions": [{"name": "AI大赛", "level": "国家级一等"}],
            "skills": ["Python", {"name": "LangGraph"}],
        }
    )
    assert p["internship_months"] == 6
    assert p["skills"] == ["Python", "LangGraph"]
    assert p["source"] == "canonical_llm"
