#!/usr/bin/env python
"""Load ability/course graph package into Neo4j (data file only, no hardcoded domain rows)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.ability_graph_service import DEFAULT_PACKAGE, load_package  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Load ability/course graph JSON into Neo4j")
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_PACKAGE,
        help="JSON package path",
    )
    args = parser.parse_args()
    result = load_package(args.path)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
