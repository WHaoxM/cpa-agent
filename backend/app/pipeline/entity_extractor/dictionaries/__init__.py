"""词典模块：技能词典、PII正则、实体规则"""
from .skill_lexicon import SKILL_CATEGORIES, get_all_skills
from .pii_patterns import PII_PATTERNS, ETHICS_SENSITIVE_KEYWORDS
from .entity_patterns import DATE_PATTERNS, DEGREE_KEYWORDS, ROLE_KEYWORDS
