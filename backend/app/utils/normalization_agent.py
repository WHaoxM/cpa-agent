"""统一归一化Agent架构

设计思想（参照OpenCode Subagent模式）：
1. 上游Agent抽取内容后，交给识别Agent（Supervisor）
2. 识别Agent将不同类型的实体分发给专业Subagent归一化
3. 每个Subagent有独立的逻辑和配置
4. 形成Agent Loop完成校验和确认

架构：
┌─────────────────────────────────────────────────────────┐
│                 NormalizationSupervisor                 │
│  职责：识别实体类型 → 分发给对应Subagent → 汇总结果      │
└─────────────────────────────────────────────────────────┘
        │              │              │              │
        ▼              ▼              ▼              ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│ SkillNormal │ │ PositionNorm│ │ CareerNorm  │ │ OtherNorm   │
│ izerAgent   │ │  izerAgent  │ │  izerAgent  │ │  izerAgent  │
│ 技能归一化   │ │ 岗位归一化   │ │ 职业归一化   │ │ 其他归一化   │
└─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘
```

"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path
import json
import re


# ─────────────────────────────────────────────────────────
# 基础定义
# ─────────────────────────────────────────────────────────

@dataclass
class NormalizationResult:
    """单个实体的归一化结果"""
    original: str
    normalized: str
    confidence: float
    entity_type: str
    is_known: bool  # 是否在已知配置中
    category: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchNormalizationResult:
    """批量归一化结果"""
    results: List[NormalizationResult]
    new_mappings: Dict[str, str]  # 新发现的映射（自动保存）
    entity_type: str
    stats: Dict[str, int] = field(default_factory=dict)  # 统计信息


class BaseNormalizerAgent(ABC):
    """归一化Subagent基类"""
    
    def __init__(self, llm_client: Any = None):
        self.llm_client = llm_client
        self._config_cache: Optional[Dict[str, Any]] = None
    
    @property
    @abstractmethod
    def entity_type(self) -> str:
        """实体类型：skill / position / career / ..."""
        pass
    
    @property
    @abstractmethod
    def config_path(self) -> Path:
        """配置文件路径"""
        pass
    
    @abstractmethod
    def normalize_single(
        self,
        raw: str,
        context: str = "",
        known_mappings: Dict[str, str] = None,
    ) -> NormalizationResult:
        """归一化单个实体"""
        pass
    
    def normalize_batch(
        self,
        raw_list: List[str],
        context: str = "",
    ) -> BatchNormalizationResult:
        """批量归一化"""
        config = self._load_config()
        known_mappings = config.get('aliases', {})
        
        results: List[NormalizationResult] = []
        new_mappings: Dict[str, str] = {}
        
        for raw in raw_list:
            result = self.normalize_single(raw, context, known_mappings)
            results.append(result)
            
            # 高置信度的新映射自动保存
            if not result.is_known and result.confidence >= 0.8:
                new_mappings[result.original] = result.normalized
        
        # 自动保存新映射
        if new_mappings:
            self._save_mappings(new_mappings)
        
        # 统计
        stats = {
            'total': len(results),
            'known': sum(1 for r in results if r.is_known),
            'normalized': sum(1 for r in results if r.is_known or r.confidence >= 0.8),
            'unknown': sum(1 for r in results if not r.is_known and r.confidence < 0.8),
        }
        
        return BatchNormalizationResult(
            results=results,
            new_mappings=new_mappings,
            entity_type=self.entity_type,
            stats=stats,
        )
    
    def _load_config(self) -> Dict[str, Any]:
        """加载配置"""
        if self._config_cache:
            return self._config_cache
        
        try:
            import yaml
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self._config_cache = yaml.safe_load(f) or {}
            else:
                self._config_cache = {}
        except Exception:
            self._config_cache = {}
        
        return self._config_cache
    
    def _save_mappings(self, mappings: Dict[str, str]):
        """保存新映射"""
        if not mappings:
            return
        
        config = self._load_config()
        aliases = config.get('aliases', {})
        aliases.update(mappings)
        config['aliases'] = aliases
        
        try:
            import yaml
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
        except Exception:
            pass
    
    def _normalize_with_llm(
        self,
        raw: str,
        context: str,
        known_mappings: Dict[str, str],
        category_hint: str = "",
    ) -> Optional[NormalizationResult]:
        """使用LLM进行语义归一化"""
        if not self.llm_client:
            return None
        
        known_list = "\n".join([f"- {k} → {v}" for k, v in list(known_mappings.items())[:30]])
        
        prompt = f"""你是一个{self.entity_type}归一化专家。请将名称归一化到标准形式。

