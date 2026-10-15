"""
test_portrait_generator.py — D2.1 Portrait 纯函数 + 入口
- exact case: 满学历满技能、零技能、混合档位
- 不变量: 0≤dX≤100、档位↔分数一致、completeness 0-100
- 纯函数: _calc_skill_coverage, _score_school, _score_major, _classify_school
"""
import pytest
from app.pipeline.entity_extractor.portrait_generator import PortraitGenerator
from config.loader import competitiveness_level


@pytest.fixture
def gen() -> PortraitGenerator:
    return PortraitGenerator()


# ─────────────────────────────────────────────────────────
# 纯函数级别
# ─────────────────────────────────────────────────────────

class TestExplicitCategories:
    @pytest.mark.parametrize("name", ["清华大学", "985大学", "北京邮电大学", "某普通一本大学", ""])
    def test_school_names_are_not_categories(self, gen, name):
        assert gen._score_school(name) is None
        assert gen._classify_school(name) is None

    def test_explicit_category_tables_preserved(self, gen):
        assert gen._score_school("985") == 15
        assert gen._score_school("211") == 12
        assert gen._score_major("文科") == 5
        assert gen._score_major("其他理工") == 10

    @pytest.mark.parametrize("major", ["机械工程", "汉语言文学", "未知专业", ""])
    def test_major_is_not_guessed(self, gen, major):
        assert gen._score_major(major) is None


class TestSkillCoverage:
    """冻结公式: skill_coverage = (exact + semantic×0.8) / required"""

    def test_full_exact_match(self, gen):
        """全部 exact 匹配 → 1.0"""
        student = [
            {"name": "Python", "match_type": "exact"},
            {"name": "SQL", "match_type": "exact"},
        ]
        job = [
            {"name": "Python", "level": "必须"},
            {"name": "SQL", "level": "必须"},
        ]
        assert gen._calc_skill_coverage(student, job) == 1.0

    def test_partial_semantic(self, gen):
        """部分 exact + 部分 semantic → 期望 (1 + 0.8) / 2 = 0.9"""
        student = [
            {"name": "Python", "match_type": "exact"},
            {"name": "Pandas", "match_type": "semantic"},
        ]
        job = [
            {"name": "Python", "level": "必须"},
            {"name": "Pandas", "level": "必须"},
        ]
        coverage = gen._calc_skill_coverage(student, job)
        assert abs(coverage - 0.9) < 1e-6, f"expected 0.9, got {coverage}"

    def test_zero_coverage(self, gen):
        """0 匹配 → 0.0"""
        student = [{"name": "Java", "match_type": "exact"}]
        job = [
            {"name": "Python", "level": "必须"},
            {"name": "Go", "level": "必须"},
        ]
        assert gen._calc_skill_coverage(student, job) == 0.0

    def test_no_required_skills(self, gen):
        """无 required → 0.0 (避免除零)"""
        assert gen._calc_skill_coverage([], [{"name": "Python"}]) == 0.0
        assert gen._calc_skill_coverage([{"name": "Python"}], []) == 0.0

    def test_required_filter(self, gen):
        """level=优选 不计入 required, 全部 必须 才算"""
        student = [{"name": "Docker", "match_type": "exact"}]
        job = [
            {"name": "Python", "level": "必须"},
            {"name": "Docker", "level": "优选"},  # 不算 required
        ]
        # required_count=1 (Python), student 无 Python → 0
        assert gen._calc_skill_coverage(student, job) == 0.0


# ─────────────────────────────────────────────────────────
# 公开入口 + exact case
# ─────────────────────────────────────────────────────────

def _full_student_profile():
    """满学历满技能的理想画像"""
    return {
        "name": "张三",
        "degree": "硕士",
        "school": "清华大学",
        "major": "计算机科学与技术",
        "certificates": [{"name": "PMP"}, {"name": "CPA"}],
        "languages": ["英语六级", "日语N2"],
        "projects": [
            {"name": "电商推荐", "is_team": True, "role": "leader"},
            {"name": "搜索排序", "is_team": True, "role": "成员"},
        ],
        "internship_months": 6,
        "experience": [
            {"company": "字节跳动", "title": "算法实习生", "duration_months": 6},
        ],
        "soft_skills": {
            "communication": 4, "teamwork": 4, "stress_tolerance": 4, "responsibility": 4,
        },
        "student_cadre": True,
        "competitions": [
            {"name": "ACM", "level": "国家级一等"},
        ],
        "researches": [
            {"name": "CCF-A paper", "level": "CCF-A"},
        ],
    }


def _full_skills():
    return [
        {"name": "Python", "level": "精通", "category": "programming_languages", "confidence": 0.95, "match_type": "exact"},
        {"name": "SQL", "level": "掌握", "category": "databases", "confidence": 0.85, "match_type": "exact"},
        {"name": "FastAPI", "level": "熟悉", "category": "web_frameworks", "confidence": 0.7, "match_type": "semantic"},
    ]


