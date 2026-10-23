"""Skills系统

借鉴OpenCode的Skills系统，为CPA-Agent提供：
1. Skill发现和加载
2. Skill执行
3. SystemContext生成
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
import yaml


@dataclass
class SkillInfo:
    """Skill信息"""
    name: str
    description: str
    content: str
    location: str
    source_type: str  # "directory", "url", "embedded"
    tools: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class SkillDiscovery:
    """Skill发现"""
    
    def __init__(self):
        self._skills: Dict[str, SkillInfo] = {}
    
    def discover_from_directory(self, directory: str) -> List[SkillInfo]:
        """
        从目录发现Skills
        
        Args:
            directory: 目录路径
        
        Returns:
            发现的Skill列表
        """
        skills = []
        dir_path = Path(directory)
        
        if not dir_path.exists():
            return skills
        
        # 查找所有.md文件和SKILL.md文件
        patterns = ['*.md', '**/SKILL.md']
        
        for pattern in patterns:
            for file_path in dir_path.glob(pattern):
                skill = self._load_skill_from_file(str(file_path), "directory")
                if skill:
                    skills.append(skill)
                    self._skills[skill.name] = skill
        
        return skills
    
    def _load_skill_from_file(self, file_path: str, source_type: str) -> Optional[SkillInfo]:
        """从文件加载Skill"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 解析Markdown frontmatter
            frontmatter, body = self._parse_frontmatter(content)
            
            if not frontmatter:
                return None
            
            name = frontmatter.get('name') or Path(file_path).stem
            description = frontmatter.get('description', '')
            
            return SkillInfo(
                name=name,
                description=description,
                content=body,
                location=file_path,
                source_type=source_type,
                tools=frontmatter.get('tools', []),
                metadata=frontmatter,
            )
        except Exception:
            return None
    
    def _parse_frontmatter(self, content: str) -> tuple:
        """解析Markdown frontmatter"""
        if not content.startswith('---'):
            return {}, content
        
        try:
            end_idx = content.index('---', 3)
            frontmatter_str = content[3:end_idx].strip()
            body = content[end_idx + 3:].strip()
            
            frontmatter = yaml.safe_load(frontmatter_str) or {}
            return frontmatter, body
        except Exception:
            return {}, content
    
    def get_skill(self, name: str) -> Optional[SkillInfo]:
        """获取指定Skill"""
        return self._skills.get(name)
    
    def list_skills(self) -> List[SkillInfo]:
        """列出所有Skills"""
        return list(self._skills.values())


class SkillExecutor:
    """Skill执行器"""
    
    def __init__(self):
        self._executors: Dict[str, Callable] = {}
    
    def register_executor(self, skill_name: str, executor: Callable):
        """注册Skill执行器"""
        self._executors[skill_name] = executor
    
    def execute(self, skill_name: str, input_data: Dict[str, Any]) -> Any:
        """执行Skill"""
        executor = self._executors.get(skill_name)
        if not executor:
            raise ValueError(f"No executor for skill: {skill_name}")
        
        return executor(input_data)


class SkillContextGenerator:
    """Skill SystemContext生成器"""
    
    def __init__(self, discovery: SkillDiscovery):
        self.discovery = discovery
    
    def generate_system_context(self, available_skills: Optional[List[str]] = None) -> str:
        """
        生成SystemContext
        
        Args:
            available_skills: 可用的Skill名称列表，None表示全部
        
        Returns:
            SystemContext字符串
        """
        all_skills = self.discovery.list_skills()
        
        if available_skills:
            skills = [s for s in all_skills if s.name in available_skills]
        else:
            skills = all_skills
        
        if not skills:
            return ""
        
        parts = ["## Available Skills\n"]
        
        for skill in skills:
            parts.append(f"### {skill.name}")
            parts.append(f"Description: {skill.description}")
            if skill.tools:
                parts.append(f"Tools: {', '.join(skill.tools)}")
            parts.append("")
        
        return "\n".join(parts)


class SkillManager:
    """Skill管理器"""
    
    def __init__(self):
        self.discovery = SkillDiscovery()
        self.executor = SkillExecutor()
        self.context_generator = SkillContextGenerator(self.discovery)
        self._initialized = False
    
    def initialize(self, skill_directories: Optional[List[str]] = None):
        """
        初始化Skill系统
        
        Args:
            skill_directories: Skill目录列表
        """
        if self._initialized:
            return
        
        if skill_directories:
            for directory in skill_directories:
                self.discovery.discover_from_directory(directory)
        
        self._initialized = True
    
    def get_skill(self, name: str) -> Optional[SkillInfo]:
        """获取Skill"""
        return self.discovery.get_skill(name)
    
    def list_skills(self) -> List[SkillInfo]:
        """列出所有Skills"""
        return self.discovery.list_skills()
    
    def get_system_context(self, available_skills: Optional[List[str]] = None) -> str:
        """获取SystemContext"""
        return self.context_generator.generate_system_context(available_skills)
    
    def execute_skill(self, skill_name: str, input_data: Dict[str, Any]) -> Any:
        """执行Skill"""
        return self.executor.execute(skill_name, input_data)


# 全局实例
_skill_manager: Optional[SkillManager] = None


def get_skill_manager() -> SkillManager:
    """获取Skill管理器单例"""
    global _skill_manager
    if _skill_manager is None:
        _skill_manager = SkillManager()
    return _skill_manager


def initialize_skills(skill_directories: Optional[List[str]] = None):
    """初始化Skill系统"""
    get_skill_manager().initialize(skill_directories)
