from __future__ import annotations

import re
from typing import Iterable, List

DEFAULT_SKILL_LEXICON = [
    "Python",
    "Java",
    "FastAPI",
    "Flask",
    "Spring Boot",
    "LangChain",
    "LangGraph",
    "RAG",
    "Neo4j",
    "Milvus",
    "MySQL",
    "Docker",
    "Git",
    "Agent",
    "多Agent",
    "知识图谱",
    "向量数据库",
]

# 短技能词（≤2字符）的边界字符：空格、标点、行首/行尾、中文字符边界
_SHORT_BOUNDARY = r"(?:^|[\s,，;；、/\|·\-\(\)（）\[\]])"
_SHORT_BOUNDARY_END = r"(?:$|[\s,，;；、/\|·\-\(\)（）\[\]])"

# 短技能词长度阈值 — 低于此长度强制边界匹配
_SHORT_THRESHOLD = 3


class SkillMatcher:
    """优先使用 Aho-Corasick；缺失依赖时退化为子串匹配。
    
    对于长度 ≤ 2 的技能词（如 C、R、Go），强制要求周围有边界字符，
    防止 "Learning" 中的 "R"、"LangChain" 中的 "C" 等误匹配。
    """

    def __init__(self, lexicon: Iterable[str] | None = None):
        self.lexicon = [x.strip() for x in (lexicon or DEFAULT_SKILL_LEXICON) if x and x.strip()]
        # 按长度分组：短词用正则，长词用 AC / 子串
        self._short_skills: List[str] = []
        self._long_skills: List[str] = []
        for s in self.lexicon:
            if len(s) < _SHORT_THRESHOLD:
                self._short_skills.append(s)
            else:
                self._long_skills.append(s)

        # 为短词预编译正则
        self._short_patterns: List[tuple] = []
        for s in self._short_skills:
            pat = re.compile(
                _SHORT_BOUNDARY + re.escape(s) + _SHORT_BOUNDARY_END,
                re.IGNORECASE,
            )
            self._short_patterns.append((pat, s))

        # 长词用 Aho-Corasick
        self._automaton = None
        self._use_aho = False
        try:
            import ahocorasick  # type: ignore
            automaton = ahocorasick.Automaton()
            for i, token in enumerate(self._long_skills):
                automaton.add_word(token.lower(), (i, token))
            automaton.make_automaton()
            self._automaton = automaton
            self._use_aho = True
        except Exception:
            self._automaton = None
            self._use_aho = False

    def match(self, text: str) -> list[str]:
        hay = (text or "")
        hay_lower = hay.lower()
        found: list[str] = []
        seen: set = set()

        # 长词匹配（AC 或子串）
        if self._use_aho and self._automaton is not None:
            for _, (_, token) in self._automaton.iter(hay_lower):
                if token not in seen:
                    seen.add(token)
                    found.append(token)
        else:
            for token in self._long_skills:
                if token.lower() in hay_lower and token not in seen:
                    seen.add(token)
                    found.append(token)

        # 短词匹配（带边界保护的正则）
        for pat, token in self._short_patterns:
            if token not in seen and pat.search(hay):
                seen.add(token)
                found.append(token)

        return found
