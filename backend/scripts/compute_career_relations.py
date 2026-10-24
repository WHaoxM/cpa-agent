"""Preview or append an immutable shared-skill career relation projection.

Preview is the default and performs no writes. After migration 0022 is applied
to an explicitly approved isolated schema, use --apply --allow-write-to SCHEMA.
Every run is keyed by the algorithm version and active source fingerprint;
previous runs and edges are retained.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.career_relations import compute_and_persist
from app.services.db import get_mysql_client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="append this immutable run")
    parser.add_argument("--allow-write-to", help="exact configured database name required with --apply")
    args = parser.parse_args()
    if args.apply and not args.allow_write_to:
        parser.error("--apply requires --allow-write-to with the exact isolated target schema name")
    if not args.apply and args.allow_write_to:
        parser.error("--allow-write-to is valid only with --apply")

    mysql = get_mysql_client()
    if args.apply and mysql.database != args.allow_write_to:
        raise SystemExit("Refusing to write: configured target schema does not match --allow-write-to")
    result = compute_and_persist(mysql, apply=args.apply)
    result["target_schema"] = mysql.database
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
