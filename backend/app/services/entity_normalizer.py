"""
实体归一化服务 — BGE-M3 语义对齐 + OOV 闭环

架构:
  LLM 抽取原始实体 → 精确匹配 → 别名匹配 → BGE-M3 语义对齐 → 归一化
  → 若无匹配 (OOV) → LLM 结构化总结 → 写入 MySQL + Milvus (闭环)

阈值 (来源: backend/config/defaults/rules/thresholds.yaml, workspace 可白名单覆盖):
  cosine > alias_threshold  → alias (归一化到已有规范名)
  related_threshold < cosine ≤ alias_threshold → related (相关但不等价, 保留原名但记录关联)
  cosine ≤ related_threshold  → novel (OOV, 触发 Agent 闭环写入)

支持的实体类型: skill, position, major, certificate
"""

import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

from ..utils.logger import get_logger
from config.loader import get_rules_config
from config.prompt_loader import load_prompt

logger = get_logger('cpa-agent.entity_normalizer')

# ── Milvus collection 映射 ──
ENTITY_COLLECTION_MAP = {
    "skill": "skill_vectors",
    "position": "jd_vectors",
    "major": "skill_vectors",
    "certificate": "skill_vectors",
    "degree": "skill_vectors",
    "award": "skill_vectors",
}


def _thresholds():
    """从 ConfigLoader 读取阈值（每次调用读取以支持 workspace 热覆盖）"""
    rules = get_rules_config()
    en = rules.thresholds["entity_normalizer"]
    oov = rules.thresholds["oov_frequency"]
    return en["alias"], en["related"], oov["observe"], oov["review"]


# ── 向后兼容的模块级常量（首次访问时从 config 读取）──
# 优先使用 _thresholds() 获取最新值；这些常量保留供旧代码引用
ALIAS_THRESHOLD, RELATED_THRESHOLD, OOV_FREQ_OBSERVE, OOV_FREQ_REVIEW = _thresholds()


class NormalizeResult:
    """归一化结果"""

    def __init__(
        self,
        original: str,
        canonical: str,
        entity_type: str,
        match_type: str,       # exact / alias / semantic / novel
        similarity: float,
        category: Optional[str] = None,
        canonical_id: Optional[int] = None,
        is_oov: bool = False,
        related_entities: Optional[List[Dict]] = None,
    ):
        self.original = original
        self.canonical = canonical
        self.entity_type = entity_type
        self.match_type = match_type
        self.similarity = similarity
        self.category = category
        self.canonical_id = canonical_id
        self.is_oov = is_oov
        self.related_entities = related_entities or []

    def to_dict(self) -> Dict:
        return {
            "original": self.original,
            "canonical": self.canonical,
            "entity_type": self.entity_type,
            "match_type": self.match_type,
            "similarity": round(self.similarity, 4),
            "category": self.category,
            "canonical_id": self.canonical_id,
            "is_oov": self.is_oov,
            "related_entities": self.related_entities,
        }


