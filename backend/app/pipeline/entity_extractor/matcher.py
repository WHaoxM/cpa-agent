"""
人岗匹配模块 (Person-Job Matching)
基于技能的匹配算法

规则来源: backend/config/defaults/rules/ (经 ConfigLoader 加载, workspace 可白名单覆盖)
"""

import re
import uuid
from typing import Dict, List, Optional
from dataclasses import dataclass

from config.loader import get_rules_config


@dataclass
class MatchResult:
    student_id: str
    job_id: str
    total_score: float
    d1_score: float
    d2_score: float
    d3_score: float
    d4_score: Optional[float]
    skill_coverage: float
    matched_skills: List[str]
    missing_skills: List[str]
    level_gap: Dict
    # 匹配判定与证据字段
    match_result: bool = False
    d1_pass: bool = True
    degree_match: bool = False
    major_match: bool = False
    cert_match_rate: float = 0.0
    d1_fail_reason: Optional[str] = None
    exact_matches: Optional[List[Dict]] = None
    semantic_matches: Optional[List[Dict]] = None
    missing_required: Optional[List[str]] = None
    preferred_matched: int = 0
    d3_details: Optional[Dict] = None
    d4_details: Optional[Dict] = None
    dimension_weights: Optional[Dict] = None
    confidence: float = 0.0


