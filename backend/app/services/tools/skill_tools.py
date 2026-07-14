"""
Agent 工具 - 技能抽取与匹配
封装 L0-L1 管线供 Agent 调用
"""

from typing import Dict, Any
from .base import BaseTool


class SkillExtractTool(BaseTool):
    """技能抽取工具"""

    name = "skill_extract"
    description = "从文本（简历、JD）中抽取IT技能。输入文本，输出技能列表。"

    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "待抽取的文本（简历或JD内容）"
            },
            "use_llm": {
                "type": "boolean",
                "description": "是否使用LLM增强抽取，默认true",
                "default": True
            }
        },
        "required": ["text"]
    }

    def execute(self, text: str, use_llm: bool = True) -> Dict[str, Any]:
        from ...pipeline.entity_extractor.llm_skill_extractor import extract_skills_with_relations

        result = extract_skills_with_relations(text, use_llm=use_llm)

        skills = result.get("skills", [])
        relations = result.get("relations", [])

        skill_names = [s["name"] for s in skills]
        categories = {}
        for s in skills:
            cat = s.get("category", "其他")
            categories[cat] = categories.get(cat, 0) + 1

        return {
            "skills_count": len(skills),
            "skill_names": skill_names,
            "categories": categories,
            "top_skills": skill_names[:10],
            "relations_count": len(relations),
            "sample": skills[:5] if skills else []
        }


class SkillMatchTool(BaseTool):
    """人岗匹配工具"""

    name = "skill_match"
    description = "计算人岗匹配度，输出匹配分数、技能覆盖率、缺失技能和建议。"

    parameters = {
        "type": "object",
        "properties": {
            "student_skills": {
                "type": "array",
                "description": "学生技能列表，如 [{\"name\": \"Python\", \"confidence\": 0.8}]",
                "items": {"type": "object"}
            },
            "job_skills": {
                "type": "array",
                "description": "岗位技能要求，如 [{\"name\": \"Python\", \"level\": \"必须\"}]",
                "items": {"type": "object"}
            }
        },
        "required": ["student_skills", "job_skills"]
    }

    def execute(self, student_skills: list, job_skills: list) -> Dict[str, Any]:
        from ...pipeline.entity_extractor.matcher import calculate_match_score

        result = calculate_match_score(student_skills, job_skills)

        return {
            "total_score": result["total_score"],
            "match_level": result["match_level"],
            "skill_coverage": result["skill_coverage"],
            "matched_skills": result["matched_skills"],
            "missing_skills": result["missing_skills"],
            "gap_priorities": result.get("gap_priorities", [])[:5]
        }


class PortraitGenerateTool(BaseTool):
    """画像生成工具"""

    name = "portrait_generate"
    description = "生成学生或岗位的四维画像。"

    parameters = {
        "type": "object",
        "properties": {
            "entity_type": {
                "type": "string",
                "description": "实体类型：student 或 job",
                "enum": ["student", "job"]
            },
            "skills": {
                "type": "array",
                "description": "技能列表"
            },
            "name": {
                "type": "string",
                "description": "学生ID或岗位名称"
            }
        },
        "required": ["entity_type", "skills"]
    }

    def execute(self, entity_type: str, skills: list, name: str = None) -> Dict[str, Any]:
        from ...pipeline.entity_extractor.portrait_generator import (
            generate_student_portrait,
            generate_job_portrait
        )

        if entity_type == "student":
            portrait = generate_student_portrait(
                student_id=name or "unknown",
                skills=skills
            )
        else:
            portrait = generate_job_portrait(
                job_id=name or "unknown",
                position_name=name or "未知岗位",
                skills=skills
            )

        return portrait


class ReportGenerateTool(BaseTool):
    """报告生成工具"""

    name = "report_generate"
    description = "生成人岗匹配报告，包含LLM智能摘要。"

    parameters = {
        "type": "object",
        "properties": {
            "student_skills": {
                "type": "array",
                "description": "学生技能列表"
            },
            "job_skills": {
                "type": "array",
                "description": "岗位技能列表"
            },
            "job_name": {
                "type": "string",
                "description": "岗位名称"
            }
        },
        "required": ["student_skills", "job_skills"]
    }

    def execute(self, student_skills: list, job_skills: list, job_name: str = "未知岗位") -> Dict[str, Any]:
        from ...pipeline.entity_extractor.l4_report_generator import generate_full_report

        result = generate_full_report(
            student_id="agent_user",
            student_skills=student_skills,
            job_id="agent_job",
            job_name=job_name,
            job_skills=job_skills,
            use_llm_summary=True
        )

        return {
            "report_id": result.get("report_id"),
            "summary": result.get("summary", {}),
            "llm_summary": result.get("llm_summary", ""),
            "conclusion": result.get("summary", {}).get("conclusion", ""),
            "recommendations_count": len(result.get("recommendations", []))
        }


class SkillRecommendTool(BaseTool):
    """技能推荐工具"""

    name = "skill_recommend"
    description = "基于已有技能，推荐相关技能。"

    parameters = {
        "type": "object",
        "properties": {
            "skills": {
                "type": "array",
                "description": "已有技能列表",
                "items": {"type": "string"}
            },
            "limit": {
                "type": "integer",
                "description": "推荐数量",
                "default": 5
            }
        },
        "required": ["skills"]
    }

    def execute(self, skills: list, limit: int = 5) -> Dict[str, Any]:
        from ...pipeline.entity_extractor.graph_storage import GraphStorage

        storage = GraphStorage()
        recommendations = storage.recommend_skills(skills, limit=limit)

        return {
            "input_skills": skills,
            "recommendations": recommendations,
            "count": len(recommendations)
        }


class LearningPathTool(BaseTool):
    """学习路径工具"""

    name = "learning_path"
    description = "查找两个技能之间的学习路径。"

    parameters = {
        "type": "object",
        "properties": {
            "from_skill": {
                "type": "string",
                "description": "起始技能"
            },
            "to_skill": {
                "type": "string",
                "description": "目标技能"
            }
        },
        "required": ["from_skill", "to_skill"]
    }

    def execute(self, from_skill: str, to_skill: str) -> Dict[str, Any]:
        from ...pipeline.entity_extractor.graph_storage import GraphStorage

        storage = GraphStorage()
        path = storage.find_learning_path(from_skill, to_skill)

        return path
