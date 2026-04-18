"""
L2 画像生成模块 (Portrait Generation)
生成学生四维画像和岗位四维画像
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class PortraitDimensions:
    D1_score: float
    D2_score: float
    D3_score: float
    D4_score: float


class PortraitGenerator:
    """画像生成器"""

    def __init__(self):
        self.weights = {
            "D1": 0.25,
            "D2": 0.35,
            "D3": 0.20,
            "D4": 0.20
        }

    def generate_student_portrait(
        self,
        student_id: str,
        skills: List[Dict],
        match_job_id: Optional[str] = None
    ) -> Dict:
        """
        生成学生四维画像

        Args:
            student_id: 学生ID
            skills: 技能列表
            match_job_id: 目标岗位ID (可选)

        Returns:
            四维画像
        """
        d1 = self._calc_d1_education(skills)
        d2 = self._calc_d2_skills(skills)
        d3 = self._calc_d3_quality(skills)
        d4 = self._calc_d4_potential(skills, skills)

        total = (
            d1 * self.weights["D1"] +
            d2 * self.weights["D2"] +
            d3 * self.weights["D3"] +
            d4 * self.weights["D4"]
        )

        categories = self._count_categories(skills)

        portrait = {
            "student_id": student_id,
            "target_job_id": match_job_id,
            "dimensions": {
                "D1": {"name": "基础要求", "score": round(d1, 1), "description": "学历、学校、专业等基础条件"},
                "D2": {"name": "职业技能", "score": round(d2, 1), "description": "技术技能掌握程度和项目经验"},
                "D3": {"name": "职业素养", "score": round(d3, 1), "description": "沟通、团队、领导力等软技能"},
                "D4": {"name": "发展潜力", "score": round(d4, 1), "description": "学习能力、成长空间、潜力评估"}
            },
            "total_score": round(total, 1),
            "level": self._get_level(total),
            "skill_categories": categories,
            "skill_count": len(skills),
            "skill_coverage": self._calc_skill_breadth(skills),
            "generated_at": datetime.now().isoformat()
        }

        return portrait

    def generate_job_portrait(
        self,
        job_id: str,
        position_name: str,
        skills: List[Dict],
        salary_range: Optional[Dict] = None
    ) -> Dict:
        """
        生成岗位四维画像

        Args:
            job_id: 岗位ID
            position_name: 岗位名称
            skills: 技能列表
            salary_range: 薪资范围 {"min": 15, "max": 30} (单位K)

        Returns:
            岗位画像
        """
        d1 = self._calc_job_d1(skills)
        d2 = self._calc_job_d2(skills)
        d3 = self._calc_job_d3(skills)
        d4 = self._calc_job_d4(skills)

        total = (
            d1 * self.weights["D1"] +
            d2 * self.weights["D2"] +
            d3 * self.weights["D3"] +
            d4 * self.weights["D4"]
        )

        portrait = {
            "job_id": job_id,
            "position_name": position_name,
            "dimensions": {
                "D1": {"name": "基础门槛", "score": round(d1, 1), "description": "学历、专业等硬性要求"},
                "D2": {"name": "技能要求", "score": round(d2, 1), "description": "技术技能要求的深度和广度"},
                "D3": {"name": "经验要求", "score": round(d3, 1), "description": "工作经验年限和复杂度"},
                "D4": {"name": "潜力要求", "score": round(d4, 1), "description": "学习能力、成长空间要求"}
            },
            "required_skill_count": len(skills),
            "required_skills": [s["name"] for s in skills],
            "skill_categories": self._count_categories(skills),
            "salary_range": salary_range or {"min": 15, "max": 30},
            "difficulty_score": round(total, 1),
            "difficulty_level": self._get_level(total),
            "generated_at": datetime.now().isoformat()
        }

        return portrait

    def _calc_d1_education(self, skills: List[Dict]) -> float:
        """计算 D1 基础分 (默认80分，学生暂无学历数据)"""
        return 80.0

    def _calc_d2_skills(self, skills: List[Dict]) -> float:
        """计算 D2 职业技能分"""
        if not skills:
            return 0.0

        scores = []
        for skill in skills:
            confidence = skill.get("confidence", 0.5)
            category_weight = self._get_category_weight(skill.get("category", "其他"))
            score = confidence * 100 * category_weight
            scores.append(score)

        return sum(scores) / len(scores) if scores else 0.0

    def _calc_d3_quality(self, skills: List[Dict]) -> float:
        """计算 D3 职业素养分 (基于技能多样性)"""
        if not skills:
            return 0.0

        categories = set(s.get("category") for s in skills if s.get("category"))
        diversity = min(len(categories) / 5.0, 1.0)

        avg_confidence = sum(s.get("confidence", 0.5) for s in skills) / len(skills)

        return (avg_confidence * 50 + diversity * 50)

    def _calc_d4_potential(self, skills: List[Dict], all_skills: List[Dict]) -> float:
        """计算 D4 发展潜力分"""
        if not skills:
            return 0.0

        foundation_count = sum(1 for s in skills if s.get("category") == "编程语言")
        advanced_count = sum(1 for s in skills if s.get("category") in ["AI/ML", "大数据", "云原生"])

        foundation_score = min(foundation_count * 15, 45)
        advanced_score = min(advanced_count * 20, 40)

        base_score = 20

        return min(foundation_score + advanced_score + base_score, 100)

    def _calc_skill_breadth(self, skills: List[Dict]) -> float:
        """计算技能广度"""
        if not skills:
            return 0.0

        categories = set(s.get("category") for s in skills if s.get("category"))
        return len(categories) / 8.0

    def _calc_job_d1(self, skills: List[Dict]) -> float:
        """计算岗位 D1 分 (基础门槛)"""
        required_count = sum(1 for s in skills if s.get("level") in ["必须", "精通"])
        return min(60 + required_count * 5, 95)

    def _calc_job_d2(self, skills: List[Dict]) -> float:
        """计算岗位 D2 分 (技能要求)"""
        if not skills:
            return 0.0

        required_count = len([s for s in skills if s.get("level") in ["必须", "精通"]])
        bonus_count = len([s for s in skills if s.get("level") == "加分"])

        return min(50 + required_count * 8 + bonus_count * 3, 95)

    def _calc_job_d3(self, skills: List[Dict]) -> float:
        """计算岗位 D3 分 (经验要求)"""
        return 70.0

    def _calc_job_d4(self, skills: List[Dict]) -> float:
        """计算岗位 D4 分 (潜力要求)"""
        advanced_count = sum(1 for s in skills if s.get("category") in ["AI/ML", "大数据", "云原生"])
        return min(50 + advanced_count * 10, 90)

    def _get_category_weight(self, category: str) -> float:
        """获取类别权重"""
        weights = {
            "编程语言": 1.0,
            "后端框架": 0.9,
            "前端": 0.85,
            "数据库": 0.9,
            "DevOps": 0.8,
            "AI/ML": 0.95,
            "大数据": 0.9,
            "云计算": 0.8,
            "其他": 0.5
        }
        return weights.get(category, 0.6)

    def _count_categories(self, skills: List[Dict]) -> Dict[str, int]:
        """统计技能类别"""
        categories = {}
        for skill in skills:
            cat = skill.get("category", "其他")
            categories[cat] = categories.get(cat, 0) + 1
        return categories

    def _get_level(self, score: float) -> str:
        """根据分数获取等级"""
        if score >= 85:
            return "优秀"
        elif score >= 70:
            return "良好"
        elif score >= 60:
            return "一般"
        else:
            return "较差"


def generate_student_portrait(student_id: str, skills: List[Dict]) -> Dict:
    """便捷函数: 生成学生画像"""
    generator = PortraitGenerator()
    return generator.generate_student_portrait(student_id, skills)


def generate_job_portrait(job_id: str, position_name: str, skills: List[Dict]) -> Dict:
    """便捷函数: 生成岗位画像"""
    generator = PortraitGenerator()
    return generator.generate_job_portrait(job_id, position_name, skills)


if __name__ == "__main__":
    test_skills = [
        {"name": "Python", "category": "编程语言", "confidence": 0.85},
        {"name": "Django", "category": "后端框架", "confidence": 0.75},
        {"name": "MySQL", "category": "数据库", "confidence": 0.8},
        {"name": "Redis", "category": "数据库", "confidence": 0.6},
        {"name": "Docker", "category": "DevOps", "confidence": 0.65},
        {"name": "TensorFlow", "category": "AI/ML", "confidence": 0.5},
    ]

    print("=" * 50)
    print("L2 画像生成测试")
    print("=" * 50)

    generator = PortraitGenerator()

    print("\n[学生画像]")
    student_portrait = generator.generate_student_portrait("stu_001", test_skills)
    print(f"  总分: {student_portrait['total_score']} ({student_portrait['level']})")
    print(f"  D1(基础): {student_portrait['dimensions']['D1']['score']}")
    print(f"  D2(技能): {student_portrait['dimensions']['D2']['score']}")
    print(f"  D3(素养): {student_portrait['dimensions']['D3']['score']}")
    print(f"  D4(潜力): {student_portrait['dimensions']['D4']['score']}")
    print(f"  技能类别: {student_portrait['skill_categories']}")

    print("\n[岗位画像]")
    job_portrait = generator.generate_job_portrait(
        "job_001",
        "Python后端开发工程师",
        test_skills
    )
    print(f"  岗位: {job_portrait['position_name']}")
    print(f"  难度: {job_portrait['difficulty_score']} ({job_portrait['difficulty_level']})")
    print(f"  要求技能数: {job_portrait['required_skill_count']}")
