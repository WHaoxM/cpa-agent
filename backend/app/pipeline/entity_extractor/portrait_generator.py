"""
四维画像生成模块
生成学生画像和岗位画像

计算规则:
- 评分公式: competitiveness_score = D1×0.25 + D2×0.35 + D3×0.20 + D4×0.20
- D2.skill_coverage = (exact_count + semantic_count×0.8) / required_count
- 语义匹配阈值: BGE-M3 cosine > 0.85
- 竞争力级别: ≥85极强 / ≥70强 / ≥55中等 / ≥40偏弱 / <40弱

评分参数由 config.loader.get_rules_config() 加载。
"""

import math
import hashlib
import json
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
    """生成学生画像和岗位画像。"""

    def __init__(self):
        self._rules = get_rules_config()
        self.weights = self._rules.dimension_weights
        self._scoring = self._rules.scoring_tables

    @staticmethod
    def _assessment_digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")).encode("utf-8")).hexdigest()

    def _assessment_evidence_bank(self, source_text, sources):
        """Freeze IDs against this source snapshot; never infer content with keywords."""
        if not source_text.strip() or not sources:
            raise ValueError("assessment_sources_required")
        bank, seen = {}, set()
        source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        for source in sources:
            if (not isinstance(source, dict) or not isinstance(source.get("source_id"), str)
                    or not source["source_id"] or not isinstance(source.get("block_id"), str)
                    or not source["block_id"] or not isinstance(source.get("text"), str)
                    or not source["text"].strip() or source.get("source_kind") not in ("resume", "official_school")):
                raise ValueError("invalid_assessment_source")
            page = source.get("page")
            if (source["source_kind"] == "resume" and (type(page) is not int or page < 1)) or (
                    source["source_kind"] == "official_school" and page is not None):
                raise ValueError("invalid_assessment_source_page")
            key = (source["source_id"], page, source["block_id"])
            if key in seen:
                raise ValueError("duplicate_assessment_source_block")
            seen.add(key)
            if source["source_kind"] == "resume" and source["text"] not in source_text:
                raise ValueError("assessment_block_not_in_resume_source")
            evidence_id = "ev_" + self._assessment_digest({"resume_sha256": source_digest, "block": source})[:24]
            bank[evidence_id] = {"evidence_id": evidence_id, "source_id": source["source_id"],
                "source_kind": source["source_kind"], "page": page, "block_id": source["block_id"],
                "quote": source["text"], "quote_sha256": hashlib.sha256(source["text"].encode("utf-8")).hexdigest()}
        return bank

    def propose_behavior_assessment(self, client, *, source_text: str, sources: List[Dict]) -> Dict:
        """Read-only structured proposal. Sources are owner-checked page/official snapshots.

        No confirmation or profile mutation occurs here. The caller must supply complete
        source snapshots; LLMClient's dispatch budget rejects over-window input rather
        than truncating pages. External snapshots must be fetched/verified by the caller.
        """
        from .portrait_assessment_schema import PortraitAssessmentProposal, PortraitAssessmentSelection
        rubric = self._scoring["portrait_assessment"]
        try:
            bank = self._assessment_evidence_bank(source_text, sources)
            response = client.chat_with_metadata(
                messages=[{"role": "system", "content": rubric["instructions"]},
                          {"role": "user", "content": json.dumps({"evidence_blocks": list(bank.values())}, ensure_ascii=False)}],
                temperature=0.1, max_tokens=5000,
                response_format={"type": "json_schema", "json_schema": {
                    "name": "portrait_behavior_assessment", "strict": True,
                    "schema": PortraitAssessmentSelection.model_json_schema()}},
            )
            if response.finish_reason != "stop":
                raise ValueError("incomplete_assessment_response")
            proposal = PortraitAssessmentSelection.model_validate_json(response.text)
            data = proposal.model_dump()

            def hydrate(node, field):
                if isinstance(node, dict):
                    if "evidence_id" in node:
                        evidence = bank.get(node["evidence_id"])
                        if not evidence:
                            raise ValueError("assessment_evidence_id_not_in_snapshot")
                        if field != "school_type" and evidence["source_kind"] != "resume":
                            raise ValueError("behavior_evidence_must_belong_to_resume")
                        return {key: value for key, value in evidence.items() if key != "source_kind"}
                    return {key: hydrate(value, field) for key, value in node.items()}
                if isinstance(node, list):
                    return [hydrate(value, field) for value in node]
                return node

            data = {field: hydrate(value, field) for field, value in data.items()}
            data = PortraitAssessmentProposal.model_validate(data).model_dump()
            stress = data["stress_tolerance"]
            if stress["level"] is not None and not all(stress[k] for k in ("situation", "action", "outcome")):
                raise ValueError("stress_assessment_requires_situation_action_outcome")
            if stress["level"] is not None:
                selected = {item["evidence_id"] for item in stress["evidence"]}
                if any(stress[key]["evidence_id"] not in selected for key in ("situation", "action", "outcome")):
                    raise ValueError("stress_context_must_be_selected_evidence")
            payload = {"status": "candidate", "assessment": data, "provenance": {
                "kind": "model_assessment", "provider": response.provider, "model": response.model,
                "rubric_version": rubric["rubric_version"],
                "source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                "sources_sha256": self._assessment_digest(sources),
            }}
            return {**payload, "candidate_sha256": self._assessment_digest(payload)}
        except Exception as exc:
            # Do not leak provider messages or private source fragments in public diagnostics.
            public_reasons = {"assessment_sources_required", "invalid_assessment_source",
                              "invalid_assessment_source_page", "duplicate_assessment_source_block",
                              "assessment_block_not_in_resume_source", "assessment_evidence_id_not_in_snapshot",
                              "behavior_evidence_must_belong_to_resume", "incomplete_assessment_response",
                              "stress_context_must_be_selected_evidence",
                              "stress_assessment_requires_situation_action_outcome"}
            reason = str(exc) if str(exc) in public_reasons else "invalid_schema_or_provider_failure"
            return {"status": "partial", "error": "assessment_candidate_rejected",
                    "error_type": type(exc).__name__, "reason": reason,
                    "next_action": "retry_or_review_assessment_sources"}

    def _assessment_state(self, profile):
        """Validate candidate identity; approval must come from a trusted server snapshot.

        Like _resume_confirmation, this is not an API authentication boundary. Never
        accept _portrait_assessment_confirmation from an arbitrary client profile.
        """
        from .portrait_assessment_schema import PortraitAssessmentCandidate
        from pydantic import ValidationError
        raw = profile.get("portrait_assessment")
        if not raw:
            return None, False
        try:
            candidate = PortraitAssessmentCandidate.model_validate(raw).model_dump()
        except (ValidationError, TypeError):
            return None, False
        digest = candidate.pop("candidate_sha256")
        provenance = candidate["provenance"]
        if (digest != self._assessment_digest(candidate)
                or provenance["source_sha256"] != profile.get("source_sha256")
                or provenance["rubric_version"] != self._scoring["portrait_assessment"]["rubric_version"]):
            return None, False
        candidate["candidate_sha256"] = digest
        approval = profile.get("_portrait_assessment_confirmation") or {}
        source_confirmation = profile.get("_resume_confirmation") or {}
        if not isinstance(approval, dict) or not isinstance(source_confirmation, dict):
            return candidate, False
        source_task = source_confirmation.get("task_id")
        approved = bool(source_task and approval.get("source_task_id") == source_task
                        and approval.get("candidate_sha256") == digest
                        and approval.get("confirmed_by") and approval.get("confirmed_at"))
        return candidate, approved

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

        known = all(d["dimension_score"] is not None for d in (d1, d2, d3, d4))
        comp_score = (
            d1["dimension_score"] * self.weights["D1"] +
            d2["dimension_score"] * self.weights["D2"] +
            d3["dimension_score"] * self.weights["D3"] +
            d4["dimension_score"] * self.weights["D4"]
        ) if known else None
        completeness = self._calc_completeness(d1, d2, d3, d4)

        return {
            "status": "completed" if known else "partial",
            "missing_fields": d1.get("missing_fields", []) + d3.get("missing_fields", []),
            "student_id": student_id,
            "name": (student_profile or {}).get("name", ""),
            "D1_basic": d1,
            "D2_skill": d2,
            "D3_quality": d3,
            "D4_potential": d4,
            "summary": {
                "dimension_weights": self.weights,
                "competitiveness_score": round(comp_score, 1) if known else None,
                "competitiveness_level": competitiveness_level(comp_score, self._rules) if known else "unknown",
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
        # Categories are supplied as source-backed confirmed facts, never guessed
        # from a school/major name or from its apparent subject family.
        from .resume_structure import confirmed_semantic_fact
        school_tier = confirmed_semantic_fact(profile, "school_tier")
        assessment, assessed = self._assessment_state(profile)
        school_type = assessment["assessment"]["school_type"] if assessment else None
        major_category = confirmed_semantic_fact(profile, "major_category")
        sub_school = self._score_school(school_tier)
        sub_major = self._score_major(major_category)
        missing = [k for k, v in (("school_tier", sub_school), ("major_category", sub_major)) if v is None]
        sub_cert = min(len(certs) * 7.5, self._scoring["d1_sub_max"]["sub_cert"]) if certs else 0
        sub_language = min(len(languages) * 7.5, self._scoring["d1_sub_max"]["sub_language"]) if languages else 0

        dimension_score = min(sub_degree + sub_school + sub_major + sub_cert + sub_language, 100) if not missing else None

        evidence = []
        if degree: evidence.append(f"学历: {degree}")
        if school: evidence.append(f"学校: {school}")
        if major: evidence.append(f"专业: {major}")
        if certs: evidence.append(f"证书: {', '.join(str(c) for c in certs[:5])}")
        if languages: evidence.append(f"语言: {', '.join(str(l) for l in languages[:5])}")

        return {
            "degree": degree,
            "status": "partial" if missing else "completed",
            "missing_fields": missing,
            "school_tier": school_tier,
            "school_type": school_type,
            "school_type_provenance": assessment["provenance"] if school_type else None,
            "school_type_status": ("reviewed_observation" if assessed else "candidate") if school_type else "unknown",
            "school_score_basis": "confirmed_school_tier" if sub_school is not None else "no_tier_mapping",
            "major_category": major_category,
            "sub_degree": sub_degree,
            "sub_school": sub_school,
            "sub_major": sub_major,
            "sub_cert": sub_cert,
            "sub_language": sub_language,
            "dimension_score": round(dimension_score, 1) if dimension_score is not None else None,
            "evidence": evidence,
        }

    def _score_school(self, category):
        return self._scoring["school_tier_score"].get(category) if category else None

    def _classify_school(self, category):
        return category if category and category in self._scoring["school_tier_score"] else None

    def _score_major(self, category):
        return self._scoring["major_category_score"].get(category) if category else None

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
        from .resume_structure import confirmed_semantic_fact
        profile = profile or {}
        dimensions = ("communication", "teamwork", "stress_tolerance", "responsibility")
        values = {key: self._score_quality_item(key, confirmed_semantic_fact(profile, key), [])
                  for key in dimensions}
        candidate, approved = self._assessment_state(profile)
        for key in dimensions:
            values[key]["provenance"] = {"kind": "confirmed_self_description"} if values[key]["score"] is not None else None
            if candidate:
                assessment = candidate["assessment"][key]
                values[key]["assessment_candidate"] = assessment
                values[key]["assessment_status"] = "reviewed" if approved else "awaiting_confirmation"
                if approved and values[key]["score"] is None:
                    has_stress_context = key != "stress_tolerance" or all(
                        assessment[k] for k in ("situation", "action", "outcome"))
                    if assessment["level"] is not None and has_stress_context:
                        values[key].update(self._score_quality_item(key, assessment["level"], []))
                        values[key]["evidence"] = assessment["evidence"]
                        values[key]["confidence"] = assessment["confidence"]
                        values[key]["provenance"] = {**candidate["provenance"],
                                                    "candidate_sha256": candidate["candidate_sha256"]}
        missing = [key for key, value in values.items() if value["score"] is None]
        # Existing deterministic bonus policy, driven by explicit confirmed facts.
        bonus = 0
        for key, amount in (("internship", 5), ("team_project", 5), ("leader", 8), ("student_officer", 3)):
            if confirmed_semantic_fact(profile, key) is True:
                bonus += amount
        return {**values, "rule_bonus": bonus,
                "dimension_score": min(sum(v["score"] for v in values.values()) + bonus, 100) if not missing else None,
                "status": "partial" if missing else "completed", "missing_fields": missing,
                "confidence": None}

    def _score_quality_item(self, item_name, explicit_value, related_data):
        table = self._scoring.get("d3_quality_level_score", {})
        score = table.get(explicit_value) if isinstance(explicit_value, str) else None
        return {"level": explicit_value if score is not None else "unknown",
                "score": score, "evidence": "confirmed_semantic_fact" if score is not None else None}

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
            if (d1.get(k) or 0) > 0: filled_fields += 1
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
            if q.get("score") is not None: filled_fields += 1

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
