"""
人岗匹配模块 (Person-Job Matching)
基于技能的匹配算法
"""

import uuid
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class MatchResult:
    student_id: str
    job_id: str
    total_score: float
    d1_score: float
    d2_score: float
    d3_score: float
    d4_score: float
    skill_coverage: float
    matched_skills: List[str]
    missing_skills: List[str]
    level_gap: Dict


class SkillMatcher:
    """技能匹配器"""

    SKILL_LEVELS = {
        "了解": 1,
        "熟悉": 2,
        "掌握": 3,
        "精通": 4
    }

    def __init__(self):
        self.weights = {
            "D1": 0.25,
            "D2": 0.35,
            "D3": 0.20,
            "D4": 0.20
        }

    def calculate_match(
        self,
        student_skills: List[Dict],
        job_skills: List[Dict]
    ) -> MatchResult:
        """
        计算人岗匹配度

        Args:
            student_skills: 学生技能列表 [{"name": "Python", "level": "掌握", "confidence": 0.8}]
            job_skills: 岗位技能要求 [{"name": "Python", "level": "必须", "weight": 0.9}]

        Returns:
            匹配结果
        """
        if not student_skills or not job_skills:
            return MatchResult(
                student_id="",
                job_id="",
                total_score=0,
                d1_score=0,
                d2_score=0,
                d3_score=0,
                d4_score=0,
                skill_coverage=0,
                matched_skills=[],
                missing_skills=[],
                level_gap={}
            )

        student_skill_map = {s["name"]: s for s in student_skills}
        job_skill_map = {j["name"]: j for j in job_skills}

        matched = []
        missing = []
        level_gaps = {}

        for job_skill_name, job_skill in job_skill_map.items():
            if job_skill_name in student_skill_map:
                matched.append(job_skill_name)

                student_level = student_skill_map[job_skill_name].get("level", "了解")
                job_level = job_skill.get("level", "必须")

                student_level_score = self.SKILL_LEVELS.get(student_level, 1)
                job_level_score = 4 if job_level == "必须" else 3

                gap = max(0, job_level_score - student_level_score)
                level_gaps[job_skill_name] = gap
            else:
                missing.append(job_skill_name)

        skill_coverage = len(matched) / len(job_skill_map) if job_skill_map else 0

        d2_score = skill_coverage * 100

        avg_gap = sum(level_gaps.values()) / len(level_gaps) if level_gaps else 0
        level_factor = 1 - (avg_gap / 4) * 0.3
        d2_adjusted = d2_score * max(0.7, level_factor)

        d1_score = 80
        d3_score = 75
        d4_score = 70

        total_score = (
            d1_score * self.weights["D1"] +
            d2_adjusted * self.weights["D2"] +
            d3_score * self.weights["D3"] +
            d4_score * self.weights["D4"]
        )

        return MatchResult(
            student_id=student_skills[0].get("student_id", ""),
            job_id=job_skills[0].get("job_id", ""),
            total_score=round(total_score, 1),
            d1_score=round(d1_score, 1),
            d2_score=round(d2_adjusted, 1),
            d3_score=round(d3_score, 1),
            d4_score=round(d4_score, 1),
            skill_coverage=round(skill_coverage, 2),
            matched_skills=matched,
            missing_skills=missing,
            level_gap=level_gaps
        )

    def get_gap_skills_priority(
        self,
        missing_skills: List[str],
        level_gaps: Dict
    ) -> List[Dict]:
        """
        获取缺失技能的优先级排序

        Args:
            missing_skills: 缺失技能列表
            level_gaps: 技能等级差距

        Returns:
            按优先级排序的缺失技能
        """
        critical = []
        quick_learn = []

        for skill in missing_skills:
            gap = level_gaps.get(skill, 0)
            if gap >= 2:
                critical.append({"skill": skill, "gap": gap, "priority": "critical"})
            else:
                quick_learn.append({"skill": skill, "gap": gap, "priority": "quick_learn"})

        critical.sort(key=lambda x: x["gap"], reverse=True)
        quick_learn.sort(key=lambda x: x["gap"], reverse=True)

        return critical + quick_learn


def calculate_skill_coverage(
    student_skills: List[str],
    job_skills: List[str]
) -> float:
    """计算技能覆盖度"""
    if not job_skills:
        return 0.0

    matched = set(student_skills) & set(job_skills)
    return len(matched) / len(job_skills)


def calculate_match_score(
    student_skills: List[Dict],
    job_skills: List[Dict]
) -> Dict:
    """
    便捷函数: 计算匹配分

    Args:
        student_skills: 学生技能
        job_skills: 岗位技能

    Returns:
        匹配结果
    """
    matcher = SkillMatcher()
    result = matcher.calculate_match(student_skills, job_skills)

    return {
        "total_score": result.total_score,
        "d1_score": result.d1_score,
        "d2_score": result.d2_score,
        "d3_score": result.d3_score,
        "d4_score": result.d4_score,
        "skill_coverage": result.skill_coverage,
        "matched_skills": result.matched_skills,
        "missing_skills": result.missing_skills,
        "gap_priorities": matcher.get_gap_skills_priority(
            result.missing_skills,
            result.level_gap
        ),
        "match_level": _get_match_level(result.total_score)
    }


def _get_match_level(score: float) -> str:
    """根据分数获取匹配等级"""
    if score >= 85:
        return "优秀"
    elif score >= 70:
        return "良好"
    elif score >= 60:
        return "一般"
    else:
        return "较差"


if __name__ == "__main__":
    student_skills = [
        {"name": "Python", "level": "掌握", "confidence": 0.85},
        {"name": "Django", "level": "熟悉", "confidence": 0.7},
        {"name": "MySQL", "level": "掌握", "confidence": 0.8},
        {"name": "Redis", "level": "了解", "confidence": 0.5},
        {"name": "Docker", "level": "熟悉", "confidence": 0.6},
    ]

    job_skills = [
        {"name": "Python", "level": "必须", "weight": 0.9},
        {"name": "Django", "level": "必须", "weight": 0.8},
        {"name": "MySQL", "level": "必须", "weight": 0.85},
        {"name": "Redis", "level": "加分", "weight": 0.5},
        {"name": "Kubernetes", "level": "加分", "weight": 0.4},
        {"name": "微服务", "level": "加分", "weight": 0.3},
    ]

    print("=" * 50)
    print("人岗匹配测试")
    print("=" * 50)

    result = calculate_match_score(student_skills, job_skills)

    print(f"\n总分: {result['total_score']} ({result['match_level']})")
    print(f"D1(基础): {result['d1_score']}")
    print(f"D2(技能): {result['d2_score']} (覆盖率: {result['skill_coverage']})")
    print(f"D3(素养): {result['d3_score']}")
    print(f"D4(潜力): {result['d4_score']}")

    print(f"\n已匹配: {result['matched_skills']}")
    print(f"缺失: {result['missing_skills']}")

    print("\n缺失技能优先级:")
    for item in result["gap_priorities"]:
        print(f"  [{item['priority']}] {item['skill']} (差距: {item['gap']})")