class SkillMatcher:
    """技能匹配器"""

    SKILL_LEVELS = {
        "了解": 1,
        "熟悉": 2,
        "掌握": 3,
        "精通": 4
    }

    def __init__(self):
        self._rules = get_rules_config()
        self.weights = self._rules.dimension_weights
        self.DIMENSION_WEIGHTS = self._rules.dimension_weights
        self._semantic_weight = self._rules.semantic_weight
        self._match_gates = self._rules.match_gates
        self._confidence_semantic_factor = self._rules.thresholds["match"]["confidence_semantic_factor"]
        self._confidence_d1_pass_bonus = self._rules.thresholds["match"]["confidence_d1_pass_bonus"]
        # D3 软技能评分参数 (走 thresholds.yaml 的 match.d3 节)
        self._d3_params = self._rules.d3_params
        self._match_level_thresholds = self._rules.match_level_thresholds
        self._normalizer = None

    @property
    def normalizer(self):
        if self._normalizer is None:
            try:
                from app.services.entity_normalizer import get_entity_normalizer
                self._normalizer = get_entity_normalizer()
            except Exception:
                self._normalizer = None
        return self._normalizer

    def _resolve_alias(self, skill_name: str, context: str = "") -> str:
        return self._normalize_entity(skill_name, "skill", context)

    def _normalize_entity(self, name: str, entity_type: str, context: str = "") -> str:
        """
        通用实体归一化 — EntityNormalizer(语义+LLM) 优先, 规则降级

        EntityNormalizer 内部: 精确→别名→BGE-M3语义(cosine>0.85)→LLM OOV分类
        """
        if not name or not name.strip():
            return name

        # ── EntityNormalizer 语义+LLM 归一化 ──
        if self.normalizer:
            try:
                result = self.normalizer.normalize(
                    name, entity_type, context=context or None, auto_register=False
                )
                if result.match_type in ("exact", "alias", "semantic_alias"):
                    return result.canonical
            except Exception:
                pass

        return name

    def _apply_external_rule(self, value: str, rule_domain: str) -> str:
        if not value or not self.normalizer:
            return value
        try:
            return self.normalizer.apply_external_rule(value, rule_domain)
        except Exception:
            return value

    def _rule_normalize_award_level(self, raw_level: str) -> str:
        """奖项级别归一化 — 外部规则表 normalization_rule.award_level"""
        return self._apply_external_rule(raw_level, "award_level")

    def _normalize_degree(self, degree: str) -> str:
        raw = str(degree or "").strip()
        if not raw:
            return ""
        # 知识库优先: EntityNormalizer 查 canonical_entity 表
        kb_result = self._normalize_entity(raw, "degree")
        if kb_result != raw:
            return kb_result
        # 外部规则表降级
        return self._apply_external_rule(raw, "degree")

    def _compact_text(self, text: str) -> str:
        return re.sub(r"[\s\-_/(),，。．（）·]+", "", str(text or "")).upper()

    def _normalize_certificate_name(self, cert_name: str) -> str:
        raw = str(cert_name or "").strip()
        if not raw:
            return ""
        # 知识库优先: EntityNormalizer 查 canonical_entity 表
        normalized = self._normalize_entity(raw, "certificate")
        if normalized != raw:
            return normalized
        # 外部规则表降级
        return self._apply_external_rule(raw, "certificate_alias")

    def _certificates_match(self, left: str, right: str) -> bool:
        left_normalized = self._normalize_certificate_name(left)
        right_normalized = self._normalize_certificate_name(right)
        if not left_normalized or not right_normalized:
            return False
        return (
            left_normalized == right_normalized
            or left_normalized in right_normalized
            or right_normalized in left_normalized
        )

    def calculate_match(
        self,
        student_skills: List[Dict],
        job_skills: List[Dict],
        student_profile: Optional[Dict] = None,
        job_profile: Optional[Dict] = None,
        semantic_matches: Optional[List[str]] = None
    ) -> MatchResult:
        # ── D1 硬约束判定 (R-08) ──
        d1_result = self._calc_d1_with_constraint(student_profile, job_profile)
        d1_score = d1_result["score"]
        d1_pass = d1_result["pass"]
        degree_match = d1_result["degree_match"]
        major_match = d1_result["major_match"]
        cert_match_rate = d1_result["cert_match_rate"]
        d1_fail_reason = d1_result.get("fail_reason")

        # ── D2 技能覆盖 (含语义匹配) ──
        if not job_skills or not student_skills:
            return MatchResult(
                student_id=(student_profile or {}).get("student_id", ""),
                job_id=(job_profile or {}).get("job_id", ""),
                total_score=None, d1_score=d1_score, d2_score=None,
                d3_score=self._calc_d3_score(student_profile), d4_score=None,
                skill_coverage=0, matched_skills=[], missing_skills=[], level_gap={},
                match_result=False, d1_pass=d1_pass, degree_match=degree_match,
                major_match=major_match, cert_match_rate=cert_match_rate,
                d1_fail_reason="missing_skill_evidence", dimension_weights=self.DIMENSION_WEIGHTS,
            )

        # ── 别名展开 ──
        student_skill_map = {s["name"]: s for s in student_skills}
        job_skill_map = {j["name"]: j for j in job_skills}

        expanded_student_map: Dict[str, Dict] = {}
        for name, skill in student_skill_map.items():
            expanded_student_map[name] = skill
            resolved = self._resolve_alias(name)
            if resolved != name and resolved not in expanded_student_map:
                skill_copy = dict(skill)
                skill_copy["name"] = resolved
                expanded_student_map[resolved] = skill_copy

        expanded_job_map: Dict[str, Dict] = {}
        for name, skill in job_skill_map.items():
            expanded_job_map[name] = skill
            resolved = self._resolve_alias(name)
            if resolved != name and resolved not in expanded_job_map:
                skill_copy = dict(skill)
                skill_copy["name"] = resolved
                expanded_job_map[resolved] = skill_copy

        # ── 精确匹配 ──
        exact_matches = []
        missing = []
        missing_required = []
        level_gaps = {}
        seen_matched: set = set()

        for job_skill_name, job_skill in expanded_job_map.items():
            job_level = job_skill.get("level", "必须")
            if job_skill_name in expanded_student_map:
                seen_matched.add(job_skill_name)
                student_level = expanded_student_map[job_skill_name].get("level", "了解")

                exact_matches.append({
                    "student_skill": job_skill_name,
                    "job_skill": job_skill_name,
                })

                student_level_score = self.SKILL_LEVELS.get(student_level, 1)
                job_level_score = 4 if job_level in ("必须", "精通") else (3 if job_level == "优先" else 2)
                gap = max(0, job_level_score - student_level_score)
                level_gaps[job_skill_name] = gap
            else:
                missing.append(job_skill_name)
                if job_level in ("必须", "精通"):
                    missing_required.append(job_skill_name)

        # ── 语义匹配 (BGE-M3 cosine > 0.85) ──
        sem_matches = []
        if semantic_matches:
            for sm in semantic_matches:
                if sm not in seen_matched and sm in expanded_job_map:
                    sem_matches.append({
                        "student_skill": sm,
                        "job_skill": sm,
                        "similarity": 0.87,
                    })
                    seen_matched.add(sm)

        # 优先匹配计数
        preferred_matched = sum(
            1 for s in seen_matched
            if expanded_job_map.get(s, {}).get("level") == "优先"
        )

        # ── skill_coverage (冻结公式: exact + semantic×semantic_weight) ──
        exact_count = len(exact_matches)
        semantic_count = len(sem_matches)
        required_count = len(job_skill_map)
        skill_coverage = (exact_count + semantic_count * self._semantic_weight) / required_count if required_count else 0

        d2_score = skill_coverage * 100
        avg_gap = sum(level_gaps.values()) / len(level_gaps) if level_gaps else 0
        level_factor = 1 - (avg_gap / 4) * 0.3
        d2_adjusted = d2_score * max(0.7, level_factor)

        # ── D3 职业素养 ──
        d3_result = self._calc_d3_detailed(student_profile)
        d3_score = d3_result["score"]

        # ── D4 发展潜力 ──
        d4_result = self._calc_d4_detailed(student_skills, student_profile)
        d4_score = d4_result["score"]

        # ── 总分 (冻结公式) ──
        total_score = (
            d1_score * self.DIMENSION_WEIGHTS["D1"] +
            d2_adjusted * self.DIMENSION_WEIGHTS["D2"] +
            d3_score * self.DIMENSION_WEIGHTS["D3"] +
            d4_score * self.DIMENSION_WEIGHTS["D4"]
        ) if d3_score is not None and d4_score is not None else None

        # ── 匹配判定 (R-08) ──
        # D1硬约束不通过 → match_result=false
        # D1通过 AND skill_coverage≥min_skill_coverage AND total_score≥min_total_score → match_result=true
        min_cov = self._match_gates["min_skill_coverage"]
        min_score = self._match_gates["min_total_score"]
        if not d1_pass or total_score is None:
            match_result_bool = False
        elif skill_coverage >= min_cov and total_score >= min_score:
            match_result_bool = True
        else:
            match_result_bool = False

        # 置信度 (规则来源: thresholds.match)
        confidence = min(1.0, skill_coverage * self._confidence_semantic_factor
                          + (self._confidence_d1_pass_bonus if d1_pass else 0.0))

        return MatchResult(
            student_id=(student_profile or {}).get("student_id", student_skills[0].get("student_id", "")) if student_skills else "",
            job_id=(job_profile or {}).get("job_id", job_skills[0].get("job_id", "")) if job_skills else "",
            total_score=round(total_score, 1) if total_score is not None else None,
            d1_score=round(d1_score, 1),
            d2_score=round(d2_adjusted, 1),
            d3_score=round(d3_score, 1) if d3_score is not None else None,
            d4_score=round(d4_score, 1) if d4_score is not None else None,
            skill_coverage=round(skill_coverage, 2),
            matched_skills=list(seen_matched),
            missing_skills=missing,
            level_gap=level_gaps,
            match_result=match_result_bool,
            d1_pass=d1_pass,
            degree_match=degree_match,
            major_match=major_match,
            cert_match_rate=cert_match_rate,
            d1_fail_reason=d1_fail_reason,
            exact_matches=exact_matches,
            semantic_matches=sem_matches,
            missing_required=missing_required,
            preferred_matched=preferred_matched,
            d3_details=d3_result.get("details"),
            d4_details=d4_result.get("details"),
            dimension_weights=self.DIMENSION_WEIGHTS,
            confidence=round(confidence, 2),
        )

    def _calc_d1_with_constraint(self, student_profile: Optional[Dict], job_profile: Optional[Dict]) -> Dict:
        """D1 硬约束判定 (冻结 Schema R-08)"""
        if not student_profile:
            return {
                "score": 60.0, "pass": True,
                "degree_match": False, "major_match": False,
                "cert_match_rate": 0.0, "fail_reason": "无学生档案数据"
            }

        score = 60.0
        degree = self._normalize_degree(student_profile.get("degree", ""))
        required_degree = self._normalize_degree((job_profile or {}).get("degree_requirement", ""))
        degree_rank = {"专科": 1, "本科": 2, "硕士": 3, "博士": 4}
        student_rank = degree_rank.get(degree, 0)
        required_rank = degree_rank.get(required_degree, 0)

        # 学历匹配
        degree_match = False
        if required_rank > 0 and student_rank >= required_rank:
            degree_match = True
            score += 20
        elif required_rank > 0 and student_rank > 0:
            score += max(0, 20 - (required_rank - student_rank) * 5)
        elif required_rank == 0:
            degree_match = True  # 无要求视为通过

        # 专业匹配
        major_match = False
        required_majors = (job_profile or {}).get("major_requirements", [])
        student_major = student_profile.get("major", "")
        major_strict = (job_profile or {}).get("major_strict", True)
        if not required_majors:
            major_match = True
        elif student_major:
            for rm in required_majors:
                if rm == student_major:
                    major_match = True
                    break
            if not major_match and not major_strict:
                major_match = True  # 非严格模式下视为通过

        if major_match:
            score += 10
        if student_profile.get("school"):
            score += 10

        student_certs = [c.get("name", "") if isinstance(c, dict) else c
                         for c in student_profile.get("certificates", [])]
        required_certs = (job_profile or {}).get("required_certs", [])
        cert_match_rate = 0.0
        if required_certs:
            matched_certs = sum(1 for rc in required_certs
                                if any(self._certificates_match(rc, sc) for sc in student_certs))
            cert_match_rate = matched_certs / len(required_certs)
            score += cert_match_rate * 15
        else:
            cert_match_rate = 1.0  # 无要求视为通过

        score = min(score, 100)

        # ── 硬约束判定 (R-08) ──
        # 学历≥要求 AND (专业匹配 OR major_strict=false)
        d1_pass = degree_match and (major_match or not major_strict)
        fail_reason = None
        if not d1_pass:
            reasons = []
            if not degree_match:
                reasons.append(f"学历不满足(需{required_degree}, 实际{degree})")
            if not major_match and major_strict:
                reasons.append(f"专业不匹配(需{required_majors})")
            fail_reason = "; ".join(reasons)

        return {
            "score": score, "pass": d1_pass,
            "degree_match": degree_match, "major_match": major_match,
            "cert_match_rate": round(cert_match_rate, 2),
            "fail_reason": fail_reason,
        }

    def _calc_d3_detailed(self, student_profile: Optional[Dict]) -> Dict:
        from .resume_structure import confirmed_semantic_fact
        profile = student_profile or {}
        dimensions = ("communication", "teamwork", "stress_tolerance", "responsibility")
        levels = self._d3_params.get("level_map", {})
        details = {key: levels.get(confirmed_semantic_fact(profile, key)) for key in dimensions}
        missing = [key for key, value in details.items() if value is None]
        if missing:
            return {"score": None, "details": details, "status": "partial", "missing_fields": missing}
        bonuses = self._d3_params.get("rule_bonus", {})
        bonus = sum(bonuses.get(key, 0) for key in ("internship", "team_project", "leader", "student_officer")
                    if confirmed_semantic_fact(profile, key) is True)
        formula = self._d3_params.get("score_formula", {})
        score = min(sum(details.values()) / 4 * formula.get("detail_weight", 70)
                    + bonus + formula.get("base_score", 20), formula.get("score_cap", 100))
        return {"score": round(score, 1), "details": details, "status": "completed"}

    def _calc_d4_detailed(self, student_skills: List[Dict], student_profile: Optional[Dict] = None) -> Dict:
        # Reuse the canonical portrait policy instead of keyword classification.
        from .portrait_generator import PortraitGenerator
        value = PortraitGenerator()._calc_d4_potential(student_skills, student_profile)
        return {"score": value["dimension_score"], "details": value}

    def _calc_d1_score(self, student_profile: Optional[Dict], job_profile: Optional[Dict]) -> float:
        if not student_profile:
            return 60.0
        score = 60.0
        degree = self._normalize_degree(student_profile.get("degree", ""))
        required_degree = self._normalize_degree((job_profile or {}).get("degree_requirement", ""))
        degree_rank = {"专科": 1, "本科": 2, "硕士": 3, "博士": 4}
        student_rank = degree_rank.get(degree, 0)
        required_rank = degree_rank.get(required_degree, 0)
        if required_rank > 0 and student_rank >= required_rank:
            score += 20
        elif required_rank > 0 and student_rank > 0:
            score += max(0, 20 - (required_rank - student_rank) * 5)
        if student_profile.get("school"):
            score += 10
        if student_profile.get("major"):
            score += 10
        return min(score, 100)

    def _calc_d3_score(self, student_profile: Optional[Dict]) -> float:
        if not student_profile:
            return 50.0
        score = 50.0
        experience = student_profile.get("experience", [])
        valid_experience = [e for e in experience if e.get("description", "").strip()]
        valid_projects = [p for p in student_profile.get("projects", []) if p.get("description", "").strip()]
        if valid_experience:
            score += min(len(valid_experience) * 8, 25)
        if valid_projects:
            score += min(len(valid_projects) * 5, 15)
        leadership_keywords = ["负责", "主导", "管理", "带领", "组织"]
        all_text = " ".join([e.get("description", "") for e in valid_experience] + [p.get("description", "") for p in valid_projects])
        keyword_count = sum(1 for kw in leadership_keywords if kw in all_text)
        score += min(keyword_count * 3, 10)
        return min(score, 100)

    def _calc_d4_score(self, student_skills: List[Dict]) -> float:
        if not student_skills:
            return 20.0
        categories = set(s.get("category", "") for s in student_skills)
        emerging_cats = {"AI/ML", "大数据", "云计算", "云原生"}
        foundation_count = sum(1 for s in student_skills if s.get("category") == "编程语言")
        emerging_count = sum(1 for s in student_skills if s.get("category") in emerging_cats)
        cross_category_score = min(len(categories) * 8, 32)
        foundation_score = min(foundation_count * 10, 28)
        emerging_score = min(emerging_count * 12, 20)
        base_score = 20
        return min(cross_category_score + foundation_score + emerging_score + base_score, 100)

    def get_gap_skills_priority(
        self,
        missing_skills: List[str],
        level_gaps: Dict
    ) -> List[Dict]:
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
    job_skills: List[Dict],
    student_profile: Optional[Dict] = None,
    job_profile: Optional[Dict] = None
) -> Dict:
    matcher = SkillMatcher()
    result = matcher.calculate_match(student_skills, job_skills, student_profile, job_profile)

    return {
        "status": "partial" if result.total_score is None else "completed",
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
        "match_level": _get_match_level(result.total_score),
        # 匹配判定与证据字段
        "match_result": result.match_result,
        "d1_pass": result.d1_pass,
        "degree_match": result.degree_match,
        "major_match": result.major_match,
        "cert_match_rate": result.cert_match_rate,
        "d1_fail_reason": result.d1_fail_reason,
        "exact_matches": result.exact_matches,
        "semantic_matches": result.semantic_matches,
        "missing_required": result.missing_required,
        "preferred_matched": result.preferred_matched,
        "d3_details": result.d3_details,
        "d4_details": result.d4_details,
        "dimension_weights": result.dimension_weights,
        "confidence": result.confidence,
    }


def _get_match_level(score: float) -> str:
    """match_level 阈值走 thresholds.yaml (统一权威源)"""
    if score is None:
        return "unknown"
    thresholds = get_rules_config().match_level_thresholds
    if score >= thresholds.get("excellent", 85):
        return "极佳"
    elif score >= thresholds.get("good", 70):
        return "良好"
    elif score >= thresholds.get("fair", 60):
        return "一般"
    else:
        return "不匹配"


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
