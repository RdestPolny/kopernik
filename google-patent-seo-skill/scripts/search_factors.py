#!/usr/bin/env python3
import argparse, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FACTORS = ROOT / "references" / "factors.jsonl"

def load():
    with FACTORS.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)

def main():
    parser = argparse.ArgumentParser(description="Search Google patent SEO factors.")
    parser.add_argument("query", nargs="?", default="", help="Substring to search in id/name/tags/definition.")
    parser.add_argument("--category", default="", help="Filter by category.")
    parser.add_argument("--confidence", default="", help="Filter by confidence.")
    args = parser.parse_args()
    q = args.query.lower()
    for factor in load():
        haystack = " ".join([
            factor.get("factor_id", ""),
            factor.get("name_pl", ""),
            factor.get("definition_pl", ""),
            " ".join(factor.get("tags", [])),
        ]).lower()
        if q and q not in haystack:
            continue
        if args.category and factor.get("category") != args.category:
            continue
        if args.confidence and factor.get("confidence") != args.confidence:
            continue
        print(json.dumps(factor, ensure_ascii=False))

if __name__ == "__main__":
    main()