class TestGenerateStudentPortraitExact:
    def test_top_student_极强(self, gen):
        """满学历+满技能学生: competitiveness_level='极强'"""
        result = gen.generate_student_portrait(
            student_id="stu_001",
            skills=_full_skills(),
            student_profile=_full_student_profile(),
        )
        s = result["summary"]
        assert result["status"] == "partial"
        assert s["competitiveness_score"] is None
        assert s["competitiveness_level"] == "unknown"

    def test_empty_skills(self, gen):
        """简历没提技能不是技能能力为零；D2 与总分都待核对。"""
        result = gen.generate_student_portrait(
            student_id="stu_empty",
            skills=[],
            student_profile={"degree": "本科", "school": "某一本", "major": "软件工程"},
        )
        s = result["summary"]
        d2 = result["D2_skill"]
        assert d2["dimension_score"] is None
        assert d2["missing_fields"] == ["skills"]
        assert "skills" in result["missing_fields"]
        assert result["D4_potential"]["dimension_score"] is None
        assert "growth_evidence" in result["missing_fields"]
        # 但 competitiveness 不应崩溃
        assert s["competitiveness_score"] is None
        assert s["competitiveness_level"] == "unknown"

    def test_project_without_skills_keeps_project_evidence_but_not_d2_score(self, gen):
        result = gen.generate_student_portrait(
            student_id="stu_project_no_skills", skills=[],
            student_profile={"degree": "本科", "school": "示例大学", "major": "计算机",
                             "projects": [{"name": "课程项目"}]},
        )
        d2 = result["D2_skill"]
        assert d2["project_count"] == 1
        assert d2["evidence"] == ["1 个项目经验"]
        assert d2["dimension_score"] is None
        assert result["summary"]["competitiveness_score"] is None

    def test_duplicate_skill_mentions_do_not_raise_d2_score(self, gen):
        profile = {"projects": [{"name": "示例项目"}]}
        once = gen._calc_d2_skill([{"name": "React"}], profile)
        repeated = gen._calc_d2_skill([{"name": "React"}, {"name": " React "}], profile)
        assert repeated["skill_count"] == once["skill_count"] == 1
        assert repeated["skill_list"] == once["skill_list"]
        assert repeated["sub_skill"] == once["sub_skill"]
        assert repeated["dimension_score"] == once["dimension_score"]
        assert repeated["evidence"][0] == "简历提及 1 个不同技术词"
        assert gen._calc_d2_skill([{"name": "React"}, {"name": "ReAct"}], profile)["skill_count"] == 2

    def test_growth_evidence_distinguishes_unknown_from_scored_zero(self, gen):
        without_evidence = gen.generate_student_portrait(
            student_id="stu_unknown_growth", skills=[],
            student_profile={"degree": "本科", "school": "示例大学", "major": "计算机"},
        )
        assert without_evidence["D4_potential"]["dimension_score"] is None
        assert without_evidence["D4_potential"]["evidence"] == []
        assert without_evidence["D4_potential"]["missing_fields"] == ["growth_evidence"]
        assert "growth_evidence" in without_evidence["missing_fields"]

        with_evidence = gen.generate_student_portrait(
            student_id="stu_known_growth", skills=[],
            student_profile={"degree": "本科", "school": "示例大学", "major": "计算机",
                             "competitions": [{"name": "程序设计竞赛", "level": "其他"}]},
        )
        assert with_evidence["D4_potential"]["dimension_score"] is not None
        assert with_evidence["D4_potential"]["evidence"] == ["竞赛: 1 项"]
        assert "growth_evidence" not in with_evidence["missing_fields"]

    def test_mid_student_中等_or_偏弱(self, gen):
        """混合档位: 普通学生 (普通本科+一般技能)"""
        profile = {
            "degree": "本科", "school": "某二本", "major": "电子信息",
            "projects": [], "internship_months": 0,
        }
        skills = [
            {"name": "Python", "level": "熟悉", "category": "programming_languages", "confidence": 0.5, "match_type": "exact"},
        ]
        result = gen.generate_student_portrait(
            student_id="stu_mid", skills=skills, student_profile=profile,
        )
        s = result["summary"]
        assert s["competitiveness_level"] == "unknown"
        assert s["competitiveness_score"] is None



# ─────────────────────────────────────────────────────────
# 不变量
# ─────────────────────────────────────────────────────────

class TestInvariants:
    @pytest.mark.parametrize("case", [
        ("full", _full_student_profile(), _full_skills()),
        ("empty_skills", {"degree": "本科", "school": "某一本", "major": "软件工程"}, []),
        ("mid", {"degree": "本科", "school": "某二本", "major": "电子信息"}, [
            {"name": "Python", "level": "熟悉", "category": "programming_languages", "confidence": 0.5, "match_type": "exact"},
        ]),
    ])
    def test_dimension_scores_in_0_100(self, gen, case):
        """所有 D1-D4 维度分数在 [0, 100]"""
        _, profile, skills = case
        result = gen.generate_student_portrait(
            student_id="stu_inv", skills=skills, student_profile=profile,
        )
        for dim in ("D1_basic", "D2_skill", "D3_quality", "D4_potential"):
            d_score = result[dim]["dimension_score"]
            assert d_score is None or 0 <= d_score <= 100, f"{dim} score {d_score} out of [0,100]"

    @pytest.mark.parametrize("score,expected_level", [
        (90, "极强"), (85, "极强"), (84.9, "强"),
        (70, "强"), (69.9, "中等"),
        (55, "中等"), (54.9, "偏弱"),
        (40, "偏弱"), (39.9, "弱"),
        (0, "弱"),
    ])
    def test_competitiveness_level_consistent(self, gen, score, expected_level):
        """档位 ↔ 分数映射一致"""
        assert competitiveness_level(score) == expected_level

    def test_completeness_in_0_100(self, gen):
        """completeness 0-100"""
        result = gen.generate_student_portrait(
            student_id="stu_inv", skills=_full_skills(), student_profile=_full_student_profile(),
        )
        comp = result["summary"]["completeness_score"]
        assert 0 <= comp <= 100
