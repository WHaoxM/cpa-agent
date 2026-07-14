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
import re
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

    def run_resume_text(self, text: str) -> Dict[str, Any]:
        """
        运行简历解析完整管道

        Args:
            text: 简历文本

        Returns:
            解析结果
        """
        try:
            blocks = self._phase1_parse(text, doc_type="resume")

            filtered_blocks = self._phase2_filter(blocks)

            entities = self._phase3_extract(filtered_blocks, doc_type="resume")

            result = self._phase4_guard(entities)

            return {
                "success": True,
                "blocks": filtered_blocks,
                "entities": result,
                "skills": self._extract_skills_from_entities(entities)
            }

        except Exception as e:
            logger.error(f"简历解析失败: {e}")
            return {"success": False, "error": str(e), "blocks": [], "entities": {}}

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
        lines = text.split("\n")
        blocks = []
        current_block_lines = []
        current_block_type = BlockType.OTHER
        block_id = 1

        resume_heading_rules = [
            (["技能", "证书", "能力", "技术栈"], BlockType.SKILLS_CERTS),
            (["项目", "科研", "课题", "研究"], BlockType.PROJECT_EXPERIENCE),
            (["工作", "经历", "经验"], BlockType.WORK_EXPERIENCE),
            (["教育", "学历", "毕业", "学校"], BlockType.EDUCATION),
            (["个人", "基本信息", "简介"], BlockType.PERSONAL_INFO),
        ]

        jd_heading_rules = [
            (["岗位", "职位", "title"], BlockType.JOB_TITLE),
            (["要求", "任职", "qualification"], BlockType.JOB_REQUIREMENTS),
            (["职责", "工作内容", "description"], BlockType.JOB_DESCRIPTION),
            (["技能", "技术", "tech"], BlockType.SKILLS_CERTS),
        ]

        heading_rules = resume_heading_rules if doc_type == "resume" else jd_heading_rules

        for line in lines:
            line = line.strip()
            if not line:
                continue

            matched_type = None
            if len(line) <= 20:
                for keywords, block_type in heading_rules:
                    for keyword in keywords:
                        if keyword in line:
                            matched_type = block_type
                            break
                    if matched_type:
                        break

            if matched_type:
                if current_block_lines:
                    blocks.append(DocumentBlock(
                        block_id=f"b{block_id}",
                        block_type=current_block_type,
                        text="\n".join(current_block_lines),
                        confidence=0.9,
                        source={"doc_type": doc_type},
                        heading=""
                    ))
                    block_id += 1
                    current_block_lines = []

                current_block_type = matched_type
                current_block_lines.append(line)
            else:
                current_block_lines.append(line)

        if current_block_lines:
            blocks.append(DocumentBlock(
                block_id=f"b{block_id}",
                block_type=current_block_type,
                text="\n".join(current_block_lines),
                confidence=0.8,
                source={"doc_type": doc_type},
                heading=""
            ))

        return blocks

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

            if len(text) > 5:
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
        skills_blocks = [b for b in blocks if b.block_type in [
            BlockType.SKILLS_CERTS, BlockType.SKILLS_CERTS.upper()
        ]]
        experience_blocks = [b for b in blocks if b.block_type in [
            BlockType.WORK_EXPERIENCE, BlockType.PROJECT_EXPERIENCE,
            BlockType.WORK_EXPERIENCE.upper(), BlockType.PROJECT_EXPERIENCE.upper()
        ]]
        education_blocks = [b for b in blocks if b.block_type in [
            BlockType.EDUCATION, BlockType.EDUCATION.upper()
        ]]

        entities = {}

        all_text = "\n".join([b.text for b in blocks])
        entities["skills"] = self._extract_skills_with_llm(all_text) if self.use_deepseek else []

        if self.use_deepseek and skills_blocks:
            skills_text = "\n".join([b.text for b in skills_blocks])
            llm_skills = self._extract_skills_with_llm(skills_text)
            if llm_skills:
                entities["skills"] = llm_skills

        if not entities.get("skills"):
            entities["skills"] = self._rule_based_extract(blocks, doc_type).get("skills", [])

        if experience_blocks:
            exp_text = "\n".join([b.text for b in experience_blocks])
            entities["experience"] = self._extract_experience_with_llm(exp_text)
        else:
            entities["experience"] = []

        if education_blocks:
            edu_text = "\n".join([b.text for b in education_blocks])
            entities["education"] = self._extract_education_with_llm(edu_text)
        else:
            entities["education"] = []

        certs_text = "\n".join([b.text for b in skills_blocks]) if skills_blocks else all_text
        entities["certificates"] = self._extract_certificates_with_llm(certs_text)

        return entities

    def _extract_skills_with_llm(self, text: str) -> List[str]:
        """使用 LLM 抽取技能"""
        prompt = load_prompt("pipeline.nsle.extract.skills.user", text=text[:1000])

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
            return self._rule_based_extract([DocumentBlock(
                block_id="llm_fallback", block_type=BlockType.SKILLS_CERTS,
                text=text[:1000], confidence=1.0, source={}
            )], "resume").get("skills", [])
        except Exception as e:
            logger.warning(f"LLM技能抽取失败: {e}")
            return self._rule_based_extract([DocumentBlock(
                block_id="llm_fallback", block_type=BlockType.SKILLS_CERTS,
                text=text[:1000], confidence=1.0, source={}
            )], "resume").get("skills", [])

    def _extract_experience_with_llm(self, text: str) -> List[Dict]:
        """使用 LLM 抽取工作经历"""
        if not text or not text.strip():
            return []

        prompt = load_prompt("pipeline.nsle.extract.experience.user", text=text[:1500])

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

        prompt = load_prompt("pipeline.nsle.extract.education.user", text=text[:1500])

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

        prompt = load_prompt("pipeline.nsle.extract.certificates.user", text=text[:1500])

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

    def _rule_based_extract(self, blocks: List[DocumentBlock], doc_type: str) -> Dict[str, Any]:
        """
        基于规则的提取 (无LLM时的降级方案)
        """
        from app.pipeline.entity_extractor.skill_extractor import extract_skills as dict_extract

        all_text = "\n".join([b.text for b in blocks])
        skills = dict_extract(all_text)

        return {
            "skills": [s["name"] for s in skills],
            "experience": [],
            "education": []
        }

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
