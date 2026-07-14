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

class TestScoreSchool:
    def test_985_school(self, gen):
        """985: 15 分"""
        for s in ["清华大学", "北京大学", "上海交通大学", "浙江大学", "985大学"]:
            assert gen._score_school(s) == 15, f"{s} 应得 15"

    def test_211_school(self, gen):
        """211: 12 分"""
        assert gen._score_school("北京邮电大学") == 12
        assert gen._score_school("211工程大学") == 12

    def test_double_first_class(self, gen):
        """双一流: 10 分"""
        assert gen._score_school("某双一流大学") == 10

    def test_other_school(self, gen):
        """其他: 默认 8 分 (一本)"""
        assert gen._score_school("某普通一本大学") == 8

    def test_empty_school(self, gen):
        """空字符串: 0 分"""
        assert gen._score_school("") == 0


class TestScoreMajor:
    def test_top_major(self, gen):
        """计算机/软工/AI: 25 分"""
        for m in ["计算机科学与技术", "软件工程", "人工智能"]:
            assert gen._score_major(m) == 25, f"{m} 应得 25"

    def test_data_science(self, gen):
        """数据科学/信息安全: 23 分"""
        assert gen._score_major("数据科学") == 23
        assert gen._score_major("信息安全") == 23

    def test_other_engineering(self, gen):
        """其他理工: 10 分"""
        assert gen._score_major("机械工程") == 10

    def test_liberal_arts(self, gen):
        """文科: 5 分"""
        assert gen._score_major("汉语言文学") == 5

    def test_empty_major(self, gen):
        """空: 0 分"""
        assert gen._score_major("") == 0


class TestClassifySchool:
    def test_985(self, gen):
        assert gen._classify_school("清华大学") == "985"
        assert gen._classify_school("北京大学") == "985"

    def test_211(self, gen):
        assert gen._classify_school("北京邮电大学") == "211"

    def test_double_first_class(self, gen):
        assert gen._classify_school("某双一流大学") == "双一流"

    def test_empty(self, gen):
        assert gen._classify_school("") == ""


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
        # 档位: ≥85 极强
        assert s["competitiveness_level"] == "极强", (
            f"期望 极强 (≥85), 实际 {s['competitiveness_level']} ({s['competitiveness_score']})"
        )
        # 分数 ≥85
        assert s["competitiveness_score"] >= 85

    def test_empty_skills(self, gen):
        """零技能学生: D2=0, 但 D1/D3/D4 仍可计算"""
        result = gen.generate_student_portrait(
            student_id="stu_empty",
            skills=[],
            student_profile={"degree": "本科", "school": "某一本", "major": "软件工程"},
        )
        s = result["summary"]
        d2 = result["D2_skill"]
        # D2 维度分数 = 0
        assert d2["dimension_score"] == 0
        # 但 competitiveness 不应崩溃
        assert 0 <= s["competitiveness_score"] <= 100
        assert s["competitiveness_level"] in {"极强", "强", "中等", "偏弱", "弱"}

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
        # 档位: 中等/偏弱/弱 都可能
        assert s["competitiveness_level"] in {"中等", "偏弱", "弱"}, (
            f"期望中等/偏弱/弱, 实际 {s['competitiveness_level']} ({s['competitiveness_score']})"
        )
        # 分数 < 70
        assert s["competitiveness_score"] < 70


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
            assert 0 <= d_score <= 100, f"{dim} score {d_score} out of [0,100]"

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
