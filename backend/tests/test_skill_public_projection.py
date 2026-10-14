"""Skill ANN hits remain hints until current taxonomy binds their exact text."""

import json
import pytest

from app.services.skill_vector_index import _canonical, project_public_skill_vector_hits


class Taxonomy:
    def __init__(self, row):
        self.row = row

    def execute(self, _sql):
        return [self.row]


def test_exact_taxonomy_hit_survives_but_changed_text_hash_or_category_does_not():
    row = {"id": 7, "skill_name": "Python", "category": "语言",
           "sub_category": "后端", "tier": 2, "dimension": "D2"}
    canonical = _canonical(row, "test/model")
    hit = {"id": 10, "text": canonical["text"],
           "metadata": json.dumps(canonical["metadata"], ensure_ascii=False),
           "distance": 0.9}
    assert project_public_skill_vector_hits(Taxonomy(row), [hit], "test/model")[1] == 0
    for altered in (
        {**hit, "text": "arbitrary browser text"},
        {**hit, "metadata": json.dumps({**canonical["metadata"],
                                         "content_sha256": "0" * 64})},
    ):
        assert project_public_skill_vector_hits(Taxonomy(row), [altered], "test/model") == ([], 1)
    changed = {**row, "category": "已更新分类"}
    assert project_public_skill_vector_hits(Taxonomy(changed), [hit], "test/model") == ([], 1)


def test_populated_taxonomy_and_empty_index_is_unavailable_not_no_match():
    row = {"id": 7, "skill_name": "Python", "category": "语言",
           "sub_category": "后端", "tier": 2, "dimension": "D2"}
    with pytest.raises(RuntimeError, match="empty while skill_taxonomy is populated"):
        project_public_skill_vector_hits(Taxonomy(row), [], "test/model")