class EntityNormalizer:
    """
    实体归一化引擎

    三级匹配策略:
    1. 精确匹配 — MySQL canonical_entity 表直接命中
    2. 别名匹配 — MySQL aliases_json 字段展开匹配
    3. 语义匹配 — BGE-M3 + Milvus 向量检索

    OOV 闭环:
    - cosine ≤ 0.70 → novel → LLM 结构化分类 → 写入 MySQL + Milvus
    """

    _instance: Optional['EntityNormalizer'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._mysql = None
        self._milvus = None
        self._embedding = None
        self._llm_client = None
        self._canonical_cache: Dict[str, Dict[str, List[Dict]]] = {}
        self._alias_cache: Dict[str, Dict[str, int]] = {}
        self._rule_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._cache_loaded = False
        self._rule_cache_loaded = False
        self._cache_loaded_at: float = 0.0  # D-12: 缓存加载时间戳
        self._rule_cache_loaded_at: float = 0.0
        self._CACHE_TTL = 3600  # D-12: 缓存有效期 1 小时
        self._initialized = True

    # ── 延迟加载依赖 ──

    @property
    def mysql(self):
        if self._mysql is None:
            from .db import get_mysql_client
            self._mysql = get_mysql_client()
        return self._mysql

    @property
    def milvus(self):
        if self._milvus is None:
            from .db import get_milvus_client
            self._milvus = get_milvus_client()
        return self._milvus

    @property
    def embedding(self):
        if self._embedding is None:
            from .embedding_service import get_embedding_service
            self._embedding = get_embedding_service()
        return self._embedding

    @property
    def llm_client(self):
        if self._llm_client is None:
            from ..utils.llm_client import get_llm_client
            self._llm_client = get_llm_client()
        return self._llm_client

    # ── 缓存加载 ──

    def _ensure_cache(self):
        """从 MySQL 加载规范实体缓存与规则缓存 (带 TTL, D-12)"""
        import time
        now = time.time()
        if self._cache_loaded and (now - self._cache_loaded_at) < self._CACHE_TTL:
            self._ensure_rule_cache()
            return
        try:
            self._load_canonical_cache()
            self._cache_loaded = True
            self._cache_loaded_at = now
            self._ensure_rule_cache()
        except Exception as e:
            logger.warning(f"规范实体缓存加载失败: {e}, 将使用降级模式")
            self._cache_loaded = True  # 防止反复重试

    def _ensure_rule_cache(self):
        """从 MySQL 加载外部规则缓存"""
        import time
        now = time.time()
        if self._rule_cache_loaded and (now - self._rule_cache_loaded_at) < self._CACHE_TTL:
            return
        try:
            self._load_rule_cache()
            self._rule_cache_loaded = True
            self._rule_cache_loaded_at = now
        except Exception as e:
            logger.warning(f"外部规则缓存加载失败: {e}, 将使用无规则降级模式")
            self._rule_cache_loaded = True

    def _load_canonical_cache(self):
        """加载 MySQL canonical_entity 到内存缓存"""
        try:
            self._canonical_cache.clear()
            self._alias_cache.clear()
            rows = self.mysql.execute(
                "SELECT id, entity_type, canonical_name, category, aliases_json FROM canonical_entity"
            )
            for row in rows:
                etype = row["entity_type"]
                if etype not in self._canonical_cache:
                    self._canonical_cache[etype] = {}
                    self._alias_cache[etype] = {}

                name = row["canonical_name"]
                self._canonical_cache[etype][name.lower()] = {
                    "id": row["id"],
                    "canonical_name": name,
                    "category": row.get("category"),
                }

                # 别名索引
                aliases_raw = row.get("aliases_json")
                if aliases_raw:
                    try:
                        aliases = json.loads(aliases_raw) if isinstance(aliases_raw, str) else aliases_raw
                        for alias in aliases:
                            if isinstance(alias, str):
                                self._alias_cache[etype][alias.lower()] = row["id"]
                    except (json.JSONDecodeError, TypeError):
                        pass

            total = sum(len(v) for v in self._canonical_cache.values())
            alias_total = sum(len(v) for v in self._alias_cache.values())
            logger.info(f"规范实体缓存加载: {total} 规范名, {alias_total} 别名")

        except Exception as e:
            logger.warning(f"MySQL canonical_entity 表可能不存在: {e}")

    def _load_rule_cache(self):
        """加载 MySQL normalization_rule 到内存缓存"""
        self._rule_cache.clear()
        try:
            rows = self.mysql.execute(
                """SELECT rule_domain, match_mode, pattern, canonical_value, priority
                   FROM normalization_rule
                   WHERE enabled = 1
                   ORDER BY rule_domain, priority DESC, id ASC"""
            )
            for row in rows:
                domain = str(row.get("rule_domain", "")).strip()
                if not domain:
                    continue
                self._rule_cache.setdefault(domain, []).append({
                    "match_mode": str(row.get("match_mode", "contains") or "contains").strip().lower(),
                    "pattern": str(row.get("pattern", "") or "").strip(),
                    "canonical_value": str(row.get("canonical_value", "") or "").strip(),
                    "priority": int(row.get("priority", 100) or 100),
                })

            rule_total = sum(len(v) for v in self._rule_cache.values())
            logger.info(f"外部规则缓存加载: {rule_total} 条, 域={list(self._rule_cache.keys())}")
        except Exception as e:
            logger.warning(f"MySQL normalization_rule 表可能不存在: {e}")

    def reload_cache(self):
        """强制重新加载缓存"""
        self._cache_loaded = False
        self._rule_cache_loaded = False
        self._canonical_cache.clear()
        self._alias_cache.clear()
        self._rule_cache.clear()
        self._ensure_cache()

    @staticmethod
    def _compact_text(text: str) -> str:
        import re
        return re.sub(r"[\s\-_/(),，。．（）·]+", "", str(text or "")).upper()

    def _rule_hit(self, value: str, pattern: str, match_mode: str) -> bool:
        if not pattern:
            return False
        value_s = str(value or "").strip()
        pattern_s = str(pattern or "").strip()
        if not value_s or not pattern_s:
            return False

        if match_mode == "exact":
            return value_s.lower() == pattern_s.lower()
        if match_mode == "compact_exact":
            return self._compact_text(value_s) == self._compact_text(pattern_s)
        if match_mode == "compact_contains":
            v = self._compact_text(value_s)
            p = self._compact_text(pattern_s)
            return p in v or v in p
        # default: contains
        return pattern_s.lower() in value_s.lower() or value_s.lower() in pattern_s.lower()

    def apply_external_rule(self, value: str, rule_domain: str) -> str:
        """应用外部规则表 normalization_rule，未命中时返回原值"""
        raw = str(value or "").strip()
        if not raw:
            return raw
        self._ensure_rule_cache()
        rules = self._rule_cache.get(rule_domain, [])
        for rule in rules:
            if self._rule_hit(raw, rule.get("pattern", ""), rule.get("match_mode", "contains")):
                canonical = rule.get("canonical_value", "")
                return canonical or raw
        return raw

    # ── 核心归一化 ──

    def normalize(
        self,
        name: str,
        entity_type: str,
        context: Optional[str] = None,
        auto_register: bool = True,
    ) -> NormalizeResult:
        """
        归一化单个实体

        Args:
            name: 原始实体名 (LLM 抽取结果)
            entity_type: skill / position / major / certificate
            context: 原始文本上下文 (用于 LLM 分类)
            auto_register: OOV 时是否自动注册到数据库

        Returns:
            NormalizeResult
        """
        if not name or not name.strip():
            return NormalizeResult(
                original=name, canonical=name, entity_type=entity_type,
                match_type="empty", similarity=0.0,
            )

        name = name.strip()
        self._ensure_cache()

        # ── Level 1: 精确匹配 ──
        exact = self._exact_match(name, entity_type)
        if exact:
            return NormalizeResult(
                original=name, canonical=exact["canonical_name"],
                entity_type=entity_type, match_type="exact",
                similarity=1.0, category=exact.get("category"),
                canonical_id=exact["id"],
            )

        # ── Level 2: 别名匹配 ──
        alias_hit = self._alias_match(name, entity_type)
        if alias_hit:
            canonical = self._canonical_cache[entity_type].get(
                self._id_to_key(entity_type, alias_hit), {}
            )
            return NormalizeResult(
                original=name, canonical=canonical.get("canonical_name", name),
                entity_type=entity_type, match_type="alias",
                similarity=0.95, category=canonical.get("category"),
                canonical_id=alias_hit,
            )

        # ── Level 3: 语义匹配 (BGE-M3 + Milvus) ──
        semantic = self._semantic_match(name, entity_type)
        if semantic:
            best = semantic[0]
            sim = best["similarity"]
            canonical_name = best["canonical_name"]
            canonical_id = best.get("canonical_id")

            alias_thr, related_thr, _, _ = _thresholds()
            if sim >= alias_thr:
                # alias — 归一化到已有规范名
                result = NormalizeResult(
                    original=name, canonical=canonical_name,
                    entity_type=entity_type, match_type="semantic_alias",
                    similarity=sim, category=best.get("category"),
                    canonical_id=canonical_id,
                )
                # 自动将原名注册为别名
                if canonical_id and auto_register:
                    self._register_alias(canonical_id, name, entity_type)
                return result

            elif sim >= related_thr:
                # related — 保留原名但记录关联
                return NormalizeResult(
                    original=name, canonical=name,
                    entity_type=entity_type, match_type="related",
                    similarity=sim, category=best.get("category"),
                    related_entities=semantic[:3],
                )

        # ── Level 4: OOV — 闭环注册 ──
        if auto_register:
            return self._register_oov(name, entity_type, context)

        return NormalizeResult(
            original=name, canonical=name, entity_type=entity_type,
            match_type="novel", similarity=0.0, is_oov=True,
        )

    def normalize_batch(
        self,
        entities: List[Dict],
        entity_type: str,
        context: Optional[str] = None,
    ) -> List[Dict]:
        """
        批量归一化

        Args:
            entities: [{"name": "...", "level": "...", ...}, ...]
            entity_type: skill / position / major / certificate
            context: 原始文本上下文

        Returns:
            归一化后的实体列表 (canonical 替换 name)
        """
        results = []
        for entity in entities:
            name = entity.get("name", "")
            norm = self.normalize(name, entity_type, context)

            normalized = dict(entity)
            normalized["original_name"] = name
            normalized["name"] = norm.canonical
            normalized["match_type"] = norm.match_type
            normalized["match_similarity"] = norm.similarity
            normalized["is_oov"] = norm.is_oov

            if norm.category and "category" not in normalized:
                normalized["category"] = norm.category

            results.append(normalized)

        return results

    # ── Level 1: 精确匹配 ──

    def _exact_match(self, name: str, entity_type: str) -> Optional[Dict]:
        cache = self._canonical_cache.get(entity_type, {})
        return cache.get(name.lower())

    # ── Level 2: 别名匹配 ──

    def _alias_match(self, name: str, entity_type: str) -> Optional[int]:
        alias_map = self._alias_cache.get(entity_type, {})
        return alias_map.get(name.lower())

    def _id_to_key(self, entity_type: str, canonical_id: int) -> Optional[str]:
        """通过 ID 反查 canonical_name (用于别名命中后获取规范名)"""
        for key, val in self._canonical_cache.get(entity_type, {}).items():
            if val.get("id") == canonical_id:
                return key
        return None

    # ── Level 3: 语义匹配 ──

    def _semantic_match(self, name: str, entity_type: str) -> List[Dict]:
        """BGE-M3 + Milvus 语义检索"""
        collection = ENTITY_COLLECTION_MAP.get(entity_type)
        if not collection:
            return []
        if not self.embedding.is_available():
            return []

        try:
            query_vec = self.embedding.encode_single(name)

            if not self.milvus.test_connection():
                return []

            results = self.milvus.search(
                collection_name=collection,
                query_vector=query_vec,
                top_k=5,
            )

            matched = []
            _, related_thr, _, _ = _thresholds()
            for hit in results:
                distance = hit.get("distance", 0)
                if distance < related_thr:
                    continue

                metadata_raw = hit.get("metadata", "{}")
                try:
                    metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

                canonical_name = metadata.get("skill_name") or metadata.get("position_name") or hit.get("text", "")
                category = metadata.get("category", "")

                matched.append({
                    "canonical_name": canonical_name,
                    "similarity": distance,
                    "category": category,
                    "canonical_id": metadata.get("canonical_id"),
                })

            matched.sort(key=lambda x: x["similarity"], reverse=True)
            return matched

        except Exception as e:
            logger.warning(f"语义匹配失败 [{entity_type}/{name}]: {e}")
            return []

    # ── Level 4: OOV 闭环注册 ──

    def _register_oov(
        self,
        name: str,
        entity_type: str,
        context: Optional[str] = None,
    ) -> NormalizeResult:
        """
        OOV 实体闭环注册:
        1. LLM 结构化分类 (category + aliases)
        2. 写入 MySQL canonical_entity
        3. 写入 Milvus 向量
        4. 更新内存缓存
        """
        # LLM 分类
        classification = self._llm_classify(name, entity_type, context)

        canonical_name = classification.get("canonical_name", name)
        category = classification.get("category", "其他")
        aliases = classification.get("aliases", [])

        # 写入 MySQL
        try:
            self.mysql.execute(
                """INSERT INTO canonical_entity
                   (entity_type, canonical_name, category, aliases_json, frequency, source, created_at)
                   VALUES (:entity_type, :canonical_name, :category, :aliases_json, 1, :source, :created_at)""",
                {
                    "entity_type": entity_type,
                    "canonical_name": canonical_name,
                    "category": category,
                    "aliases_json": json.dumps(aliases + [name], ensure_ascii=False),
                    "source": "oov_auto",
                    "created_at": datetime.now(),
                },
                commit=True,
            )

            row = self.mysql.execute_single(
                "SELECT id, canonical_name, category FROM canonical_entity WHERE canonical_name = :name AND entity_type = :etype ORDER BY id DESC LIMIT 1",
                {"name": canonical_name, "etype": entity_type},
            )

            canonical_id = row["id"] if row else None

            # 写入 Milvus
            self._store_to_milvus(canonical_name, entity_type, category, canonical_id)

            # 更新缓存
            if entity_type not in self._canonical_cache:
                self._canonical_cache[entity_type] = {}
                self._alias_cache[entity_type] = {}
            self._canonical_cache[entity_type][canonical_name.lower()] = {
                "id": canonical_id,
                "canonical_name": canonical_name,
                "category": category,
            }
            for alias in aliases + [name]:
                if canonical_id:
                    self._alias_cache[entity_type][alias.lower()] = canonical_id

            logger.info(f"OOV 注册: [{entity_type}] {name} → {canonical_name} (id={canonical_id})")

            return NormalizeResult(
                original=name, canonical=canonical_name,
                entity_type=entity_type, match_type="novel",
                similarity=0.0, category=category,
                canonical_id=canonical_id, is_oov=True,
            )

        except Exception as e:
            logger.warning(f"OOV 注册失败 [{entity_type}/{name}]: {e}")
            return NormalizeResult(
                original=name, canonical=name,
                entity_type=entity_type, match_type="novel",
                similarity=0.0, category=category,
                is_oov=True,
            )

    def _register_alias(self, canonical_id: int, alias_name: str, entity_type: str):
        """将语义匹配到的原名注册为别名"""
        try:
            row = self.mysql.execute_single(
                "SELECT aliases_json FROM canonical_entity WHERE id = :id",
                {"id": canonical_id},
            )
            if not row:
                return

            aliases_raw = row.get("aliases_json", "[]")
            try:
                aliases = json.loads(aliases_raw) if isinstance(aliases_raw, str) else aliases_raw
            except (json.JSONDecodeError, TypeError):
                aliases = []

            if alias_name not in aliases and alias_name.lower() not in [a.lower() for a in aliases]:
                aliases.append(alias_name)
                self.mysql.execute(
                    "UPDATE canonical_entity SET aliases_json = :aliases WHERE id = :id",
                    {"aliases": json.dumps(aliases, ensure_ascii=False), "id": canonical_id},
                    commit=True,
                )

                # 更新缓存
                if entity_type not in self._alias_cache:
                    self._alias_cache[entity_type] = {}
                self._alias_cache[entity_type][alias_name.lower()] = canonical_id

                logger.debug(f"别名注册: [{entity_type}] {alias_name} → id={canonical_id}")

        except Exception as e:
            logger.warning(f"别名注册失败: {e}")

    def _store_to_milvus(
        self,
        name: str,
        entity_type: str,
        category: str,
        canonical_id: Optional[int] = None,
    ):
        """将规范实体向量写入 Milvus"""
        collection = ENTITY_COLLECTION_MAP.get(entity_type)
        if not collection:
            return

        try:
            if not self.milvus.test_connection():
                return

            vec = self.embedding.encode_single(name)
            metadata = {
                "entity_type": entity_type,
                "skill_name" if entity_type in ("skill", "major") else "position_name": name,
                "category": category,
                "canonical_id": canonical_id,
            }

            self.milvus.insert(
                collection_name=collection,
                texts=[name],
                vectors=[vec],
                metadata=[metadata],
            )

        except Exception as e:
            logger.warning(f"Milvus 写入失败 [{entity_type}/{name}]: {e}")

    # ── LLM 分类 ──

    def _llm_classify(self, name: str, entity_type: str, context: Optional[str] = None) -> Dict:
        """LLM 对 OOV 实体进行结构化分类"""
        type_labels = {
            "skill": "技能",
            "position": "职位",
            "major": "专业",
            "certificate": "证书",
            "award": "奖项",
        }
        label = type_labels.get(entity_type, "实体")

        # ── award 类型使用专用 prompt (D-09: 语义+LLM 归一化) ──
        if entity_type == "award":
            ctx_line = f"上下文: {context[:500]}" if context else ""
            prompt = load_prompt(
                "entity.normalize.user.award",
                name=name,
                ctx=ctx_line,
            )
        else:
            ctx_line = f"上下文: {context[:500]}" if context else ""
            prompt = load_prompt(
                "entity.normalize.user.generic",
                label=label,
                name=name,
                ctx=ctx_line,
            )

        try:
            response = self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=300,
            )

            content = str(response).strip()
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return {
                    "canonical_name": data.get("canonical_name", name),
                    "category": data.get("category", "其他"),
                    "aliases": data.get("aliases", []),
                }

        except Exception as e:
            logger.warning(f"LLM 分类失败 [{entity_type}/{name}]: {e}")

        # 降级: 原名保留
        return {"canonical_name": name, "category": "其他", "aliases": []}

    # ── 查询接口 ──

    def get_canonical_entities(self, entity_type: str) -> List[Dict]:
        """获取某类型的所有规范实体"""
        self._ensure_cache()
        return list(self._canonical_cache.get(entity_type, {}).values())

    def get_aliases_map(self, entity_type: str) -> Dict[str, str]:
        """获取别名→规范名映射"""
        self._ensure_cache()
        result = {}
        type_cache = self._canonical_cache.get(entity_type, {})
        alias_cache = self._alias_cache.get(entity_type, {})

        for alias_lower, canonical_id in alias_cache.items():
            for key, val in type_cache.items():
                if val.get("id") == canonical_id:
                    result[alias_lower] = val["canonical_name"]
                    break

        return result

    def get_categories(self, entity_type: str) -> List[str]:
        """获取某类型的所有分类"""
        self._ensure_cache()
        categories = set()
        for val in self._canonical_cache.get(entity_type, {}).values():
            cat = val.get("category")
            if cat:
                categories.add(cat)
        return sorted(categories)

    def increment_frequency(self, canonical_name: str, entity_type: str):
        """增加实体频次 (用于 OOV 频次阈值判定)"""
        try:
            self.mysql.execute(
                "UPDATE canonical_entity SET frequency = frequency + 1 WHERE canonical_name = :name AND entity_type = :etype",
                {"name": canonical_name, "etype": entity_type},
                commit=True,
            )
        except Exception as e:
            logger.warning(f"频次更新失败: {e}")


