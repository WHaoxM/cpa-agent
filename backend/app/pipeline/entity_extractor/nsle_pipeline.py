"""
NS-LE Pipeline 封装 (迁移自 mvp)
使用 DeepSeek API 替代 Qwen3-0.6B

Phase 1: 物理解析层 (保留)
Phase 2: 符号化隐私层 (保留)
Phase 3: DeepSeek LLM 推理核 (替代 Qwen3)
Phase 4: 逻辑守卫 (简化)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from config.prompt_loader import load_prompt

logger = logging.getLogger(__name__)


@dataclass
class DocumentBlock:
    block_id: str
    block_type: str
    text: str
    confidence: float
    source: Dict[str, Any]
    heading: str = ""


class BlockType:
    PERSONAL_INFO = "PERSONAL_INFO"
    EDUCATION = "EDUCATION"
    WORK_EXPERIENCE = "WORK_EXPERIENCE"
    PROJECT_EXPERIENCE = "PROJECT_EXPERIENCE"
    SKILLS_CERTS = "SKILLS_CERTS"
    JOB_TITLE = "JOB_TITLE"
    JOB_REQUIREMENTS = "JOB_REQUIREMENTS"
    JOB_DESCRIPTION = "JOB_DESCRIPTION"
    OTHER = "OTHER"


class NSLEPipeline:
    """
    NS-LE 架构完整管道 (DeepSeek 版本)

    使用方式:
        pipeline = NSLEPipeline()
        result = pipeline.run_resume_text("简历文本...")
        result = pipeline.run_jd_text("JD文本...")
    """

    def __init__(self, use_deepseek: bool = True):
        self.use_deepseek = use_deepseek
        self._llm_client = None

    @property
    def llm_client(self):
        if self._llm_client is None:
            from app.utils.llm_client import get_llm_client
            self._llm_client = get_llm_client()
        return self._llm_client

    @staticmethod
    def _extract_json_from_response(content: str) -> Optional[Any]:
        """从 LLM 响应中稳健提取第一个有效 JSON 对象或数组"""
        content = content.strip()
        try:
            return json.loads(content)
        except (json.JSONDecodeError, ValueError):
            pass
        for i, ch in enumerate(content):
            if ch in ('{', '['):
                closing = '}' if ch == '{' else ']'
                for j in range(len(content), i, -1):
                    if content[j - 1] == closing:
                        try:
                            return json.loads(content[i:j])
                        except (json.JSONDecodeError, ValueError):
                            continue
        return None

    def run_resume_text(self, text: str, *, link_entities: bool = True) -> Dict[str, Any]:
        """
        运行简历解析完整管道

        Args:
            text: 简历文本

        Returns:
            解析结果
        """
        try:
            blocks = self._phase1_parse(text, doc_type="resume")

            from .resume_structure import extract_canonical_profile, validate_structure_truth
            if not self.use_deepseek:
                raise ValueError("semantic_extractor_required")
            profile = extract_canonical_profile(self.llm_client, text, link_entities=link_entities)
            validation = validate_structure_truth(profile)
            return {
                "success": validation["ok"],
                "status": "needs_input" if validation["ok"] else "partial",
                "blocks": blocks, "entities": {},
                "skills": profile.get("skills", []), "student_profile": profile,
                "structure_validation": validation,
                "error": None if validation["ok"] else "structure_incomplete",
            }

        except Exception as e:
            logger.error(f"简历解析失败: {e}")
            return {"success": False, "error": str(e), "blocks": [], "entities": {}}

    def _parse_agent_loop_result(self, content: str) -> Dict[str, Any]:
        """解析Agent Loop返回的结果"""
        import json
        import re

        if not content:
            return {}

        # 尝试从内容中提取JSON
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                # 使用normalize_profile处理
                from .resume_structure import _normalize_profile
                return _normalize_profile(data)
            except json.JSONDecodeError:
                pass

        return {}

    def run_jd_text(self, text: str) -> Dict[str, Any]:
        """
        运行 JD 解析完整管道

        Args:
            text: JD 文本

        Returns:
            解析结果
        """
        try:
            blocks = self._phase1_parse(text, doc_type="jd")

            filtered_blocks = self._phase2_filter(blocks)

            entities = self._phase3_extract(filtered_blocks, doc_type="jd")

            result = self._phase4_guard(entities)

            return {
                "success": True,
                "blocks": filtered_blocks,
                "entities": result,
                "required_skills": self._extract_skills_from_entities(entities)
            }

        except Exception as e:
            logger.error(f"JD解析失败: {e}")
            return {"success": False, "error": str(e), "blocks": [], "entities": {}}

    def _phase1_parse(self, text: str, doc_type: str) -> List[DocumentBlock]:
        """
        Phase 1: 物理解析层
        文本分段 + Block 分类
        """
        # Physical text only. Heading meaning belongs to the schema extractor.
        return [DocumentBlock("b1", BlockType.OTHER, text, 1.0,
                              {"doc_type": doc_type, "start": 0, "end": len(text)})] if text else []

    def _phase2_filter(self, blocks: List[DocumentBlock]) -> List[DocumentBlock]:
        """
        Phase 2: 符号化隐私层
        过滤敏感信息 + 格式化
        """
        import re

        pii_patterns = [
            (r'\d{11,}', '[PHONE]'),
            (r'\d{3,4}[- ]?\d{3,4}[- ]?\d{3,4}', '[PHONE]'),
            (r'[\w.-]+@[\w.-]+\.\w+', '[EMAIL]'),
            (r'\d{17}[\dXx]', '[ID]'),
        ]

        filtered = []
        for block in blocks:
            text = block.text
            for pattern, replacement in pii_patterns:
                text = re.sub(pattern, replacement, text)

            text = re.sub(r'\s+', ' ', text).strip()

            if text:
                filtered.append(DocumentBlock(
                    block_id=block.block_id,
                    block_type=block.block_type,
                    text=text,
                    confidence=block.confidence,
                    source=block.source,
                    heading=block.heading
                ))

        return filtered

    def _phase3_extract(self, blocks: List[DocumentBlock], doc_type: str) -> Dict[str, Any]:
        """
        Phase 3: DeepSeek LLM 推理核
        替代 Qwen3-0.6B SFT
        """
        if not self.use_deepseek:
            raise ValueError("semantic_extractor_required")
        all_text = "\n".join(b.text for b in blocks)
        return {"skills": self._extract_skills_with_llm(all_text),
                "experience": self._extract_experience_with_llm(all_text),
                "education": self._extract_education_with_llm(all_text),
                "certificates": self._extract_certificates_with_llm(all_text)}

    def _extract_basic_info_with_llm(self, text: str) -> Dict[str, Any]:
        if not text or not text.strip():
            return {}
        prompt = load_prompt("pipeline.nsle.extract.basic_info.user", text=text)
        try:
            response = self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=400,
            )
            data = self._extract_json_from_response(str(response))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"LLM基本信息抽取失败: {e}")
            return {}

    def _extract_awards_with_llm(self, text: str) -> List[Dict]:
        if not text or not text.strip():
            return []
        prompt = load_prompt("pipeline.nsle.extract.awards.user", text=text)
        try:
            response = self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=600,
            )
            data = self._extract_json_from_response(str(response))
            if isinstance(data, dict):
                awards = data.get("awards", [])
                return awards if isinstance(awards, list) else []
            if isinstance(data, list):
                return data
            return []
        except Exception as e:
            logger.warning(f"LLM奖项抽取失败: {e}")
            return []

    def _extract_skills_with_llm(self, text: str) -> List[str]:
        """使用 LLM 抽取技能"""
        prompt = load_prompt("pipeline.nsle.extract.skills.user", text=text)

        try:
            response = self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500
            )
            content = str(response)
            data = self._extract_json_from_response(content)
            if isinstance(data, dict):
                skills = data.get("skills", [])
                return skills if isinstance(skills, list) else []
            elif isinstance(data, list):
                return data
            raise ValueError("invalid_skill_schema")
        except Exception:
            raise

    def _extract_experience_with_llm(self, text: str) -> List[Dict]:
        """使用 LLM 抽取工作经历"""
        if not text or not text.strip():
            return []

        prompt = load_prompt("pipeline.nsle.extract.experience.user", text=text)

        try:
            response = self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=800
            )
            content = str(response).strip()
            data = self._extract_json_from_response(content)
            if isinstance(data, dict):
                return data.get("experiences", [])
            elif isinstance(data, list):
                return data
            return []
        except Exception as e:
            logger.warning(f"LLM工作经历抽取失败: {e}")
            return []

    def _extract_education_with_llm(self, text: str) -> List[Dict]:
        """使用 LLM 抽取教育经历"""
        if not text or not text.strip():
            return []

        prompt = load_prompt("pipeline.nsle.extract.education.user", text=text)

        try:
            response = self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=600
            )
            content = str(response).strip()
            data = self._extract_json_from_response(content)
            if isinstance(data, dict):
                return data.get("education", [])
            elif isinstance(data, list):
                return data
            return []
        except Exception as e:
            logger.warning(f"LLM教育经历抽取失败: {e}")
            return []

    def _extract_certificates_with_llm(self, text: str) -> List[Dict]:
        """使用 LLM 抽取证书"""
        if not text or not text.strip():
            return []

        prompt = load_prompt("pipeline.nsle.extract.certificates.user", text=text)

        try:
            response = self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=400
            )
            content = str(response).strip()
            data = self._extract_json_from_response(content)
            if isinstance(data, dict):
                return data.get("certificates", [])
            elif isinstance(data, list):
                return data
            return []
        except Exception as e:
            logger.warning(f"LLM证书抽取失败: {e}")
            return []

    def _phase4_guard(self, entities: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 4: 逻辑守卫
        验证 + 格式化输出
        """
        validated = {}

        if "skills" in entities:
            skills = entities["skills"]
            if isinstance(skills, list):
                seen = set()
                unique_skills = []
                for s in skills:
                    if s not in seen:
                        seen.add(s)
                        unique_skills.append(s)
                validated["skills"] = unique_skills
            else:
                validated["skills"] = []
        else:
            validated["skills"] = []

        validated["experience"] = entities.get("experience", [])

        # ── 知识库驱动归一化 (degree + certificate) ──
        # 数据来源: MySQL canonical_entity 表 (Agent/Admin 维护)
        # 无 MySQL 时优雅降级: 保留 LLM 原始抽取结果
        normalizer = self._get_normalizer()

        raw_edu = entities.get("education", [])
        if isinstance(raw_edu, list) and normalizer:
            for edu in raw_edu:
                if isinstance(edu, dict) and edu.get("degree"):
                    result = normalizer.normalize(
                        str(edu["degree"]).strip(), "degree", auto_register=False
                    )
                    if result.match_type in ("exact", "alias", "semantic_alias"):
                        edu["degree"] = result.canonical
        validated["education"] = raw_edu

        raw_certs = entities.get("certificates", [])
        if isinstance(raw_certs, list):
            seen_certs = set()
            unique_certs = []
            for c in raw_certs:
                name = c.get("name", "") if isinstance(c, dict) else str(c)
                name = name.strip()
                if name and normalizer:
                    result = normalizer.normalize(name, "certificate", auto_register=False)
                    if result.match_type in ("exact", "alias", "semantic_alias"):
                        name = result.canonical
                if name and name not in seen_certs:
                    seen_certs.add(name)
                    unique_certs.append({"name": name})
            validated["certificates"] = unique_certs
        else:
            validated["certificates"] = []

        return validated

    def _get_normalizer(self):
        """获取 EntityNormalizer 实例，无 MySQL 时返回 None (降级)"""
        try:
            from app.services.entity_normalizer import get_entity_normalizer
            normalizer = get_entity_normalizer()
            normalizer._ensure_cache()
            if not normalizer._canonical_cache:
                return None
            return normalizer
        except Exception:
            return None

    def _extract_skills_from_entities(self, entities: Dict[str, Any]) -> List[str]:
        """从实体中提取技能列表"""
        skills = entities.get("skills", [])
        if isinstance(skills, list):
            if skills and isinstance(skills[0], str):
                return skills
            elif skills and isinstance(skills[0], dict):
                return [s.get("name", s.get("skill", "")) for s in skills if s]
        return []


