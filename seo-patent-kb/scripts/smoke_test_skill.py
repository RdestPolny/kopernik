#!/usr/bin/env python3
"""Smoke-test the skill references on two realistic usage scenarios."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FACTORS = ROOT / "google-patent-seo-skill" / "references" / "factors.jsonl"


def load_factors() -> dict[str, dict]:
    with FACTORS.open(encoding="utf-8") as fh:
        return {json.loads(line)["factor_id"]: json.loads(line) for line in fh if line.strip()}


def require_fields(factor: dict) -> None:
    required = ["factor_id", "definition_pl", "how_to_satisfy_pl", "example_pl", "audit_checks_pl", "confidence", "evidence_ids"]
    missing = [field for field in required if field not in factor or factor[field] in ("", []) and field != "evidence_ids"]
    if missing:
        raise AssertionError(f"{factor.get('factor_id')}: missing useful fields {missing}")


def scenario(name: str, factor_ids: list[str], factors: dict[str, dict]) -> dict:
    selected = []
    for factor_id in factor_ids:
        if factor_id not in factors:
            raise AssertionError(f"{name}: missing factor {factor_id}")
        factor = factors[factor_id]
        require_fields(factor)
        if factor["confidence"] == "low":
            raise AssertionError(f"{name}: low-confidence factor selected as core recommendation: {factor_id}")
        selected.append(
            {
                "factor_id": factor_id,
                "confidence": factor["confidence"],
                "inference": factor["seo_inference_level"],
                "sample_recommendation_pl": factor["how_to_satisfy_pl"],
            }
        )
    return {"scenario": name, "selected_factors": selected}


def main() -> int:
    factors = load_factors()
    results = [
        scenario(
            "brief: schema Product dla sklepu e-commerce",
            [
                "content-data-alignment-score",
                "entity-coverage-depth",
                "citation-quality-source-verifiability",
                "citable-fragment-density",
                "query-intent-classification-alignment",
            ],
            factors,
        ),
        scenario(
            "audit: ekspercki artykuł SEO bez źródeł i z generycznym tytułem",
            [
                "cross-document-factual-consistency",
                "headline-summary-fit",
                "opinion-subjectivity-detection",
                "human-likeness-score",
                "site-engagement-duration",
            ],
            factors,
        ),
    ]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
