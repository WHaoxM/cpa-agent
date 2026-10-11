"""Publish the reviewed repository skill seed to an isolated Neo4j label.

Run from repo root: mkg/python backend/scripts/seed_public_skills.py
No legacy node or property is copied. Re-running is idempotent.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.db import get_neo4j_client
from app.services.public_skill_graph import SOURCE_ID


def publish(client=None):
    seed = json.loads(Path(__file__).with_name("public_skill_seed.json").read_text(encoding="utf-8"))
    client = client or get_neo4j_client()
    # Atomic publication; runtime GraphStorage does not write this label.
    def write(tx):
        tx.run(
            "UNWIND $skills AS row MERGE (s:PublicSkill {source_id: $source_id, name: row.name}) "
            "SET s.category = row.category, s.visibility = 'public'",
            skills=seed["skills"], source_id=SOURCE_ID,
        ).consume()
        tx.run(
            "UNWIND $relations AS row "
            "MATCH (s:PublicSkill {source_id: $source_id, name: row.source}), "
            "(t:PublicSkill {source_id: $source_id, name: row.target}) "
            "MERGE (s)-[r:PREREQUISITE {source_id: $source_id}]->(t) "
            "SET r.confidence = row.confidence",
            relations=seed["relations"], source_id=SOURCE_ID,
        ).consume()
    with client.session() as session:
        session.execute_write(write)
    return {"source_id": SOURCE_ID, "skills": len(seed["skills"]), "relations": len(seed["relations"])}


if __name__ == "__main__":
    print(json.dumps(publish(), ensure_ascii=False))
