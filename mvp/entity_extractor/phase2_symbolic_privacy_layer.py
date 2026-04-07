"""
Phase 2: 符号化隐私与实体锚定层 (Symbolic & Privacy Layer)
基于 NS-LE 架构 + 输入契约

执行顺序严格按契约：
raw_text
  → [1] 伦理过滤（ethics_bias_filter）
  → [2] PII 检测（regex 7类）
  → [3] PII 转换（按 rules/privacy_rules.py 策略）
  → [4] AC 算法技能匹配（skill_matcher + dictionaries/skill_lexicon）
  → [5] 日期/学位/角色正则预标注
  → [6] XML 标签注入
  → tagged_text（送入 Phase 3）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

try:
    from .dictionaries.pii_patterns import PII_PATTERNS, ETHICS_SENSITIVE_KEYWORDS
    from .dictionaries.entity_patterns import DATE_PATTERNS, DEGREE_KEYWORDS, ROLE_KEYWORDS
    from .dictionaries.skill_lexicon import get_all_skills
    from .rules.privacy_rules import PIITransformer
    from .skill_matcher import SkillMatcher
except ImportError:
    from dictionaries.pii_patterns import PII_PATTERNS, ETHICS_SENSITIVE_KEYWORDS
    from dictionaries.entity_patterns import DATE_PATTERNS, DEGREE_KEYWORDS, ROLE_KEYWORDS
    from dictionaries.skill_lexicon import get_all_skills
    from rules.privacy_rules import PIITransformer
    from skill_matcher import SkillMatcher

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class PIIDetection:
    """PII 检测结果"""
    pii_type: str
    original: str
    masked: str
    start: int
    end: int
    mask_info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PreAnnotation:
    """预标注结果"""
    tag_type: str   # skill / date / degree / role / req_skill / job_level / degree_req
    value: str
    start: int
    end: int


# ---------------------------------------------------------------------------
# [1] 伦理偏见过滤器
# ---------------------------------------------------------------------------

class EthicsBiasFilter:
    """
    契约 ethics_bias_filter:
    - execution_stage: raw_text_first_step
    - mask_fields: [gender, age, ethnicity, hometown]
    """

    def filter(self, text: str) -> Tuple[str, List[Dict[str, str]]]:
        """
        过滤伦理敏感词，返回 (filtered_text, detections)。
        不直接删除，而是用占位符替换。
        """
        detections: List[Dict[str, str]] = []

        for field_type, keywords in ETHICS_SENSITIVE_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    # 构建包含关键词的子句的正则
                    pattern = re.compile(
                        rf"(?:^|[，,。；;\n])\s*[^，,。；;\n]*{re.escape(kw)}[^，,。；;\n]*(?=[，,。；;\n]|$)",
                        re.MULTILINE,
                    )
                    for m in pattern.finditer(text):
                        detections.append({
                            "field": field_type,
                            "keyword": kw,
                            "matched": m.group().strip(),
                        })
                    # 简单替换关键词（保留上下文结构）
                    text = text.replace(kw, f"[{field_type.upper()}_MASKED]")

        if detections:
            logger.info("伦理过滤: 检测到 %d 个敏感项", len(detections))
        return text, detections


# ---------------------------------------------------------------------------
# [2] PII 检测器
# ---------------------------------------------------------------------------

class PIIDetector:
    """PII 正则检测器 — 覆盖契约 7 类"""

    def detect(self, text: str) -> List[PIIDetection]:
        """检测所有 PII，返回按位置排序的检测结果"""
        results: List[PIIDetection] = []

        for pii_type, pattern in PII_PATTERNS.items():
            for m in re.finditer(pattern, text):
                results.append(PIIDetection(
                    pii_type=pii_type,
                    original=m.group(),
                    masked="",  # 后续填充
                    start=m.start(),
                    end=m.end(),
                ))

        results.sort(key=lambda d: d.start)
        return results


# ---------------------------------------------------------------------------
# [4] 日期/学位/角色提取器
# ---------------------------------------------------------------------------

class EntityPreAnnotator:
    """确定性实体预标注器（正则）"""

    def find_dates(self, text: str) -> List[PreAnnotation]:
        results: List[PreAnnotation] = []
        for pat in DATE_PATTERNS:
            for m in re.finditer(pat, text):
                results.append(PreAnnotation(
                    tag_type="date",
                    value=m.group(),
                    start=m.start(),
                    end=m.end(),
                ))
        return results

    def find_degrees(self, text: str) -> List[PreAnnotation]:
        results: List[PreAnnotation] = []
        for kw in DEGREE_KEYWORDS:
            for m in re.finditer(re.escape(kw), text):
                results.append(PreAnnotation(
                    tag_type="degree",
                    value=kw,
                    start=m.start(),
                    end=m.end(),
                ))
        return results

    def find_roles(self, text: str) -> List[PreAnnotation]:
        results: List[PreAnnotation] = []
        for kw in ROLE_KEYWORDS:
            for m in re.finditer(re.escape(kw), text):
                results.append(PreAnnotation(
                    tag_type="role",
                    value=kw,
                    start=m.start(),
                    end=m.end(),
                ))
        return results


# ---------------------------------------------------------------------------
# [6] XML 标签注入器
# ---------------------------------------------------------------------------

class XMLInjector:
    """XML 标签注入器 — 支持简历标签和 JD 专用标签"""

    # JD 专用标签映射
    JD_TAG_MAP = {
        "req_skill": "req_skill",
        "job_level": "job_level",
        "degree_req": "degree_req",
    }

    @staticmethod
    def _remove_overlaps(annotations: List[PreAnnotation]) -> List[PreAnnotation]:
        """
        去除重叠标注：优先保留更长的匹配。
        解决 "Spring" 与 "Spring Boot" 重叠、"C" 误匹配等问题。
        """
        # 按 span 长度降序，相同长度按 start 升序
        sorted_anns = sorted(annotations, key=lambda a: (-(a.end - a.start), a.start))
        kept: List[PreAnnotation] = []
        occupied: List[tuple] = []  # (start, end) 已占用的区间

        for a in sorted_anns:
            overlaps = any(
                not (a.end <= os or a.start >= oe)
                for os, oe in occupied
            )
            if not overlaps:
                kept.append(a)
                occupied.append((a.start, a.end))

        return kept

    @staticmethod
    def inject(text: str, annotations: List[PreAnnotation]) -> str:
        """
        按位置倒序注入 XML 标签（避免位置偏移）。
        先去重和去除重叠，再注入。
        """
        # 去重
        seen = set()
        unique: List[PreAnnotation] = []
        for a in annotations:
            key = (a.start, a.end, a.tag_type)
            if key not in seen:
                seen.add(key)
                unique.append(a)

        # 去除重叠
        unique = XMLInjector._remove_overlaps(unique)

        # 按位置倒序注入
        unique.sort(key=lambda a: a.start, reverse=True)

        for a in unique:
            tag = a.tag_type
            text = (
                text[:a.start]
                + f"<{tag}>{text[a.start:a.end]}</{tag}>"
                + text[a.end:]
            )
        return text


# ---------------------------------------------------------------------------
# SymbolicPreprocessor — 统一入口
# ---------------------------------------------------------------------------

class SymbolicPreprocessor:
    """
    Phase 2 符号化预处理器：整合伦理过滤 + PII检测转换 + AC技能匹配 + 预标注 + XML注入。

    严格按契约执行顺序处理。
    """

    def __init__(self, doc_type: str = "resume"):
        self.doc_type = doc_type
        self.ethics_filter = EthicsBiasFilter()
        self.pii_detector = PIIDetector()
        self.pii_transformer = PIITransformer()
        self.skill_matcher = SkillMatcher(lexicon=get_all_skills())
        self.entity_annotator = EntityPreAnnotator()

    def process(self, text: str) -> Dict[str, Any]:
        """
        完整处理流水线。

        Returns:
            {
                "original_text": str,
                "masked_text": str,
                "tagged_text": str,
                "ethics_detections": [...],
                "privacy_masks": [...],
                "pre_extracted": {
                    "skills": [...],
                    "dates": [...],
                    "degrees": [...],
                    "roles": [...],
                },
            }
        """
        original_text = text

        # [1] 伦理过滤
        text, ethics_detections = self.ethics_filter.filter(text)

        # [2] PII 检测
        pii_list = self.pii_detector.detect(text)

        # [3] PII 转换（按位置倒序替换）
        privacy_masks: List[Dict[str, Any]] = []
        for det in sorted(pii_list, key=lambda d: d.start, reverse=True):
            masked, mask_info = self.pii_transformer.transform(det.pii_type, det.original)
            det.masked = masked
            det.mask_info = mask_info
            text = text[:det.start] + masked + text[det.end:]
            privacy_masks.append({
                "pii_type": det.pii_type,
                "original": det.original,
                "masked": masked,
                **mask_info,
            })

        masked_text = text

        # [4] AC 算法技能匹配
        matched_skills = self.skill_matcher.match(masked_text)
        skill_annotations: List[PreAnnotation] = []
        for skill in matched_skills:
            for m in re.finditer(re.escape(skill), masked_text, re.IGNORECASE):
                tag = "skill" if self.doc_type == "resume" else "req_skill"
                skill_annotations.append(PreAnnotation(
                    tag_type=tag,
                    value=skill,
                    start=m.start(),
                    end=m.end(),
                ))

        # [5] 日期/学位/角色预标注
        date_annotations = self.entity_annotator.find_dates(masked_text)
        degree_annotations = self.entity_annotator.find_degrees(masked_text)
        role_annotations = self.entity_annotator.find_roles(masked_text)

        # JD 专用：学位要求标签
        if self.doc_type == "jd":
            for a in degree_annotations:
                a.tag_type = "degree_req"

        # 汇总所有标注
        all_annotations = (
            skill_annotations
            + date_annotations
            + degree_annotations
            + role_annotations
        )

        # [6] XML 标签注入
        tagged_text = XMLInjector.inject(masked_text, all_annotations)

        return {
            "original_text": original_text,
            "masked_text": masked_text,
            "tagged_text": tagged_text,
            "ethics_detections": ethics_detections,
            "privacy_masks": privacy_masks,
            "pre_extracted": {
                "skills": matched_skills,
                "dates": [a.value for a in date_annotations],
                "degrees": [a.value for a in degree_annotations],
                "roles": [a.value for a in role_annotations],
            },
        }
