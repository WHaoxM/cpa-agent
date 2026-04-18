"""
L3 报告生成模块 (Report Generation)
生成人岗匹配分析报告
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional
from .portrait_generator import PortraitGenerator
from .matcher import calculate_match_score, SkillMatcher


class ReportGenerator:
    """报告生成器"""

    def __init__(self):
        self.portrait_gen = PortraitGenerator()
        self.matcher = SkillMatcher()

    def generate_match_report(
        self,
        student_id: str,
        student_skills: List[Dict],
        job_id: str,
        job_name: str,
        job_skills: List[Dict]
    ) -> Dict:
        """
        生成人岗匹配报告

        Args:
            student_id: 学生ID
            student_skills: 学生技能列表
            job_id: 岗位ID
            job_name: 岗位名称
            job_skills: 岗位技能列表

        Returns:
            匹配报告
        """
        student_portrait = self.portrait_gen.generate_student_portrait(
            student_id, student_skills, job_id
        )

        job_portrait = self.portrait_gen.generate_job_portrait(
            job_id, job_name, job_skills
        )

        match_result = self.matcher.calculate_match(student_skills, job_skills)

        gap_analysis = self.matcher.get_gap_skills_priority(
            match_result.missing_skills,
            match_result.level_gap
        )

        recommendations = self._generate_recommendations(
            student_skills,
            match_result.missing_skills,
            gap_analysis
        )

        report = {
            "report_id": f"rpt_{uuid.uuid4().hex[:12]}",
            "student_id": student_id,
            "job_id": job_id,
            "job_name": job_name,
            "summary": {
                "match_score": match_result.total_score,
                "match_level": self._get_match_level(match_result.total_score),
                "skill_coverage": match_result.skill_coverage,
                "matched_count": len(match_result.matched_skills),
                "missing_count": len(match_result.missing_skills),
                "conclusion": self._generate_conclusion(match_result)
            },
            "student_portrait": student_portrait,
            "job_portrait": job_portrait,
            "match_details": {
                "D1": {
                    "name": "基础要求",
                    "student_score": match_result.d1_score,
                    "requirement": "学历、专业符合岗位基本要求",
                    "passed": match_result.d1_score >= 60
                },
                "D2": {
                    "name": "职业技能",
                    "student_score": match_result.d2_score,
                    "skill_coverage": match_result.skill_coverage,
                    "matched_skills": match_result.matched_skills,
                    "passed": match_result.skill_coverage >= 0.6
                },
                "D3": {
                    "name": "职业素养",
                    "student_score": match_result.d3_score,
                    "description": "基于技能多样性和学习广度评估"
                },
                "D4": {
                    "name": "发展潜力",
                    "student_score": match_result.d4_score,
                    "description": "基于基础技能和进阶技能比例评估"
                }
            },
            "gap_analysis": {
                "total_gaps": len(match_result.missing_skills),
                "critical_gaps": [g for g in gap_analysis if g["priority"] == "critical"],
                "quick_learn_gaps": [g for g in gap_analysis if g["priority"] == "quick_learn"],
                "details": gap_analysis
            },
            "recommendations": recommendations,
            "generated_at": datetime.now().isoformat()
        }

        return report

    def generate_career_report(
        self,
        student_id: str,
        student_skills: List[Dict]
    ) -> Dict:
        """
        生成职业发展报告

        Args:
            student_id: 学生ID
            student_skills: 学生技能列表

        Returns:
            职业发展报告
        """
        portrait = self.portrait_gen.generate_student_portrait(
            student_id, student_skills
        )

        career_paths = self._analyze_career_paths(student_skills)

        skill_gaps = self._analyze_skill_gaps(student_skills)

        report = {
            "report_id": f"career_{uuid.uuid4().hex[:12]}",
            "student_id": student_id,
            "portrait": portrait,
            "career_analysis": {
                "current_level": self._estimate_current_level(student_skills),
                "potential_direction": career_paths,
                "skill_gaps": skill_gaps,
                "learning_suggestions": self._generate_learning_suggestions(student_skills)
            },
            "generated_at": datetime.now().isoformat()
        }

        return report

    def _generate_recommendations(
        self,
        student_skills: List[Dict],
        missing_skills: List[str],
        gap_analysis: List[Dict]
    ) -> List[Dict]:
        """生成建议"""
        recommendations = []

        if not missing_skills:
            recommendations.append({
                "type": "positive",
                "title": "技能完全匹配",
                "content": "您的技能完全满足岗位要求，可以尝试更高难度的岗位。"
            })
            return recommendations

        critical = [g for g in gap_analysis if g["priority"] == "critical"]
        if critical:
            recommendations.append({
                "type": "priority",
                "title": "优先弥补关键技能差距",
                "content": f"建议优先学习: {', '.join([g['skill'] for g in critical[:3]])}",
                "skills": [g['skill'] for g in critical[:3]]
            })

        quick_learn = [g for g in gap_analysis if g["priority"] == "quick_learn"]
        if quick_learn:
            recommendations.append({
                "type": "bonus",
                "title": "快速提升加分项",
                "content": f"这些技能可以快速学习: {', '.join([g['skill'] for g in quick_learn[:3]])}",
                "skills": [g['skill'] for g in quick_learn[:3]]
            })

        recommendations.append({
            "type": "general",
            "title": "学习路径建议",
            "content": "建议按照 基础 → 进阶 → 高级 的顺序系统学习缺失技能。"
        })

        return recommendations

    def _generate_conclusion(self, match_result) -> str:
        """生成结论"""
        coverage = match_result.skill_coverage

        if coverage >= 0.9:
            return "高度匹配，您的技能与岗位要求高度吻合，建议申请。"
        elif coverage >= 0.7:
            return "较好匹配，核心技能已具备，可尝试申请并补充差距技能。"
        elif coverage >= 0.5:
            return "部分匹配，需要针对缺失技能进行有针对性的学习。"
        else:
            return "匹配度较低，建议先提升基础技能再申请该岗位。"

    def _get_match_level(self, score: float) -> str:
        """获取匹配等级"""
        if score >= 85:
            return "优秀"
        elif score >= 70:
            return "良好"
        elif score >= 60:
            return "一般"
        else:
            return "较差"

    def _analyze_career_paths(self, skills: List[Dict]) -> List[Dict]:
        """分析职业发展路径"""
        paths = []

        categories = set(s.get("category") for s in skills)

        if "后端框架" in categories or "编程语言" in categories:
            paths.append({
                "direction": "后端开发",
                "current": "中级工程师",
                "next": "高级工程师 → 技术专家/架构师",
                "key_skills": ["系统设计", "分布式", "微服务"]
            })

        if "前端" in categories:
            paths.append({
                "direction": "前端开发",
                "current": "中级工程师",
                "next": "高级工程师 → 前端专家/技术负责人",
                "key_skills": ["前端架构", "性能优化", "工程化"]
            })

        if "AI/ML" in categories:
            paths.append({
                "direction": "AI/算法",
                "current": "算法工程师",
                "next": "高级算法工程师 → AI架构师",
                "key_skills": ["深度学习", "模型优化", "AI系统工程"]
            })

        return paths

    def _analyze_skill_gaps(self, skills: List[Dict]) -> List[Dict]:
        """分析技能差距"""
        categories = set(s.get("category") for s in skills)
        gaps = []

        category_map = {
            "前端": ["后端框架", "数据库"],
            "后端框架": ["前端", "DevOps"],
            "DevOps": ["云原生", "安全"],
            "AI/ML": ["大数据", "分布式"]
        }

        for cat, recommendations in category_map.items():
            if cat in categories:
                for rec in recommendations:
                    if rec not in categories:
                        gaps.append({
                            "category": rec,
                            "reason": f"{cat}工程师补充{rec}技能可拓宽职业发展",
                            "priority": "medium"
                        })

        return gaps[:5]

    def _generate_learning_suggestions(self, skills: List[Dict]) -> List[str]:
        """生成学习建议"""
        suggestions = []
        categories = set(s.get("category") for s in skills)

        if "编程语言" not in categories:
            suggestions.append("建议深入学习一门编程语言，如 Python 或 Java")

        if "数据库" not in categories:
            suggestions.append("建议掌握主流数据库，如 MySQL + Redis")

        if "DevOps" not in categories:
            suggestions.append("建议了解 Docker 和 CI/CD 工具")

        if categories.__len__() < 3:
            suggestions.append("建议拓宽技术栈，覆盖前端、后端、数据库等多个领域")

        return suggestions

    def _estimate_current_level(self, skills: List[Dict]) -> str:
        """估算当前水平"""
        count = len(skills)
        has_advanced = any(s.get("category") in ["AI/ML", "大数据", "云原生"] for s in skills)

        if count >= 10 and has_advanced:
            return "高级"
        elif count >= 6:
            return "中级"
        else:
            return "初级"


def generate_match_report(
    student_id: str,
    student_skills: List[Dict],
    job_id: str,
    job_name: str,
    job_skills: List[Dict]
) -> Dict:
    """便捷函数: 生成人岗匹配报告"""
    generator = ReportGenerator()
    return generator.generate_match_report(
        student_id, student_skills, job_id, job_name, job_skills
    )


def generate_career_report(student_id: str, student_skills: List[Dict]) -> Dict:
    """便捷函数: 生成职业发展报告"""
    generator = ReportGenerator()
    return generator.generate_career_report(student_id, student_skills)


if __name__ == "__main__":
    student_skills = [
        {"name": "Python", "category": "编程语言", "confidence": 0.85},
        {"name": "Django", "category": "后端框架", "confidence": 0.75},
        {"name": "MySQL", "category": "数据库", "confidence": 0.8},
        {"name": "Redis", "category": "数据库", "confidence": 0.6},
        {"name": "Docker", "category": "DevOps", "confidence": 0.65},
    ]

    job_skills = [
        {"name": "Python", "level": "必须", "weight": 0.9},
        {"name": "Django", "level": "必须", "weight": 0.8},
        {"name": "MySQL", "level": "必须", "weight": 0.85},
        {"name": "Redis", "level": "加分", "weight": 0.5},
        {"name": "Kubernetes", "level": "加分", "weight": 0.4},
    ]

    print("=" * 50)
    print("L3 报告生成测试")
    print("=" * 50)

    generator = ReportGenerator()

    report = generator.generate_match_report(
        "stu_001",
        student_skills,
        "job_001",
        "Python后端开发工程师",
        job_skills
    )

    print(f"\n报告ID: {report['report_id']}")
    print(f"匹配总分: {report['summary']['match_score']} ({report['summary']['match_level']})")
    print(f"技能覆盖率: {report['summary']['skill_coverage']}")
    print(f"结论: {report['summary']['conclusion']}")
    print(f"\n缺失技能: {report['summary']['missing_count']} 个")
    print(f"建议:")
    for rec in report["recommendations"]:
        print(f"  [{rec['type']}] {rec['title']}: {rec['content']}")
