"""Explicit additive migration; runtime candidate retrieval never performs DDL."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def install(client=None):
    from app.services.db import get_neo4j_client
    client = client or get_neo4j_client()
    client.execute_query("CREATE FULLTEXT INDEX cpa_public_skill_search_v1 IF NOT EXISTS "
        "FOR (s:PublicSkill) ON EACH [s.name,s.aliases,s.category] "
        "OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words'}}", read_only=False)
    client.execute_query("CREATE INDEX cpa_public_skill_name_v1 IF NOT EXISTS "
                         "FOR (s:PublicSkill) ON (s.source_id,s.name)", read_only=False)
    client.execute_query("CALL db.awaitIndexes(60)", read_only=True)
    return client.execute_query("SHOW INDEXES YIELD name,type,state WHERE name STARTS WITH 'cpa_public_skill_' "
                                "RETURN name,type,state", read_only=True)


if __name__ == "__main__":
    print(install())