已知映射示例：
{known_list}

需要归一化的{self.entity_type}：{raw}
上下文：{context[:300] if context else "无"}
类别提示：{category_hint or "自动判断"}

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
                    original=raw,
                    normalized=data.get('normalized', raw),
                    confidence=data.get('confidence', 0.5),
                    entity_type=self.entity_type,
                    is_known=False,
                    category=data.get('category', ''),
                )
        except Exception:
            pass
        
        return None


# ─────────────────────────────────────────────────────────
# 技能归一化Subagent
# ─────────────────────────────────────────────────────────

class SkillNormalizerAgent(BaseNormalizerAgent):
    """技能归一化Subagent"""
    
    @property
    def entity_type(self) -> str:
        return "skill"
    
    @property
    def config_path(self) -> Path:
        return Path(__file__).parent.parent.parent / 'config' / 'defaults' / 'dictionaries' / 'skill_aliases.yaml'
    
    def normalize_single(
        self,
        raw: str,
        context: str = "",
        known_mappings: Dict[str, str] = None,
    ) -> NormalizationResult:
        """归一化单个技能"""
        known_mappings = known_mappings or {}
        raw_lower = raw.lower().strip()
        
        # 1. 精确匹配（忽略大小写）
        for key, value in known_mappings.items():
            if key.lower() == raw_lower:
                return NormalizationResult(
                    original=raw,
                    normalized=value,
                    confidence=1.0,
                    entity_type=self.entity_type,
                    is_known=True,
                )
        
        # 2. LLM语义归一化
        llm_result = self._normalize_with_llm(raw, context, known_mappings, "技术技能")
        if llm_result:
            return llm_result
        
        # 3. 未归一化
        return NormalizationResult(
            original=raw,
            normalized=raw,
            confidence=0.0,
            entity_type=self.entity_type,
            is_known=False,
        )


# ─────────────────────────────────────────────────────────
# 岗位归一化Subagent
# ─────────────────────────────────────────────────────────

class PositionNormalizerAgent(BaseNormalizerAgent):
    """岗位归一化Subagent"""
    
    @property
    def entity_type(self) -> str:
        return "position"
    
    @property
    def config_path(self) -> Path:
        return Path(__file__).parent.parent.parent / 'config' / 'defaults' / 'dictionaries' / 'position_aliases.yaml'
    
    def normalize_single(
        self,
        raw: str,
        context: str = "",
        known_mappings: Dict[str, str] = None,
    ) -> NormalizationResult:
        """归一化单个岗位"""
        known_mappings = known_mappings or {}
        raw_lower = raw.lower().strip()
        
        # 1. 精确匹配
        for key, value in known_mappings.items():
            if key.lower() == raw_lower:
                return NormalizationResult(
                    original=raw,
                    normalized=value,
                    confidence=1.0,
                    entity_type=self.entity_type,
                    is_known=True,
                )
        
        # 2. LLM语义归一化
        llm_result = self._normalize_with_llm(raw, context, known_mappings, "职位名称")
        if llm_result:
            return llm_result
        
        # 3. 未归一化
        return NormalizationResult(
            original=raw,
            normalized=raw,
            confidence=0.0,
            entity_type=self.entity_type,
            is_known=False,
        )


# ─────────────────────────────────────────────────────────
# 职业信息归一化Subagent
# ─────────────────────────────────────────────────────────

