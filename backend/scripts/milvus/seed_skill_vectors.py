"""Rebuild public skill vectors from MySQL taxonomy structural fields.

Run from backend/: python -B -m scripts.milvus.seed_skill_vectors
Add --apply --expect-count N to write. No LLM, SQL update, or synthetic vector.
"""

import argparse
import json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write and verify the index")
    parser.add_argument("--dry-run", action="store_true", help="legacy alias for the read-only default")
    parser.add_argument("--expect-count", type=int, help="required with --apply; guards source drift")
    parser.add_argument("--milvus-uri", help="use an isolated Milvus Lite path or server URI")
    parser.add_argument("--allow-unmanaged", action="store_true",
                        help="preserve pre-existing untagged vectors instead of failing")
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")
    if args.apply and (args.expect_count is None or args.expect_count <= 0):
        parser.error("--apply requires a positive --expect-count")

    from app.services.db import get_mysql_client
    from app.services.skill_vector_index import load_taxonomy, reconcile_skill_vectors
    from app.config import Config

    mysql = get_mysql_client()
    model = Config.EMBEDDING_MODEL
    desired = load_taxonomy(mysql, model)
    if args.expect_count is not None and len(desired) != args.expect_count:
        raise SystemExit(f"taxonomy count changed: expected {args.expect_count}, got {len(desired)}")
    if not args.apply:
        print(json.dumps({"source_rows": len(desired), "model": model,
                          "sample": desired[0], "applied": False}, ensure_ascii=False))
        return

    from app.services.embedding_service import get_embedding_service
    from app.services.db.milvus_client import MilvusClient

    milvus = MilvusClient(uri=args.milvus_uri) if args.milvus_uri else MilvusClient()
    try:
        result = reconcile_skill_vectors(mysql, milvus, get_embedding_service(),
                                         allow_unmanaged=args.allow_unmanaged)
        print(json.dumps({**result, "model": model, "milvus_uri": milvus.uri,
                          "applied": True}, ensure_ascii=False))
    finally:
        if milvus._client is not None:
            milvus._client.close()
        milvus.disconnect()


if __name__ == "__main__":
    main()
