"""
L2 画像生成模块 (Portrait Generation) — v2.0 冻结 Schema 对齐
生成学生四维画像 (2A) 和岗位四维画像 (2B)

冻结规则:
- 评分公式: competitiveness_score = D1×0.25 + D2×0.35 + D3×0.20 + D4×0.20
- D2.skill_coverage = (exact_count + semantic_count×0.8) / required_count
- 语义匹配阈值: BGE-M3 cosine > 0.85
- 竞争力级别: ≥85极强 / ≥70强 / ≥55中等 / ≥40偏弱 / <40弱

评分表走 YAML (config/defaults/rules/scoring_tables.yaml 的 portrait_generator 节)
通过 config.loader.get_rules_config() 加载
"""

import math
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from dataclasses import dataclass, asdict

from config.loader import get_rules_config, competitiveness_level


@dataclass
class PortraitDimensions:
    D1_score: float
    D2_score: float
    D3_score: float
    D4_score: float


class PortraitGenerator:
    """画像生成器 — v2.0 冻结 Schema"""

    def __init__(self):
        self._rules = get_rules_config()
        self.weights = self._rules.dimension_weights
        self._scoring = self._rules.scoring_tables

    def generate_student_portrait(
        self,
        student_id: str,
        skills: List[Dict],
        match_job_id: Optional[str] = None,
        student_profile: Optional[Dict] = None,
        target_job_skills: Optional[List[Dict]] = None,
    ) -> Dict:
        d1 = self._calc_d1_basic(skills, student_profile)
        d2 = self._calc_d2_skill(skills, student_profile, target_job_skills)
        d3 = self._calc_d3_quality(skills, student_profile)
        d4 = self._calc_d4_potential(skills, student_profile)

        comp_score = (
            d1["dimension_score"] * self.weights["D1"] +
            d2["dimension_score"] * self.weights["D2"] +
            d3["dimension_score"] * self.weights["D3"] +
            d4["dimension_score"] * self.weights["D4"]
        )
        completeness = self._calc_completeness(d1, d2, d3, d4)

        return {
            "student_id": student_id,
            "name": (student_profile or {}).get("name", ""),
            "D1_basic": d1,
            "D2_skill": d2,
            "D3_quality": d3,
            "D4_potential": d4,
            "summary": {
                "dimension_weights": self.weights,
                "competitiveness_score": round(comp_score, 1),
                "competitiveness_level": competitiveness_level(comp_score, self._rules),
                "completeness_score": round(completeness, 1),
            },
            "generated_at": datetime.now().isoformat(),
        }

    def generate_job_portrait(
        self,
        job_id: str,
        position_name: str,
        skills: List[Dict],
        salary_range: Optional[Dict] = None,
        job_profile: Optional[Dict] = None,
        jd_count: int = 1,
    ) -> Dict:
        d1 = self._calc_job_d1(skills, job_profile)
        d2 = self._calc_job_d2(skills)
        d3 = self._calc_job_d3(skills, job_profile)
        d4 = self._calc_job_d4(skills, job_profile)

        return {
            "portrait_id": job_id,
            "position_name": position_name,
            "jd_count": jd_count,
            "D1_requirements": d1,
            "D2_requirements": d2,
            "D3_requirements": d3,
            "D4_requirements": d4,
            "dimension_weights": self.weights,
            "salary_range": salary_range,
            "generated_at": datetime.now().isoformat(),
        }

    # ═══ D1 基础要求 (冻结: sub_degree 0-30, sub_school 0-15, sub_major 0-25, sub_cert 0-15, sub_language 0-15) ═══

    def _calc_d1_basic(self, skills: List[Dict], profile: Optional[Dict] = None) -> Dict:
        profile = profile or {}
        degree = profile.get("degree", "")
        school = profile.get("school", "")
        major = profile.get("major", "")
        certs = profile.get("certificates", [])
        languages = profile.get("languages", [])

        sub_degree = self._scoring["degree_score"].get(degree, 0)
        sub_school = self._score_school(school)
        sub_major = self._score_major(major)
        sub_cert = min(len(certs) * 7.5, self._scoring["d1_sub_max"]["sub_cert"]) if certs else 0
        sub_language = min(len(languages) * 7.5, self._scoring["d1_sub_max"]["sub_language"]) if languages else 0

        dimension_score = min(sub_degree + sub_school + sub_major + sub_cert + sub_language, 100)

        evidence = []
        if degree: evidence.append(f"学历: {degree}")
        if school: evidence.append(f"学校: {school}")
        if major: evidence.append(f"专业: {major}")
        if certs: evidence.append(f"证书: {', '.join(str(c) for c in certs[:5])}")
        if languages: evidence.append(f"语言: {', '.join(str(l) for l in languages[:5])}")

        return {
            "degree": degree,
            "school_tier": self._classify_school(school),
            "major_category": major,
            "sub_degree": sub_degree,
            "sub_school": sub_school,
            "sub_major": sub_major,
            "sub_cert": sub_cert,
            "sub_language": sub_language,
            "dimension_score": round(dimension_score, 1),
            "evidence": evidence,
        }

    def _score_school(self, school: str) -> int:
        if not school: return 0
        school_lower = school.lower()
        tier_table = self._scoring["school_tier_score"]
        top_985 = [
            "清华", "清华大学", "北大", "北京大学", "复旦", "复旦大学",
            "上交", "上海交通大学", "浙大", "浙江大学", "中科大",
            "中国科学技术大学", "南大", "南京大学", "哈工大",
            "哈尔滨工业大学", "西交大", "西安交通大学", "北航",
            "北京航空航天大学", "华科", "华中科技大学",
        ]
        known_211 = [
            "北京邮电大学", "北邮", "北京交通大学", "北京科技大学",
            "南京理工大学", "华东理工大学", "西安电子科技大学",
        ]
        if "985" in school_lower or any(kw in school for kw in top_985):
            return tier_table["985"]
        if "211" in school_lower or any(kw in school for kw in known_211): return tier_table["211"]
        if "双一流" in school_lower: return tier_table["双一流"]
        return tier_table.get("一本", 8)

    def _classify_school(self, school: str) -> str:
        if not school: return ""
        school_lower = school.lower()
        top_985 = ["清华", "清华大学", "北大", "北京大学", "复旦", "上交", "上海交通大学", "浙大", "浙江大学"]
        known_211 = ["北京邮电大学", "北邮"]
        if "985" in school_lower or any(kw in school for kw in top_985):
            return "985"
        if "211" in school_lower or any(kw in school for kw in known_211): return "211"
        if "双一流" in school_lower: return "双一流"
        return "其他"

    def _score_major(self, major: str) -> int:
        if not major: return 0
        major_table = self._scoring["major_category_score"]
        liberal_keywords = ["汉语言", "文学", "历史", "哲学", "新闻", "传播", "法学", "社会学", "外语", "英语"]
        if any(kw in major for kw in liberal_keywords):
            return major_table.get("文科", 5)
        for key, score in major_table.items():
            if key and key not in ("其他理工", "文科") and key in major: return score
        return major_table.get("其他理工", 10)

    # ═══ D2 职业技能 (冻结: sub_skill 0-35, sub_project 0-35, sub_internship 0-30, skill_coverage) ═══

    def _calc_d2_skill(self, skills: List[Dict], profile: Optional[Dict] = None, target_job_skills: Optional[List[Dict]] = None) -> Dict:
        profile = profile or {}
        skill_list = []
        for s in skills:
            match_type = s.get("match_type", "unmatched")
            if match_type not in ("exact", "semantic", "unmatched"):
                match_type = "unmatched"
            skill_list.append({
                "name": s.get("name", ""),
                "proficiency": s.get("level", s.get("proficiency", "了解")),
                "category": s.get("category", "其他"),
                "match_type": match_type,
            })

        skill_count = len(skills)
        project_count = len(profile.get("projects", []))
        internship_months = profile.get("internship_months", 0)

        # skill_coverage: 冻结公式 = (exact_count + semantic_count×0.8) / required_count
        skill_coverage = self._calc_skill_coverage(skills, target_job_skills)

        d2_max = self._scoring["d2_sub_max"]
        sub_skill = min(self._score_skill_depth(skills), d2_max["sub_skill"])
        sub_project = min(project_count * 14, d2_max["sub_project"])
        sub_internship = min(int(internship_months) * 3, d2_max["sub_internship"]) if internship_months else 0

        dimension_score = min(sub_skill + sub_project + sub_internship, 100)

        evidence = []
        if skills: evidence.append(f"掌握 {skill_count} 项技能")
        if project_count: evidence.append(f"{project_count} 个项目经验")
        if internship_months: evidence.append(f"{internship_months} 个月实习")

        return {
            "skill_list": skill_list,
            "skill_count": skill_count,
            "project_count": project_count,
            "internship_months": int(internship_months) if internship_months else 0,
            "skill_coverage": round(skill_coverage, 3),
            "sub_skill": round(sub_skill, 1),
            "sub_project": round(sub_project, 1),
            "sub_internship": round(sub_internship, 1),
            "dimension_score": round(dimension_score, 1),
            "evidence": evidence,
        }

    def _calc_skill_coverage(self, student_skills: List[Dict], target_job_skills: Optional[List[Dict]]) -> float:
        """冻结公式: skill_coverage = (exact_count + semantic_count×0.8) / required_count"""
        if not target_job_skills:
            return 0.0
        required = [s for s in target_job_skills if s.get("level") in ("必须", "精通", "required")]
        if not required:
            required = target_job_skills
        required_count = len(required)
        if required_count == 0:
            return 0.0

        exact_count = 0
        semantic_count = 0
        for js in required:
            jname = js.get("name", "").lower()
            matches = [s for s in student_skills if s.get("name", "").lower() == jname]
            if any(s.get("match_type", "exact") == "exact" for s in matches):
                exact_count += 1
            elif any(s.get("match_type") == "semantic" for s in matches):
                semantic_count += 1
        return (exact_count + semantic_count * 0.8) / required_count

    def _score_skill_depth(self, skills: List[Dict]) -> float:
        if not skills: return 0.0
        total = 0.0
        for s in skills:
            confidence = s.get("confidence", 0.5)
            cat_weight = self._get_category_weight(s.get("category", "其他"))
            level_weight = self._get_level_weight(s.get("level", s.get("proficiency", "")))
            total += confidence * cat_weight * level_weight * 14
        return total

    # ═══ D3 职业素养 (冻结: 4项各0-25, rule_bonus, confidence) ═══

    def _calc_d3_quality(self, skills: List[Dict], profile: Optional[Dict] = None) -> Dict:
        profile = profile or {}
        soft_skills = profile.get("soft_skills", {})
        experiences = profile.get("experience", [])
        projects = profile.get("projects", [])

        communication = self._score_quality_item("communication", soft_skills.get("communication"), experiences)
        teamwork = self._score_quality_item("teamwork", soft_skills.get("teamwork"), projects)
        stress_tolerance = self._score_quality_item("stress_tolerance", soft_skills.get("stress_tolerance"), experiences)
        responsibility = self._score_quality_item("responsibility", soft_skills.get("responsibility"), projects)

        # rule_bonus (冻结: 实习+5, 团队项目+5, leader+8, 学生干部+3)
        rule_bonus = 0
        if profile.get("internship_months", 0) > 0: rule_bonus += 5
        team_projects = [p for p in projects if isinstance(p, dict) and p.get("is_team")]
        if team_projects: rule_bonus += 5
        leader_roles = [p for p in projects if isinstance(p, dict) and p.get("role", "").lower() in ("leader", "负责人", "组长")]
        if leader_roles: rule_bonus += 8
        if profile.get("student_cadre"): rule_bonus += 3

        dimension_score = min(
            communication["score"] + teamwork["score"] + stress_tolerance["score"] + responsibility["score"] + rule_bonus,
            100
        )

        # confidence: Shannon Entropy 映射, <0.6 标记 low_confidence
        scores = [communication["score"], teamwork["score"], stress_tolerance["score"], responsibility["score"]]
        confidence = self._shannon_confidence(scores)

        return {
            "communication": communication,
            "teamwork": teamwork,
            "stress_tolerance": stress_tolerance,
            "responsibility": responsibility,
            "rule_bonus": rule_bonus,
            "dimension_score": round(dimension_score, 1),
            "confidence": round(confidence, 3),
        }

    def _score_quality_item(self, item_name: str, explicit_value, related_data: list) -> Dict:
        """评分单素养项: 有显式值用显式值, 否则从数据推断"""
        if explicit_value is not None:
            if isinstance(explicit_value, dict):
                level = explicit_value.get("level", "中")
                score = min(explicit_value.get("score", 12), 25)
                evidence = explicit_value.get("evidence", "")
            elif isinstance(explicit_value, str):
                level_map = self._scoring.get("d3_quality_level_score", {})
                score = level_map.get(explicit_value, 12)
                level = explicit_value
                evidence = f"自评: {explicit_value}"
            elif isinstance(explicit_value, (int, float)):
                numeric_score = max(0.0, min(float(explicit_value), 5.0))
                score = int(round(numeric_score / 5.0 * 25))
                if score >= 20:
                    level = "强"
                elif score >= 12:
                    level = "中"
                else:
                    level = "弱"
                evidence = f"自评: {explicit_value}/5"
            else:
                score, level, evidence = 12, "中", ""
        else:
            # 推断: 从相关数据量级估算
            data_count = len(related_data) if related_data else 0
            if data_count >= 3:
                score, level = 18, "强"
            elif data_count >= 1:
                score, level = 12, "中"
            else:
                score, level = 5, "弱"
            evidence = f"基于 {data_count} 条相关记录推断"

        return {"level": level, "score": score, "evidence": evidence}

    def _shannon_confidence(self, scores: List[int]) -> float:
        """Shannon Entropy → [0,1] confidence; <0.6 标记 low_confidence"""
        total = sum(scores)
        if total == 0: return 0.0
        probs = [s / total for s in scores if s > 0]
        entropy = -sum(p * math.log2(p) for p in probs) if probs else 0.0
        max_entropy = math.log2(4)  # 4 items
        return round(1.0 - entropy / max_entropy, 3) if max_entropy > 0 else 0.0

    # ═══ D4 发展潜力 (冻结: sub_competition 0-25, sub_research 0-25, sub_academic 0-25, sub_leadership 0-25) ═══

    def _calc_d4_potential(self, skills: List[Dict], profile: Optional[Dict] = None) -> Dict:
        profile = profile or {}

        # 竞赛
        competitions = profile.get("competitions", [])
        d4_max = self._scoring["d4_sub_max"]
        sub_competition = min(self._score_competitions(competitions), d4_max["sub_competition"])

        # 科研
        researches = profile.get("research", profile.get("researches", []))
        sub_research = min(self._score_research(researches), d4_max["sub_research"])

        # 学术 (GPA + 排名)
        gpa = None
        rank_pct = None
        try:
            gpa_val = profile.get("gpa")
            if gpa_val: gpa = float(gpa_val)
        except (ValueError, TypeError):
            pass
        try:
            rank_val = profile.get("rank_percent")
            if rank_val: rank_pct = float(rank_val)
        except (ValueError, TypeError):
            pass
        sub_academic = min(_academic_score(gpa, rank_pct), d4_max["sub_academic"])

        # 领导力
        leadership_roles = list(profile.get("leadership_roles", []))
        projects = profile.get("projects", [])
        if any(isinstance(p, dict) and p.get("role", "").lower() in ("leader", "负责人", "组长") for p in projects):
            leadership_roles.append("项目负责人")
        if profile.get("student_cadre"):
            leadership_roles.append("班干部")
        sub_leadership = min(self._score_leadership(leadership_roles), d4_max["sub_leadership"])

        dimension_score = min(sub_competition + sub_research + sub_academic + sub_leadership, 100)

        evidence = []
        if competitions: evidence.append(f"竞赛: {len(competitions)} 项")
        if researches: evidence.append(f"科研: {len(researches)} 项")
        if gpa: evidence.append(f"GPA: {gpa}")
        if rank_pct: evidence.append(f"排名: 前{rank_pct}%")
        if leadership_roles: evidence.append(f"领导角色: {len(leadership_roles)} 个")

        return {
            "sub_competition": sub_competition,
            "sub_research": sub_research,
            "sub_academic": sub_academic,
            "sub_leadership": sub_leadership,
            "dimension_score": round(dimension_score, 1),
            "evidence": evidence,
        }

    def _score_competitions(self, competitions: list) -> int:
        if not competitions: return 0
        total = 0
        comp_table = self._scoring["competition_score"]
        for comp in competitions:
            if isinstance(comp, dict):
                level = comp.get("level", comp.get("award", ""))
            else:
                level = str(comp)
            total += comp_table.get(level, comp_table.get("其他", 2))
        return total

    def _score_research(self, researches: list) -> int:
        if not researches: return 0
        total = 0
        res_table = self._scoring["research_score"]
        for res in researches:
            if isinstance(res, dict):
                level = res.get("level", res.get("type", ""))
            else:
                level = str(res)
            total += res_table.get(level, res_table.get("其他", 2))
        return total

    def _score_leadership(self, roles: list) -> int:
        if not roles: return 0
        total = 0
        lead_table = self._scoring["leadership_score"]
        for role in roles:
            if isinstance(role, dict):
                role_name = role.get("role", role.get("name", ""))
            else:
                role_name = str(role)
            total += lead_table.get(role_name, lead_table.get("其他", 3))
        return total

    # ═══ 完整度评分 ═══

    def _calc_completeness(self, d1: Dict, d2: Dict, d3: Dict, d4: Dict) -> float:
        """字段填充率加权平均"""
        total_fields = 0
        filled_fields = 0

        # D1: 5 sub + degree + school_tier + major_category
        d1_keys = ["sub_degree", "sub_school", "sub_major", "sub_cert", "sub_language"]
        for k in d1_keys:
            total_fields += 1
            if d1.get(k, 0) > 0: filled_fields += 1
        for k in ["degree", "school_tier", "major_category"]:
            total_fields += 1
            if d1.get(k): filled_fields += 1

        # D2: skill_count + project_count + internship_months + skill_coverage
        for k in ["skill_count", "project_count", "internship_months"]:
            total_fields += 1
            if d2.get(k, 0) > 0: filled_fields += 1
        total_fields += 1
        if d2.get("skill_coverage", 0) > 0: filled_fields += 1

        # D3: 4 quality items
        for item in ["communication", "teamwork", "stress_tolerance", "responsibility"]:
            total_fields += 1
            q = d3.get(item, {})
            if q.get("score", 0) > 0: filled_fields += 1

        # D4: 4 sub dimensions
        for k in ["sub_competition", "sub_research", "sub_academic", "sub_leadership"]:
            total_fields += 1
            if d4.get(k, 0) > 0: filled_fields += 1

        return (filled_fields / total_fields * 100) if total_fields > 0 else 0.0

    # ═══ 岗位画像方法 (2B Schema) ═══

    def _calc_job_d1(self, skills: List[Dict], job_profile: Optional[Dict] = None) -> Dict:
        job_profile = job_profile or {}
        return {
            "required_degree": job_profile.get("required_degree", ""),
            "major_strict": job_profile.get("major_strict", False),
            "required_majors": job_profile.get("required_majors", []),
            "required_certs": job_profile.get("required_certs", []),
            "required_languages": job_profile.get("required_languages", []),
        }

    def _calc_job_d2(self, skills: List[Dict]) -> Dict:
        required_skills = [{"skill": s["name"], "level": s.get("level", "必须"), "tf_score": s.get("weight", 0.8)}
                           for s in skills if s.get("level") in ("必须", "精通", "required")]
        preferred_skills = [{"skill": s["name"], "level": s.get("level", "偏好"), "tf_score": s.get("weight", 0.5)}
                            for s in skills if s.get("level") in ("偏好", "preferred")]
        bonus_skills = [{"skill": s["name"], "level": s.get("level", "加分"), "tf_score": s.get("weight", 0.3)}
                        for s in skills if s.get("level") in ("加分", "bonus")]
        return {
            "required_skills": required_skills,
            "preferred_skills": preferred_skills,
            "bonus_skills": bonus_skills,
        }

    def _calc_job_d3(self, skills: List[Dict], job_profile: Optional[Dict] = None) -> Dict:
        job_profile = job_profile or {}
        d3_weights = self._scoring.get("d3_sub_weights", {})
        d3_required = self._scoring.get("d3_sub_required", {})
        return {
            "communication": {"required": job_profile.get("communication_required", d3_required.get("communication", False)), "weight": job_profile.get("communication_weight", d3_weights.get("communication", 0.25))},
            "teamwork": {"required": job_profile.get("teamwork_required", d3_required.get("teamwork", True)), "weight": job_profile.get("teamwork_weight", d3_weights.get("teamwork", 0.30))},
            "stress_tolerance": {"required": job_profile.get("stress_tolerance_required", d3_required.get("stress_tolerance", False)), "weight": job_profile.get("stress_tolerance_weight", d3_weights.get("stress_tolerance", 0.20))},
            "responsibility": {"required": job_profile.get("responsibility_required", d3_required.get("responsibility", True)), "weight": job_profile.get("responsibility_weight", d3_weights.get("responsibility", 0.25))},
        }

    def _calc_job_d4(self, skills: List[Dict], job_profile: Optional[Dict] = None) -> Dict:
        job_profile = job_profile or {}
        d4_weights = self._scoring.get("d4_sub_weights", {})
        d4_defaults = self._scoring.get("d4_defaults", {})
        return {
            "innovation_weight": job_profile.get("innovation_weight", d4_weights.get("innovation", 0.30)),
            "learning_weight": job_profile.get("learning_weight", d4_weights.get("learning", 0.30)),
            "leadership_weight": job_profile.get("leadership_weight", d4_weights.get("leadership", 0.20)),
            "internship_preferred": job_profile.get("internship_preferred", d4_defaults.get("internship_preferred", True)),
            "project_count_min": job_profile.get("project_count_min", d4_defaults.get("project_count_min", 1)),
        }

    # ═══ 通用工具 ═══

    def _get_category_weight(self, category: str) -> float:
        weights = self._scoring.get("skill_category_weights", {})
        default = self._scoring.get("skill_category_weight_default", 0.6)
        return weights.get(category, default)

    def _get_level_weight(self, level: str) -> float:
        weights = self._scoring.get("skill_level_weights", {})
        default = self._scoring.get("skill_level_weight_default", 0.8)
        return weights.get(level, default)


