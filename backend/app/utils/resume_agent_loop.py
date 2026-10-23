"""简历解析 Agent Loop

完整流程：
1. ResumeParseAgent - 解析简历文本
2. ExtractAgent - 抽取技能、岗位、职业信息
3. NormalizeAgent - 归一化抽取结果
4. ValidateAgent - 校验归一化结果
5. DisplayAgent - 格式化显示

每个Agent都有明确的职责，形成闭环。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path
import json


# ─────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────

@dataclass
class ResumeData:
    """简历数据"""
    raw_text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
@dataclass
class ExtractedEntities:
    """抽取的实体"""
    skills: List[Dict[str, Any]] = field(default_factory=list)
    positions: List[str] = field(default_factory=list)
    careers: List[Dict[str, Any]] = field(default_factory=list)
    education: List[Dict[str, Any]] = field(default_factory=list)
    experience: List[Dict[str, Any]] = field(default_factory=list)

@dataclass
class NormalizedEntities:
    """归一化后的实体"""
    skills: List[Dict[str, Any]] = field(default_factory=list)
    positions: List[Dict[str, Any]] = field(default_factory=list)
    careers: List[Dict[str, Any]] = field(default_factory=list)
    new_mappings: Dict[str, str] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ValidatedEntities:
    """校验后的实体"""
    skills: List[Dict[str, Any]] = field(default_factory=list)
    positions: List[Dict[str, Any]] = field(default_factory=list)
    careers: List[Dict[str, Any]] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    confidence: float = 0.0

@dataclass
class DisplayData:
    """显示数据"""
    skills: List[Dict[str, Any]] = field(default_factory=list)
    education: Dict[str, Any] = field(default_factory=dict)
    experience: List[Dict[str, Any]] = field(default_factory=list)
    portrait: Dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────
# Agent 基类
# ─────────────────────────────────────────────────────────

class BaseAgent:
    """Agent基类"""
    
    def __init__(self, name: str, llm_client: Any = None):
        self.name = name
        self.llm_client = llm_client
    
    def log(self, message: str):
        print(f"[{self.name}] {message}")


# ─────────────────────────────────────────────────────────
# 简历解析 Agent
# ─────────────────────────────────────────────────────────

class ResumeParseAgent(BaseAgent):
    """简历解析Agent - 解析简历文本"""
    
    def __init__(self, llm_client: Any = None):
        super().__init__("ResumeParseAgent", llm_client)
    
    def execute(self, resume: ResumeData) -> ExtractedEntities:
        """执行简历解析"""
        self.log("开始解析简历...")
        
        # 使用NS-LE管道解析
        try:
            from app.pipeline.entity_extractor.nsle_pipeline import NSLEPipeline
            
            pipeline = NSLEPipeline(use_deepseek=True)
            result = pipeline.process(resume.raw_text)
            
            entities = ExtractedEntities(
                skills=result.get("skills", []),
                positions=[result.get("target_role", "")],
                careers=[{
                    "degree": result.get("degree", ""),
                    "school": result.get("school", ""),
                    "major": result.get("major", ""),
                }],
                education=result.get("education", []),
                experience=result.get("experience", []),
            )
            
            self.log(f"解析完成: {len(entities.skills)} 技能, {len(entities.experience)} 经历")
            return entities
            
        except Exception as e:
            self.log(f"解析失败: {e}")
            return ExtractedEntities()


# ─────────────────────────────────────────────────────────
# 抽取 Agent
# ─────────────────────────────────────────────────────────

class ExtractAgent(BaseAgent):
    """抽取Agent - 从文本中抽取实体"""
    
    def __init__(self, llm_client: Any = None):
        super().__init__("ExtractAgent", llm_client)
    
    def execute(self, resume: ResumeData, entities: ExtractedEntities) -> ExtractedEntities:
        """执行实体抽取"""
        self.log("开始抽取实体...")
        
        # 补充抽取技能
        if not entities.skills:
            try:
                from app.pipeline.entity_extractor.llm_skill_extractor import LLMSkillExtractor
                
                extractor = LLMSkillExtractor()
                skills = extractor.extract(resume.raw_text, use_llm=True)
                entities.skills = skills
                self.log(f"抽取到 {len(skills)} 个技能")
            except Exception as e:
                self.log(f"技能抽取失败: {e}")
        
        return entities


# ─────────────────────────────────────────────────────────
# 归一化 Agent
# ─────────────────────────────────────────────────────────

class NormalizeAgent(BaseAgent):
    """归一化Agent - 归一化抽取结果"""
    
    def __init__(self, llm_client: Any = None):
        super().__init__("NormalizeAgent", llm_client)
    
    def execute(self, entities: ExtractedEntities) -> NormalizedEntities:
        """执行归一化"""
        self.log("开始归一化...")
        
        from app.utils.normalization_agent import get_normalization_supervisor
        
        supervisor = get_normalization_supervisor(self.llm_client)
        
        # 准备归一化数据
        extract_data = {}
        
        if entities.skills:
            skill_names = [s.get("name", "") for s in entities.skills]
            extract_data["skills"] = skill_names
        
        if entities.positions:
            extract_data["positions"] = entities.positions
        
        if entities.careers:
            career_items = []
            for c in entities.careers:
                if c.get("degree"):
                    career_items.append(c["degree"])
                if c.get("major"):
                    career_items.append(c["major"])
            if career_items:
                extract_data["careers"] = career_items
        
        # 执行归一化
        norm_results = supervisor.normalize_all(extract_data)
        
        # 构建归一化结果
        normalized = NormalizedEntities()
        
        # 归一化技能
        if "skills" in norm_results:
            skill_norm = norm_results["skills"]
            for i, skill in enumerate(entities.skills):
                if i < len(skill_norm.results):
                    norm = skill_norm.results[i]
                    normalized.skills.append({
                        **skill,
                        "original_name": skill.get("name"),
                        "name": norm.normalized,
                        "confidence": max(skill.get("confidence", 0.5), norm.confidence),
                        "is_known": norm.is_known,
                    })
                else:
                    normalized.skills.append(skill)
        
        # 归一化岗位
        if "positions" in norm_results:
            pos_norm = norm_results["positions"]
            for i, pos in enumerate(entities.positions):
                if i < len(pos_norm.results):
                    norm = pos_norm.results[i]
                    normalized.positions.append({
                        "original": pos,
                        "normalized": norm.normalized,
                        "confidence": norm.confidence,
                        "is_known": norm.is_known,
                    })
        
        # 归一化职业信息
        if "careers" in norm_results:
            career_norm = norm_results["careers"]
            for i, career in enumerate(entities.careers):
                if i < len(career_norm.results):
                    norm = career_norm.results[i]
                    normalized.careers.append({
                        **career,
                        "normalized_degree": norm.normalized if "degree" in str(career) else career.get("degree"),
                        "normalized_major": norm.normalized if "major" in str(career) else career.get("major"),
                    })
        
        # 收集新映射
        for result in norm_results.values():
            normalized.new_mappings.update(result.new_mappings)
        
        # 统计
        total_skills = len(normalized.skills)
        known_skills = sum(1 for s in normalized.skills if s.get("is_known"))
        normalized.stats = {
            "total_skills": total_skills,
            "known_skills": known_skills,
            "coverage": known_skills / total_skills if total_skills > 0 else 0,
        }
        
        self.log(f"归一化完成: {known_skills}/{total_skills} 技能已知 "
                f"(覆盖率 {normalized.stats['coverage']:.1%})")
        
        return normalized


# ─────────────────────────────────────────────────────────
# 校验 Agent
# ─────────────────────────────────────────────────────────

class ValidateAgent(BaseAgent):
    """校验Agent - 校验归一化结果"""
    
    def __init__(self, llm_client: Any = None):
        super().__init__("ValidateAgent", llm_client)
    
    def execute(self, normalized: NormalizedEntities) -> ValidatedEntities:
        """执行校验"""
        self.log("开始校验...")
        
        validated = ValidatedEntities()
        issues = []
        
        # 校验技能
        for skill in normalized.skills:
            name = skill.get("name", "")
            confidence = skill.get("confidence", 0)
            
            # 过滤无效技能
            if len(name) < 2:
                issues.append(f"技能名过短: {name}")
                continue
            
            # 过滤URL/链接
            if any(url in name.lower() for url in ["http", "www", "github", "com"]):
                issues.append(f"过滤URL: {name}")
                continue
            
            # 过滤单字符
            if len(name) == 1:
                issues.append(f"过滤单字符: {name}")
                continue
            
            validated.skills.append(skill)
        
        # 校验岗位
        for pos in normalized.positions:
            validated.positions.append(pos)
        
        # 校验职业信息
        for career in normalized.careers:
            validated.careers.append(career)
        
        # 计算置信度
        if validated.skills:
            total_conf = sum(s.get("confidence", 0) for s in validated.skills)
            validated.confidence = total_conf / len(validated.skills)
        
        validated.issues = issues
        
        self.log(f"校验完成: {len(validated.skills)} 技能, "
                f"{len(validated.positions)} 岗位, "
                f"{len(validated.careers)} 职业信息")
        if issues:
            self.log(f"发现 {len(issues)} 个问题")
        
        return validated


# ─────────────────────────────────────────────────────────
# 显示 Agent
# ─────────────────────────────────────────────────────────

class DisplayAgent(BaseAgent):
    """显示Agent - 格式化显示结果"""
    
    def __init__(self, llm_client: Any = None):
        super().__init__("DisplayAgent", llm_client)
    
    def execute(self, validated: ValidatedEntities, resume: ResumeData) -> DisplayData:
        """执行显示格式化"""
        self.log("格式化显示数据...")
        
        display = DisplayData()
        
        # 格式化技能
        for skill in validated.skills:
            display.skills.append({
                "name": skill.get("name"),
                "level": skill.get("level", "熟悉"),
                "category": skill.get("category", "技术"),
                "confidence": skill.get("confidence", 0.5),
                "source": skill.get("source", "unknown"),
            })
        
        # 格式化教育信息
        for career in validated.careers:
            display.education = {
                "degree": career.get("normalized_degree", career.get("degree", "")),
                "school": career.get("school", ""),
                "major": career.get("normalized_major", career.get("major", "")),
            }
            break
        
        # 格式化经历
        display.experience = [
            {
                "title": exp.get("company", ""),
                "role": exp.get("position", ""),
                "period": f"{exp.get('start_date', '')} - {exp.get('end_date', '')}",
                "description": exp.get("description", ""),
            }
            for exp in (resume.metadata.get("experience", []) or [])
        ]
        
        self.log(f"格式化完成: {len(display.skills)} 技能")
        
        return display


# ─────────────────────────────────────────────────────────
# Agent Loop 编排器
# ─────────────────────────────────────────────────────────

class ResumeAgentLoop:
    """简历解析Agent Loop编排器"""
    
    def __init__(self, llm_client: Any = None):
        self.llm_client = llm_client
        
        # 初始化所有Agent
        self.parse_agent = ResumeParseAgent(llm_client)
        self.extract_agent = ExtractAgent(llm_client)
        self.normalize_agent = NormalizeAgent(llm_client)
        self.validate_agent = ValidateAgent(llm_client)
        self.display_agent = DisplayAgent(llm_client)
    
    def execute(self, resume_text: str, metadata: Dict[str, Any] = None) -> DisplayData:
        """
        执行完整的Agent Loop
        
        Args:
            resume_text: 简历文本
            metadata: 元数据
        
        Returns:
            DisplayData
        """
        print("=" * 60)
        print("开始简历解析 Agent Loop")
        print("=" * 60)
        
        # Step 1: 简历解析
        resume = ResumeData(raw_text=resume_text, metadata=metadata or {})
        entities = self.parse_agent.execute(resume)
        
        # Step 2: 实体抽取
        entities = self.extract_agent.execute(resume, entities)
        
        # Step 3: 归一化
        normalized = self.normalize_agent.execute(entities)
        
        # Step 4: 校验
        validated = self.validate_agent.execute(normalized)
        
        # Step 5: 显示
        display = self.display_agent.execute(validated, resume)
        
        print("=" * 60)
        print("Agent Loop 完成")
        print("=" * 60)
        
        return display
    
    def execute_with_validation(
        self,
        resume_text: str,
        metadata: Dict[str, Any] = None,
        max_retries: int = 3,
    ) -> DisplayData:
        """
        带重试的Agent Loop
        
        Args:
            resume_text: 简历文本
            metadata: 元数据
            max_retries: 最大重试次数
        
        Returns:
            DisplayData
        """
        for attempt in range(max_retries):
            try:
                display = self.execute(resume_text, metadata)
                
                # 检查置信度
                if display.skills:
                    avg_confidence = sum(s.get("confidence", 0) for s in display.skills) / len(display.skills)
                    if avg_confidence >= 0.7:
                        return display
                
                print(f"置信度不足 ({avg_confidence:.2f}), 重试...")
                
            except Exception as e:
                print(f"执行失败: {e}, 重试...")
        
        # 最后一次尝试
        return self.execute(resume_text, metadata)


# ─────────────────────────────────────────────────────────
# 全局实例
# ─────────────────────────────────────────────────────────

_loop: Optional[ResumeAgentLoop] = None


def get_resume_agent_loop(llm_client: Any = None) -> ResumeAgentLoop:
    """获取简历解析Agent Loop单例"""
    global _loop
    if _loop is None:
        _loop = ResumeAgentLoop(llm_client)
    return _loop


def parse_resume_with_agent_loop(
    resume_text: str,
    metadata: Dict[str, Any] = None,
    llm_client: Any = None,
) -> DisplayData:
    """使用Agent Loop解析简历的便捷函数"""
    loop = get_resume_agent_loop(llm_client)
    return loop.execute(resume_text, metadata)