# ── MySQL 建表 ──

CANONICAL_ENTITY_DDL = """
CREATE TABLE IF NOT EXISTS `canonical_entity` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `entity_type` VARCHAR(32) NOT NULL COMMENT 'skill/position/major/certificate',
  `canonical_name` VARCHAR(128) NOT NULL COMMENT '规范名称',
  `category` VARCHAR(64) DEFAULT NULL COMMENT '分类',
  `aliases_json` JSON DEFAULT NULL COMMENT '别名列表',
  `frequency` INT DEFAULT 1 COMMENT '出现频次',
  `source` VARCHAR(32) DEFAULT 'seed' COMMENT 'seed/oov_auto/oov_review',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_type_name` (`entity_type`, `canonical_name`),
  KEY `idx_category` (`entity_type`, `category`),
  KEY `idx_frequency` (`frequency`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

NORMALIZATION_RULE_DDL = """
CREATE TABLE IF NOT EXISTS `normalization_rule` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `rule_domain` VARCHAR(64) NOT NULL COMMENT '规则域: degree/certificate_alias/award_level/major_alias/skill_alias',
  `match_mode` VARCHAR(32) NOT NULL DEFAULT 'contains' COMMENT 'exact/contains/compact_exact/compact_contains',
  `pattern` VARCHAR(255) NOT NULL COMMENT '触发模式',
  `canonical_value` VARCHAR(255) NOT NULL COMMENT '归一化结果',
  `priority` INT NOT NULL DEFAULT 100 COMMENT '优先级(大优先)',
  `enabled` TINYINT(1) NOT NULL DEFAULT 1,
  `source` VARCHAR(32) DEFAULT 'seed' COMMENT 'seed/admin/agent_auto',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY `idx_domain_priority` (`rule_domain`, `priority`),
  KEY `idx_enabled` (`enabled`),
  UNIQUE KEY `uk_domain_pattern` (`rule_domain`, `pattern`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def init_canonical_tables(mysql_client=None):
    """初始化 canonical_entity 与 normalization_rule 表"""
    if mysql_client is None:
        from .db import get_mysql_client
        mysql_client = get_mysql_client()

    try:
        mysql_client.execute(CANONICAL_ENTITY_DDL, commit=True)
        mysql_client.execute(NORMALIZATION_RULE_DDL, commit=True)
        logger.info("canonical_entity / normalization_rule 表已就绪")
    except Exception as e:
        logger.warning(f"canonical_entity/normalization_rule 建表失败: {e}")


def seed_canonical_entities(mysql_client=None):
    """
    从 skill_lexicon 种子数据初始化规范实体

    仅在表为空时执行, 不覆盖已有数据
    """
    if mysql_client is None:
        from .db import get_mysql_client
        mysql_client = get_mysql_client()

    try:
        count = mysql_client.execute_single(
            "SELECT COUNT(*) as cnt FROM canonical_entity"
        )
        if count and count["cnt"] > 0:
            logger.info(f"canonical_entity 已有 {count['cnt']} 条数据, 跳过种子")
            seed_external_rules(mysql_client)
            return

        from ..pipeline.entity_extractor.dictionaries.skill_lexicon import SKILL_CATEGORIES
        from config.dictionary_loader import (
            get_skill_category_map,
            get_common_majors,
            get_common_positions,
            get_degree_seeds,
            get_cert_seeds,
        )

        # category_map / common_majors / common_positions / degree_seeds / cert_seeds 走 YAML
        # (config/defaults/dictionaries/entity_seeds.yaml)
        category_map = get_skill_category_map()

        params = []
        for cat_en, skills in SKILL_CATEGORIES.items():
            cat_cn = category_map.get(cat_en, cat_en)
            for skill in skills:
                aliases = _get_skill_aliases(skill)
                params.append({
                    "entity_type": "skill",
                    "canonical_name": skill,
                    "category": cat_cn,
                    "aliases_json": json.dumps(aliases, ensure_ascii=False),
                    "frequency": 1,
                    "source": "seed",
                    "created_at": datetime.now(),
                })

        # 常见专业
        for major in get_common_majors():
            params.append({
                "entity_type": "major",
                "canonical_name": major,
                "category": "计算机/信息类",
                "aliases_json": json.dumps([], ensure_ascii=False),
                "frequency": 1,
                "source": "seed",
                "created_at": datetime.now(),
            })

        # 常见职位
        for pos in get_common_positions():
            params.append({
                "entity_type": "position",
                "canonical_name": pos,
                "category": "IT技术",
                "aliases_json": json.dumps([], ensure_ascii=False),
                "frequency": 1,
                "source": "seed",
                "created_at": datetime.now(),
            })

        # 学历层次 (degree) — {canonical: [aliases]}
        for canonical, aliases in get_degree_seeds().items():
            params.append({
                "entity_type": "degree",
                "canonical_name": canonical,
                "category": "学历",
                "aliases_json": json.dumps(aliases, ensure_ascii=False),
                "frequency": 1,
                "source": "seed",
                "created_at": datetime.now(),
            })

        # 常见证书 — [{canonical_name, category, aliases}]
        for cert in get_cert_seeds():
            params.append({
                "entity_type": "certificate",
                "canonical_name": cert["canonical_name"],
                "category": cert["category"],
                "aliases_json": json.dumps(cert.get("aliases", []), ensure_ascii=False),
                "frequency": 1,
                "source": "seed",
                "created_at": datetime.now(),
            })

        mysql_client.execute_many(
            """INSERT IGNORE INTO canonical_entity
               (entity_type, canonical_name, category, aliases_json, frequency, source, created_at)
               VALUES (:entity_type, :canonical_name, :category, :aliases_json, :frequency, :source, :created_at)""",
            params,
            commit=True,
        )

        logger.info(f"种子数据写入: {len(params)} 条规范实体")
        seed_external_rules(mysql_client)

    except Exception as e:
        logger.warning(f"种子数据写入失败: {e}")


def seed_external_rules(mysql_client=None):
    """初始化外部规则表 normalization_rule"""
    if mysql_client is None:
        from .db import get_mysql_client
        mysql_client = get_mysql_client()

    try:
        count = mysql_client.execute_single("SELECT COUNT(*) as cnt FROM normalization_rule")
        if count and count.get("cnt", 0) > 0:
            logger.info(f"normalization_rule 已有 {count['cnt']} 条规则, 跳过种子")
            return

        rule_rows = []

        # award level
        for pattern, canonical in [
            ("全国", "国家级"), ("国际", "国家级"), ("国家", "国家级"), ("部属", "国家级"),
            ("省", "省级"), ("市", "省级"), ("地区", "省级"),
            ("校", "校级"), ("院", "校级"),
        ]:
            rule_rows.append({
                "rule_domain": "award_level",
                "match_mode": "contains",
                "pattern": pattern,
                "canonical_value": canonical,
                "priority": 100,
                "source": "seed",
            })

        # major alias
        for pattern, canonical in [
            ("计算机科学", "计算机科学与技术"), ("计科", "计算机科学与技术"), ("CS", "计算机科学与技术"),
            ("软件工程", "软件工程"), ("软工", "软件工程"),
            ("智能科学", "人工智能"), ("AI", "人工智能"),
            ("大数据", "数据科学与大数据技术"), ("Data Science", "数据科学与大数据技术"),
            ("网络安全", "信息安全"), ("信安", "信息安全"),
        ]:
            rule_rows.append({
                "rule_domain": "major_alias",
                "match_mode": "contains",
                "pattern": pattern,
                "canonical_value": canonical,
                "priority": 90,
                "source": "seed",
            })

        # certificate compact alias
        for pattern, canonical in [
            ("大学英语六级", "CET-6"), ("英语六级", "CET-6"), ("CET6", "CET-6"),
            ("大学英语四级", "CET-4"), ("英语四级", "CET-4"), ("CET4", "CET-4"),
            ("全国计算机等级考试二级", "计算机二级"), ("NCRE二级", "计算机二级"),
            ("全国计算机等级考试三级", "计算机三级"), ("NCRE三级", "计算机三级"),
            ("PMP项目管理专业人士认证", "PMP"), ("项目管理专业人士认证", "PMP"),
            ("AWS Certified", "AWS认证"), ("AWS Certified Solutions Architect", "AWS认证"),
            ("Oracle数据库认证", "Oracle认证"), ("OCA", "Oracle认证"),
        ]:
            rule_rows.append({
                "rule_domain": "certificate_alias",
                "match_mode": "compact_contains",
                "pattern": pattern,
                "canonical_value": canonical,
                "priority": 100,
                "source": "seed",
            })

        mysql_client.execute_many(
            """INSERT IGNORE INTO normalization_rule
               (rule_domain, match_mode, pattern, canonical_value, priority, enabled, source)
               VALUES (:rule_domain, :match_mode, :pattern, :canonical_value, :priority, 1, :source)""",
            rule_rows,
            commit=True,
        )
        logger.info(f"外部规则写入: {len(rule_rows)} 条")
    except Exception as e:
        logger.warning(f"外部规则写入失败: {e}")


def _get_skill_aliases(skill_name: str) -> List[str]:
    """获取技能的常见别名"""
    alias_map = {
        "JavaScript": ["JS", "ECMAScript"],
        "TypeScript": ["TS"],
        "Kubernetes": ["K8S", "K8s"],
        "Vue": ["Vue.js", "Vue3", "Vue2"],
        "React": ["React.js", "ReactJS"],
        "Angular": ["NG", "AngularJS"],
        "Spring Boot": ["SpringBoot"],
        "PyTorch": ["Torch"],
        "TensorFlow": ["TFS"],
        "PostgreSQL": ["Postgres", "PG"],
        "MySQL": ["Mysql"],
        "机器学习": ["ML"],
        "深度学习": ["DL"],
        "自然语言处理": ["NLP"],
        "计算机视觉": ["CV"],
        "Docker": ["容器化"],
        "CI/CD": ["持续集成", "持续部署"],
        "FastAPI": ["fast-api"],
        "Flask": ["flask"],
        "Django": ["django"],
        "LangChain": ["langchain"],
        "LangGraph": ["langgraph"],
    }
    return alias_map.get(skill_name, [])


# ── 全局单例 ──

_normalizer: Optional[EntityNormalizer] = None
_normalizer_lock = __import__('threading').Lock()


def get_entity_normalizer() -> EntityNormalizer:
    """获取全局 EntityNormalizer 单例"""
    global _normalizer
    if _normalizer is None:
        with _normalizer_lock:
            if _normalizer is None:
                _normalizer = EntityNormalizer()
    return _normalizer


# ── 便捷函数 ──

def normalize_skill(name: str, context: Optional[str] = None) -> NormalizeResult:
    return get_entity_normalizer().normalize(name, "skill", context)


def normalize_position(name: str, context: Optional[str] = None) -> NormalizeResult:
    return get_entity_normalizer().normalize(name, "position", context)


def normalize_major(name: str, context: Optional[str] = None) -> NormalizeResult:
    return get_entity_normalizer().normalize(name, "major", context)


def normalize_certificate(name: str, context: Optional[str] = None) -> NormalizeResult:
    return get_entity_normalizer().normalize(name, "certificate", context)


def normalize_degree(name: str, context: Optional[str] = None) -> NormalizeResult:
    return get_entity_normalizer().normalize(name, "degree", context)