def entities_to_student_profile(entities: Dict[str, Any]) -> Dict[str, Any]:
    """
    Schema adapter only: already-extracted LLM entities → Portrait student_profile.
    No regex / keyword repair. Missing fields stay absent (no invented GPA/months).
    Prefer ``extract_canonical_profile`` for Career Nav scoring.
    """
    if not isinstance(entities, dict):
        return {}

    basic = entities.get("basic_info") if isinstance(entities.get("basic_info"), dict) else {}
    education = entities.get("education") or []
    experience = entities.get("experience") or []
    certificates = entities.get("certificates") or []
    awards = entities.get("awards") or []
    research_src = entities.get("research") or []

    edu0: Dict[str, Any] = {}
    if isinstance(education, list) and education:
        first = education[0]
        if isinstance(first, dict):
            edu0 = first

    projects: List[Dict[str, Any]] = []
    internship_months = 0
    leadership: List[str] = []
    for exp in experience if isinstance(experience, list) else []:
        if not isinstance(exp, dict):
            continue
        etype = str(exp.get("type") or "").lower()
        name = str(exp.get("name") or exp.get("company") or exp.get("position") or "").strip()
        role = str(exp.get("position") or "").strip() or "成员"
        desc = str(exp.get("description") or "").strip()
        if etype in ("project", "") or exp.get("name") or (not exp.get("company") and desc):
            if name or desc:
                projects.append({
                    "name": name or (desc[:40] if desc else "项目"),
                    "role": role,
                    "is_team": bool(exp.get("is_team")),
                    "desc": desc[:400],
                })
        months_raw = exp.get("internship_months")
        if months_raw is not None and str(months_raw).strip() != "":
            try:
                internship_months = max(internship_months, min(int(months_raw), 24))
            except (TypeError, ValueError):
                pass
        for lead in exp.get("leadership_roles") or []:
            if str(lead).strip():
                leadership.append(str(lead).strip())

    competitions: List[Dict[str, Any]] = []
    for a in awards if isinstance(awards, list) else []:
        if isinstance(a, dict):
            competitions.append({
                "name": str(a.get("name") or "").strip(),
                "level": str(a.get("level") or a.get("award") or "其他").strip(),
                "award": str(a.get("level") or a.get("award") or "其他").strip(),
            })
        elif isinstance(a, str) and a.strip():
            competitions.append({"name": a.strip(), "level": "其他", "award": "其他"})

    cert_names: List[str] = []
    for c in certificates if isinstance(certificates, list) else []:
        if isinstance(c, dict) and c.get("name"):
            cert_names.append(str(c["name"]).strip())
        elif isinstance(c, str) and c.strip():
            cert_names.append(c.strip())

    research: List[Dict[str, Any]] = []
    for r in research_src if isinstance(research_src, list) else []:
        if isinstance(r, dict) and (r.get("name") or r.get("level")):
            research.append({
                "name": str(r.get("name") or "").strip(),
                "level": str(r.get("level") or "校级").strip(),
            })

    school = str(edu0.get("school") or basic.get("school") or "").strip()
    major = str(edu0.get("major") or basic.get("major") or "").strip()

    top_months = entities.get("internship_months")
    if top_months is not None and str(top_months).strip() != "":
        try:
            internship_months = max(internship_months, min(int(top_months), 24))
        except (TypeError, ValueError):
            pass

    for lead in entities.get("leadership_roles") or []:
        if str(lead).strip():
            leadership.append(str(lead).strip())

    profile: Dict[str, Any] = {
        "name": str(basic.get("name") or "").strip(),
        "phone": str(basic.get("phone") or "").strip(),
        "email": str(basic.get("email") or "").strip(),
        "grade": str(basic.get("grade") or "").strip(),
        "school": school,
        "major": major,
        "degree": str(edu0.get("degree") or "").strip() or "本科",
        "projects": projects,
        "competitions": [c for c in competitions if c.get("name")],
        "certificates": cert_names,
        "research": research,
        "leadership_roles": list(dict.fromkeys(leadership)),
        "internship_months": internship_months,
        "source": "nsle_llm",
    }
    gpa_raw = edu0.get("gpa")
    if gpa_raw is not None and str(gpa_raw).strip() != "":
        try:
            profile["gpa"] = float(gpa_raw)
        except (TypeError, ValueError):
            pass
    rank_raw = edu0.get("rank_percent")
    if rank_raw is not None and str(rank_raw).strip() != "":
        try:
            profile["rank_percent"] = float(rank_raw)
        except (TypeError, ValueError):
            pass

    return {k: v for k, v in profile.items() if v not in ("", None, [], {})}


