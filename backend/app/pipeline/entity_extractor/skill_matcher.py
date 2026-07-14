from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

from .dictionaries.skill_lexicon import get_all_skills

DEFAULT_SKILL_LEXICON = get_all_skills()

_SHORT_BOUNDARY = r"(?:^|[\s,，;；、/\|·\-\(\)（）\[\]])"
_SHORT_BOUNDARY_END = r"(?:$|[\s,，;；、/\|·\-\(\)（）\[\]])"
_SHORT_THRESHOLD = 3
_SEMANTIC_THRESHOLD = 0.85
_MILVUS_COLLECTION = "skill_vectors"


class SkillMatcher:
    def __init__(self, lexicon: Iterable[str] | None = None, use_semantic: bool = True):
        self.lexicon = [x.strip() for x in (lexicon or DEFAULT_SKILL_LEXICON) if x and x.strip()]
        self.use_semantic = use_semantic
        self._short_skills: List[str] = []
        self._long_skills: List[str] = []
        for s in self.lexicon:
            if len(s) < _SHORT_THRESHOLD:
                self._short_skills.append(s)
            else:
                self._long_skills.append(s)

        self._short_patterns: List[tuple] = []
        for s in self._short_skills:
            pat = re.compile(
                _SHORT_BOUNDARY + re.escape(s) + _SHORT_BOUNDARY_END,
                re.IGNORECASE,
            )
            self._short_patterns.append((pat, s))

        self._automaton = None
        self._use_aho = False
        try:
            import ahocorasick
            automaton = ahocorasick.Automaton()
            for i, token in enumerate(self._long_skills):
                automaton.add_word(token.lower(), (i, token))
            automaton.make_automaton()
            self._automaton = automaton
            self._use_aho = True
        except Exception:
            self._automaton = None
            self._use_aho = False

        self._milvus_client = None
        self._embedding_service = None

    def semantic_match(self, text: str, top_k: int = 5) -> List[Tuple[str, float]]:
        if not self.use_semantic:
            return []
        hay = (text or "").strip()[:2000]
        if not hay:
            return []
        milvus = self._get_milvus()
        if milvus is None:
            return []
        try:
            embedding = self._get_embedding_service()
            if embedding is None:
                return []
            query_vec = embedding.encode_single(hay)
            results = milvus.search(
                collection_name=_MILVUS_COLLECTION,
                query_vector=query_vec,
                top_k=top_k
            )
            matched = []
            for hits in results:
                for hit in hits:
                    score = hit.get("score", 0)
                    if score >= _SEMANTIC_THRESHOLD:
                        skill_name = hit.get("entity", {}).get("skill_name", "")
                        if skill_name:
                            matched.append((skill_name, score))
            return matched
        except Exception:
            return []

    def _get_milvus(self):
        if self._milvus_client is None:
            try:
                from app.services.db import get_milvus_client
                self._milvus_client = get_milvus_client()
            except Exception:
                return None
        return self._milvus_client

    def _get_embedding_service(self):
        if self._embedding_service is None:
            try:
                from app.services.embedding_service import get_embedding_service
                self._embedding_service = get_embedding_service()
            except Exception:
                return None
        return self._embedding_service

    def match(self, text: str) -> list[str]:
        hay = (text or "")
        hay_lower = hay.lower()
        found: list[str] = []
        seen: set = set()

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

        for pat, token in self._short_patterns:
            if token not in seen and pat.search(hay):
                seen.add(token)
                found.append(token)

        semantic = self.semantic_match(text, top_k=3)
        for skill_name, score in semantic:
            if skill_name not in seen:
                seen.add(skill_name)
                found.append(skill_name)
        return found

    def match_with_semantic(self, text: str) -> Tuple[List[str], List[str]]:
        dict_matches = self.match(text)
        semantic_raw = self.semantic_match(text, top_k=5)
        semantic_only = [name for name, _ in semantic_raw if name not in dict_matches]
        return dict_matches, semantic_only