def _academic_score(gpa: Optional[float], rank_pct: Optional[float]) -> int:
    """冻结学术评分 (阈值走 YAML: scoring_tables.academic_score)"""
    scoring = get_rules_config().scoring_tables.get("academic_score", {})
    score = 0
    if gpa is not None:
        for threshold in scoring.get("gpa_thresholds", []):
            if gpa >= threshold["min"]:
                score += threshold["score"]
                break
    if rank_pct is not None:
        for threshold in scoring.get("rank_pct_thresholds", []):
            if rank_pct <= threshold["max"]:
                score = max(score, threshold["score"])
                break
    return score


def generate_student_portrait(student_id: str, skills: List[Dict], student_profile: Optional[Dict] = None, target_job_skills: Optional[List[Dict]] = None) -> Dict:
    generator = PortraitGenerator()
    return generator.generate_student_portrait(student_id, skills, student_profile=student_profile, target_job_skills=target_job_skills)


def generate_job_portrait(job_id: str, position_name: str, skills: List[Dict], job_profile: Optional[Dict] = None, jd_count: int = 1) -> Dict:
    generator = PortraitGenerator()
    return generator.generate_job_portrait(job_id, position_name, skills, job_profile=job_profile, jd_count=jd_count)


if __name__ == "__main__":
    import json

    test_skills = [
        {"name": "Python", "category": "编程语言", "confidence": 0.85},
        {"name": "Django", "category": "后端框架", "confidence": 0.75},
        {"name": "MySQL", "category": "数据库", "confidence": 0.8},
        {"name": "Redis", "category": "数据库", "confidence": 0.6},
        {"name": "Docker", "category": "DevOps", "confidence": 0.65},
        {"name": "TensorFlow", "category": "AI/ML", "confidence": 0.5},
    ]

    test_profile = {
        "name": "张三", "degree": "本科", "school": "浙江大学", "major": "计算机科学与技术",
        "gpa": 3.6, "certificates": ["CET-6", "软考中级"], "languages": ["英语"],
        "projects": [{"name": "电商系统", "is_team": True, "role": "负责人"}],
        "internship_months": 6, "student_cadre": True,
        "competitions": [{"level": "省级一等"}],
        "research": [{"level": "EI"}],
        "leadership_roles": [{"role": "项目负责人"}],
        "soft_skills": {"communication": "强", "teamwork": "强"},
    }

    test_job_skills = [
        {"name": "Python", "level": "必须", "weight": 0.9},
        {"name": "Django", "level": "必须", "weight": 0.8},
        {"name": "MySQL", "level": "必须", "weight": 0.85},
        {"name": "Redis", "level": "加分", "weight": 0.5},
        {"name": "Kubernetes", "level": "加分", "weight": 0.4},
    ]

    print("=" * 60)
    print("L2 画像生成测试 — v2.0 冻结 Schema")
    print("=" * 60)

    generator = PortraitGenerator()

    print("\n[学生画像 2A]")
    sp = generator.generate_student_portrait("stu_001", test_skills, student_profile=test_profile, target_job_skills=test_job_skills)
    print(f"  竞争力: {sp['summary']['competitiveness_score']} ({sp['summary']['competitiveness_level']})")
    print(f"  完整度: {sp['summary']['completeness_score']}")
    print(f"  D1(基础): {sp['D1_basic']['dimension_score']}")
    print(f"  D2(技能): {sp['D2_skill']['dimension_score']} coverage={sp['D2_skill']['skill_coverage']}")
    print(f"  D3(素养): {sp['D3_quality']['dimension_score']} confidence={sp['D3_quality']['confidence']}")
    print(f"  D4(潜力): {sp['D4_potential']['dimension_score']}")

    print("\n[岗位画像 2B]")
    jp = generator.generate_job_portrait("job_001", "Python后端开发工程师", test_job_skills, jd_count=3)
    print(f"  岗位: {jp['position_name']} (聚合 {jp['jd_count']} 条JD)")
    print(f"  D1要求: {jp['D1_requirements']}")
    print(f"  D2必须技能: {[s['skill'] for s in jp['D2_requirements']['required_skills']]}")
