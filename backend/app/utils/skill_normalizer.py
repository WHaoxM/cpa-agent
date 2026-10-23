"""技能语义归一化Agent

设计原则（Agent内部loop行为）：
1. LLM抽取技能后，自动进行语义归一化
2. 无需用户确认，自动完成
3. 新发现的映射自动保存到配置文件
4. 前端只显示最终的技能列表
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path


@dataclass
class NormalizationResult:
    """归一化结果"""
    original: str
    normalized: str
    confidence: float
    is_known: bool  # 是否在已知配置中
    category: str = ""


@dataclass
class SkillNormalizationResult:
    """技能归一化完整结果"""
    skills: List[NormalizationResult]
    new_mappings: Dict[str, str]  # 新发现的映射（自动保存）


class SkillNormalizerAgent:
    """技能语义归一化Agent - Agent内部自动行为"""
    
    def __init__(self, llm_client: Any = None):
        self.llm_client = llm_client
        self._config_cache: Optional[Dict[str, Any]] = None
        self._config_path = Path(__file__).parent.parent.parent / 'config' / 'defaults' / 'dictionaries' / 'skill_aliases.yaml'
    
    def normalize_skills(
        self,
        raw_skills: List[str],
        context: str = "",
    ) -> SkillNormalizationResult:
        """
        自动归一化技能列表（Agent内部行为）
        
        Args:
            raw_skills: 原始技能列表（可能包含缩写、变体）
            context: 上下文文本（简历内容）
        
        Returns:
            SkillNormalizationResult
        """
        config = self._load_config()
        known_mappings = config.get('aliases', {})
        
        results: List[NormalizationResult] = []
        new_mappings: Dict[str, str] = {}
        
        for skill in raw_skills:
            skill_lower = skill.lower().strip()
            
            # 1. 尝试精确匹配（忽略大小写）
            normalized = None
            for key, value in known_mappings.items():
                if key.lower() == skill_lower:
                    normalized = value
                    break
            
            if normalized:
                results.append(NormalizationResult(
                    original=skill,
                    normalized=normalized,
                    confidence=1.0,
                    is_known=True,
                ))
                continue
            
            # 2. 使用LLM进行语义归一化
            if self.llm_client:
                llm_result = self._normalize_with_llm(skill, context, known_mappings)
                if llm_result and llm_result.confidence >= 0.7:
                    results.append(llm_result)
                    # 自动保存新映射
                    new_mappings[skill] = llm_result.normalized
                    continue
            
            # 3. 未归一化，保持原样
            results.append(NormalizationResult(
                original=skill,
                normalized=skill,
                confidence=0.0,
                is_known=False,
            ))
        
        # 自动保存新发现的映射
        if new_mappings:
            self._save_mappings(new_mappings)
        
        return SkillNormalizationResult(
            skills=results,
            new_mappings=new_mappings,
        )
    
    def _normalize_with_llm(
        self,
        skill: str,
        context: str,
        known_mappings: Dict[str, str],
    ) -> Optional[NormalizationResult]:
        """使用LLM进行语义归一化"""
        if not self.llm_client:
            return None
        
        # 构建prompt
        known_list = "\n".join([f"- {k} → {v}" for k, v in list(known_mappings.items())[:30]])
        
        prompt = f"""你是一个技能归一化专家。请将技能名称归一化到标准形式。

已知映射示例：
{known_list}

技能：{skill}
上下文：{context[:300] if context else "无"}

输出JSON：{{"normalized": "标准名", "confidence": 0.9, "category": "分类"}}

如果无法确定，confidence设为0.5以下，normalized保持原样。"""

        try:
            response = self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=150,
            )
            
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return NormalizationResult(
                    original=skill,
                    normalized=data.get('normalized', skill),
                    confidence=data.get('confidence', 0.5),
                    is_known=False,
                    category=data.get('category', ''),
                )
        except Exception:
            pass
        
        return None
    
    def _load_config(self) -> Dict[str, Any]:
        """加载配置"""
        if self._config_cache:
            return self._config_cache
        
        try:
            import yaml
            if self._config_path.exists():
                with open(self._config_path, 'r', encoding='utf-8') as f:
                    self._config_cache = yaml.safe_load(f) or {}
            else:
                self._config_cache = {}
        except Exception:
            self._config_cache = {}
        
        return self._config_cache
    
    def _save_mappings(self, mappings: Dict[str, str]):
        """自动保存新映射到配置文件"""
        if not mappings:
            return
        
        config = self._load_config()
        aliases = config.get('aliases', {})
        aliases.update(mappings)
        config['aliases'] = aliases
        
        try:
            import yaml
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._config_path, 'w', encoding='utf-8') as f:
                yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
        except Exception:
            pass


# 全局实例
_normalizer_agent: Optional[SkillNormalizerAgent] = None


def get_skill_normalizer_agent() -> SkillNormalizerAgent:
    """获取技能归一化Agent单例"""
    global _normalizer_agent
    if _normalizer_agent is None:
        _normalizer_agent = SkillNormalizerAgent()
    return _normalizer_agent


def normalize_skills(raw_skills: List[str], context: str = "") -> SkillNormalizationResult:
    """归一化技能的便捷函数"""
    return get_skill_normalizer_agent().normalize_skills(raw_skills, context)