def run_nsle_resume(text: str) -> Dict[str, Any]:
    """便捷函数: 运行简历 NS-LE 管道"""
    pipeline = NSLEPipeline()
    return pipeline.run_resume_text(text)


def run_nsle_jd(text: str) -> Dict[str, Any]:
    """便捷函数: 运行 JD NS-LE 管道"""
    pipeline = NSLEPipeline()
    return pipeline.run_jd_text(text)


if __name__ == "__main__":
    test_resume = """
    张三 | Python后端开发 | 3年经验

    个人信息
    手机: 138xxxx8888
    邮箱: zhangsan@email.com

    教育背景
    2015-2019 XX大学 计算机科学与技术 本科

    技能特长
    - 熟练掌握 Python、Django、Flask
    - 精通 MySQL、Redis 数据库
    - 熟悉 Docker、Kubernetes
    - 了解 Vue.js、TensorFlow

    工作经历
    2021-至今 某互联网公司 Python后端开发
    2019-2021 某科技公司 实习
    """

    print("=" * 50)
    print("NS-LE Pipeline 测试 (DeepSeek)")
    print("=" * 50)

    result = run_nsle_resume(test_resume)

    print(f"\n成功: {result['success']}")
    print(f"Blocks: {len(result.get('blocks', []))}")
    print(f"技能: {result.get('skills', [])[:10]}")