class CareerNormalizerAgent(BaseNormalizerAgent):
    """职业信息归一化Subagent（学历、证书、奖项等）"""
    
    @property
    def entity_type(self) -> str:
        return "career"
    
    @property
    def config_path(self) -> Path:
        return Path(__file__).parent.parent.parent / 'config' / 'defaults' / 'dictionaries' / 'career_aliases.yaml'
    
    def normalize_single(
        self,
        raw: str,
        context: str = "",
        known_mappings: Dict[str, str] = None,
    ) -> NormalizationResult:
        """归一化单个职业信息"""
        known_mappings = known_mappings or {}
        raw_lower = raw.lower().strip()
        
        # 1. 精确匹配
        for key, value in known_mappings.items():
            if key.lower() == raw_lower:
                return NormalizationResult(
                    original=raw,
                    normalized=value,
                    confidence=1.0,
                    entity_type=self.entity_type,
                    is_known=True,
                )
        
        # 2. LLM语义归一化
        llm_result = self._normalize_with_llm(raw, context, known_mappings, "学历/证书/奖项")
        if llm_result:
            return llm_result
        
        # 3. 未归一化
        return NormalizationResult(
            original=raw,
            normalized=raw,
            confidence=0.0,
            entity_type=self.entity_type,
            is_known=False,
        )


# ─────────────────────────────────────────────────────────
# 识别Agent（Supervisor）
# ─────────────────────────────────────────────────────────

class NormalizationSupervisor:
    """
    归一化Supervisor Agent
    
    职责：
    1. 识别上游抽取内容中的实体类型
    2. 分发给对应的Subagent归一化
    3. 汇总结果
    """
    
    def __init__(self, llm_client: Any = None):
        self.llm_client = llm_client
        
        # 初始化所有Subagent
        self.subagents: Dict[str, BaseNormalizerAgent] = {
            'skill': SkillNormalizerAgent(llm_client),
            'position': PositionNormalizerAgent(llm_client),
            'career': CareerNormalizerAgent(llm_client),
        }
    
    def normalize_all(
        self,
        extracted_data: Dict[str, List[str]],
        context: str = "",
    ) -> Dict[str, BatchNormalizationResult]:
        """
        归一化所有实体
        
        Args:
            extracted_data: 抽取的数据，格式如：
                {
                    "skills": ["Python", "React", "dl"],
                    "positions": ["前端开发", "后端工程师"],
                    "careers": ["本科", "计算机科学"]
                }
            context: 上下文文本
        
        Returns:
            各类型的归一化结果
        """
        results: Dict[str, BatchNormalizationResult] = {}
        
        for entity_type, raw_list in extracted_data.items():
            if entity_type in self.subagents and raw_list:
                agent = self.subagents[entity_type]
                results[entity_type] = agent.normalize_batch(raw_list, context)
        
        return results
    
    def register_subagent(self, entity_type: str, agent: BaseNormalizerAgent):
        """注册新的Subagent"""
        self.subagents[entity_type] = agent
    
    def get_stats(self, results: Dict[str, BatchNormalizationResult]) -> Dict[str, Any]:
        """获取汇总统计"""
        total = 0
        known = 0
        normalized = 0
        unknown = 0
        
        for result in results.values():
            total += result.stats.get('total', 0)
            known += result.stats.get('known', 0)
            normalized += result.stats.get('normalized', 0)
            unknown += result.stats.get('unknown', 0)
        
        return {
            'total': total,
            'known': known,
            'normalized': normalized,
            'unknown': unknown,
            'coverage': normalized / total if total > 0 else 0,
        }


# ─────────────────────────────────────────────────────────
# 全局实例
# ─────────────────────────────────────────────────────────

_supervisor: Optional[NormalizationSupervisor] = None


def get_normalization_supervisor(llm_client: Any = None) -> NormalizationSupervisor:
    """获取归一化Supervisor单例"""
    global _supervisor
    if _supervisor is None:
        _supervisor = NormalizationSupervisor(llm_client)
    return _supervisor


def normalize_extracted_data(
    extracted_data: Dict[str, List[str]],
    context: str = "",
    llm_client: Any = None,
) -> Dict[str, BatchNormalizationResult]:
    """归一化抽取数据的便捷函数"""
    supervisor = get_normalization_supervisor(llm_client)
    return supervisor.normalize_all(extracted_data, context)
