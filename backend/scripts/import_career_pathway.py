"""Explicit public pathway import/projection. Preview is the default.

Usage (repo root, mkg): python -B backend/scripts/import_career_pathway.py manifest.json
  --apply      append facts atomically after a separately executed migration
  --project    replay current revision into public Neo4j/Milvus projections
No schema migration, source fetching, private indexing or score write is implicit.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.db import get_mysql_client, get_neo4j_client, get_milvus_client
from app.services.career_pathway import apply_manifest, read_pathway, record_projection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--project', action='store_true')
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    mysql = get_mysql_client()
    if args.project:
        from app.services.career_pathway_projection import ensure_graph_schema, project_graph, project_vectors
        payload = read_pathway(mysql, manifest['role_id'])
        if not payload or payload['revision'] != manifest['revision']:
            raise RuntimeError('Import this exact revision first')
        results = {}
        for target in ('graph','vector'):
            try:
                if target == 'graph':
                    graph = get_neo4j_client()
                    ensure_graph_schema(graph)
                    result = project_graph(graph, payload)
                else:
                    result = project_vectors(get_milvus_client(), payload)
            except Exception as error:
                result = {'status': 'failed', 'reason': type(error).__name__}
            record_projection(mysql, payload, target, result)
            results[target] = result
        receipt = {'revision': payload['revision'], 'projections': results}
    else:
        receipt = apply_manifest(mysql, manifest, apply=args.apply)
    if args.receipt:
        args.receipt.write_text(json.dumps(receipt,ensure_ascii=False,indent=2,default=str), encoding='utf-8')
    print(json.dumps({k:v for k,v in receipt.items() if k != 'payload'}, ensure_ascii=False,default=str))
    if args.project and any(r.get('status') != 'ready' for r in receipt['projections'].values()):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
