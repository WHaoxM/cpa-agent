"""
隐私转换规则 — 严格对齐契约 privacy_guard.transformation

转换策略：
- PHONE       → TOKENIZE（可逆令牌）
- EMAIL       → TOKENIZE
- ID_CARD     → DROP
- ADDRESS     → GENERALIZE_CITY_LEVEL（正则保留城市级别）
- PERSON_NAME → PSEUDONYMIZE（假名化）
- STUDENT_ID  → HMAC_SHA256
- COMPANY_INTERNAL_ID → TOKENIZE
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class TransformStrategy(Enum):
    """PII 转换策略"""
    TOKENIZE = "TOKENIZE"
    DROP = "DROP"
    GENERALIZE_CITY_LEVEL = "GENERALIZE_CITY_LEVEL"
    PSEUDONYMIZE = "PSEUDONYMIZE"
    HMAC_SHA256 = "HMAC_SHA256"


# 契约定义的 PII 类型 → 转换策略映射
PII_TRANSFORM_MAP: Dict[str, TransformStrategy] = {
    "PHONE": TransformStrategy.TOKENIZE,
    "EMAIL": TransformStrategy.TOKENIZE,
    "ID_CARD": TransformStrategy.DROP,
    "ADDRESS": TransformStrategy.GENERALIZE_CITY_LEVEL,
    "PERSON_NAME": TransformStrategy.PSEUDONYMIZE,
    "STUDENT_ID": TransformStrategy.HMAC_SHA256,
    "COMPANY_INTERNAL_ID": TransformStrategy.TOKENIZE,
}


class PIITransformer:
    """
    PII 转换器：按契约策略对每种 PII 类型执行相应脱敏。

    特点：
    - TOKENIZE: 生成可逆令牌（同一原始值 → 同一令牌，跨调用一致）
    - HMAC_SHA256: 使用密钥的 HMAC（同一实体在多轮中保持一致）
    - PSEUDONYMIZE: 姓名假名化
    - GENERALIZE_CITY_LEVEL: 地址泛化到城市级别
    """

    # 假名池（姓名假名化用）
    _PSEUDO_SURNAMES = ["张", "李", "王", "刘", "陈", "杨", "黄", "赵"]
    _PSEUDO_NAMES = ["某", "某某", "同学", "先生", "女士"]

    def __init__(self, hmac_key: Optional[str] = None):
        self._hmac_key = (hmac_key or secrets.token_hex(16)).encode("utf-8")
        self._token_cache: Dict[str, str] = {}
        self._pseudo_counter = 0

    def transform(self, pii_type: str, original: str) -> Tuple[str, Dict[str, Any]]:
        """
        对 PII 执行转换。

        Args:
            pii_type: PII 类型（如 "PHONE"）
            original: 原始文本

        Returns:
            (masked_text, mask_info)
        """
        strategy = PII_TRANSFORM_MAP.get(pii_type, TransformStrategy.TOKENIZE)

        if strategy == TransformStrategy.TOKENIZE:
            return self._tokenize(pii_type, original)
        elif strategy == TransformStrategy.DROP:
            return self._drop(pii_type)
        elif strategy == TransformStrategy.GENERALIZE_CITY_LEVEL:
            return self._generalize_city(original)
        elif strategy == TransformStrategy.PSEUDONYMIZE:
            return self._pseudonymize(original)
        elif strategy == TransformStrategy.HMAC_SHA256:
            return self._hmac_sha256(pii_type, original)
        else:
            return self._tokenize(pii_type, original)

    def _tokenize(self, pii_type: str, original: str) -> Tuple[str, Dict]:
        """可逆令牌化：同一原始值 → 同一令牌"""
        cache_key = f"{pii_type}:{original}"
        if cache_key not in self._token_cache:
            short_hash = hashlib.sha256(original.encode()).hexdigest()[:8]
            self._token_cache[cache_key] = f"[{pii_type}_{short_hash}]"

        masked = self._token_cache[cache_key]
        return masked, {
            "strategy": "TOKENIZE",
            "pii_type": pii_type,
            "token": masked,
            "reversible": True,
        }

    def _drop(self, pii_type: str) -> Tuple[str, Dict]:
        """直接丢弃"""
        masked = f"[{pii_type}_REDACTED]"
        return masked, {
            "strategy": "DROP",
            "pii_type": pii_type,
            "reversible": False,
        }

    def _generalize_city(self, original: str) -> Tuple[str, Dict]:
        """地址泛化到城市级别"""
        # 尝试提取省市
        city_match = re.search(
            r"([\u4e00-\u9fa5]{2,6}(?:省|自治区))?\s*([\u4e00-\u9fa5]{2,8}(?:市))",
            original,
        )
        if city_match:
            parts = [p for p in city_match.groups() if p]
            masked = "".join(parts)
        else:
            masked = "[ADDRESS_GENERALIZED]"

        return masked, {
            "strategy": "GENERALIZE_CITY_LEVEL",
            "pii_type": "ADDRESS",
            "generalized": masked,
        }

    def _pseudonymize(self, original: str) -> Tuple[str, Dict]:
        """姓名假名化"""
        idx = self._pseudo_counter % len(self._PSEUDO_SURNAMES)
        self._pseudo_counter += 1
        pseudo = self._PSEUDO_SURNAMES[idx] + "某"
        return pseudo, {
            "strategy": "PSEUDONYMIZE",
            "pii_type": "PERSON_NAME",
            "pseudo": pseudo,
        }

    def _hmac_sha256(self, pii_type: str, original: str) -> Tuple[str, Dict]:
        """HMAC-SHA256 哈希（跨轮次一致）"""
        mac = hmac.new(self._hmac_key, original.encode(), hashlib.sha256)
        hash_val = mac.hexdigest()[:12]
        masked = f"[{pii_type}_{hash_val}]"
        return masked, {
            "strategy": "HMAC_SHA256",
            "pii_type": pii_type,
            "hash": hash_val,
            "reversible": False,
        }
