"""LLM语义匹配器 - 理解语义相似的内容

使用LLM作为中间匹配层，把语义相似的内容归一化。
配合小型关键词库使用，实现混合匹配方法。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from config.loader import get_grep_config, get_extraction_config

logger = logging.getLogger(__name__)


class SemanticMatcher:
    """基于LLM的语义匹配器"""

    def __init__(self, llm_client: Any = None):
        """
        Args:
            llm_client: LLM客户端，需要有chat方法。如果为None，则禁用语义匹配。
        """
        self.llm_client = llm_client
        self.grep_config = get_grep_config()
        self.extraction_config = get_extraction_config()
        self.semantic_config = self.grep_config.get('semantic_matching', {})
        self.enabled = self.semantic_config.get('enabled', False) and llm_client is not None

    def find_semantically_similar(
        self,
        query: str,
        candidate_texts: List[str],
        full_text: str,
        category: str = "education",
    ) -> Dict[str, Any]:
        """
        使用LLM找到语义相似的内容

        Args:
            query: 用户查询（如"教育背景"）
            candidate_texts: 关键词匹配的候选文本
            full_text: 完整文本
            category: 类别（education/experience/projects/skills）

        Returns:
            语义匹配结果 {
                "matched_segments": [...],
                "extracted_info": {...},
                "confidence": 0.9
            }
        """
        if not self.enabled:
            return {
                "matched_segments": [],
                "extracted_info": {},
                "confidence": 0.0,
                "reason": "semantic_matching_disabled",
            }

        # 从配置加载类别信息
        category_config = self.semantic_config.get('categories', {}).get(category, {})
        semantic_variants = category_config.get('semantic_variants', [])
        description = category_config.get('description', category)

        # 构建候选文本摘要（限制长度）
        candidates_summary = self._format_candidates(candidate_texts)

        # 限制full_text长度，避免token溢出
        full_text_preview = full_text[:3000] if len(full_text) > 3000 else full_text

        # 构建LLM prompt
        prompt = f"""你是一个简历分析专家。请帮我找到与"{query}"语义相关的内容。

**类别信息**：
- 类别：{category}
- 描述：{description}
- 语义变体：{', '.join(semantic_variants[:5])}

**候选文本**（已通过关键词匹配找到）：
{candidates_summary}

**完整文本**（供参考）：
{full_text_preview}

**任务**：
1. 判断候选文本是否与"{query}"语义相关
2. 如果关键词匹配的候选文本不完整，从完整文本中补充相关内容
3. 提取结构化信息

**输出格式**（JSON）：
{{
  "matched_segments": [
    {{
      "text": "匹配的文本内容",
      "relevance_score": 0.95,
      "is_exact_match": true,
      "semantic_reason": "为什么认为语义相关"
    }}
  ],
  "extracted_info": {{
    // 根据类别提取结构化信息
  }},
  "confidence": 0.9
}}

只输出JSON，不要输出其他内容。"""

        try:
            # 调用LLM
            response = self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1500,
            )

            # 解析结果
            return self._parse_response(response)

        except Exception as e:
            logger.warning("semantic matching failed: %s", e)
            return {
                "matched_segments": [],
                "extracted_info": {},
                "confidence": 0.0,
                "reason": f"llm_error: {e}",
            }

    def _format_candidates(self, candidates: List[str]) -> str:
        """格式化候选文本"""
        if not candidates:
            return "无候选文本（关键词未匹配到任何内容）"

        formatted = []
        for i, candidate in enumerate(candidates[:5], 1):
            text = candidate[:200] + "..." if len(candidate) > 200 else candidate
            formatted.append(f"{i}. {text}")

        return "\n".join(formatted)

    def _parse_response(self, response: str) -> Dict[str, Any]:
        """解析LLM响应"""
        if not response:
            return {
                "matched_segments": [],
                "extracted_info": {},
                "confidence": 0.0,
                "reason": "empty_response",
            }

        try:
            data = json.loads(response)
            return data
        except (json.JSONDecodeError, ValueError):
            # 尝试从文本中提取JSON
            try:
                # 找到第一个{和最后一个}
                start = response.find('{')
                end = response.rfind('}')
                if start >= 0 and end > start:
                    json_str = response[start:end + 1]
                    return json.loads(json_str)
            except (json.JSONDecodeError, ValueError):
                pass

            logger.warning("failed to parse semantic matching response: %s", response[:200])
            return {
                "matched_segments": [],
                "extracted_info": {},
                "confidence": 0.0,
                "reason": "parse_error",
            }


def create_semantic_matcher(llm_client: Any = None) -> SemanticMatcher:
    """创建语义匹配器的工厂函数"""
    return SemanticMatcher(llm_client=llm_client)