#!/usr/bin/env python3
"""Validate the generated SEO patent knowledge base."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "seo-patent-kb" / "data"

REQUIRED_FACTOR_FIELDS = {
    "factor_id",
    "name_pl",
    "category",
    "definition_pl",
    "mechanism_pl",
    "how_to_satisfy_pl",
    "example_pl",
    "audit_checks_pl",
    "measurement_inputs",
    "source_patents",
    "evidence_ids",
    "evidence_summary_pl",
    "seo_inference_level",
    "confidence",
    "anti_patterns_pl",
    "tags",
}

REQUIRED_EVIDENCE_FIELDS = {
    "evidence_id",
    "patent_id",
    "evidence_type",
    "source_file",
    "quote_or_ocr",
    "text_pl",
    "support_role",
    "vision_status",
}

VALID_EVIDENCE_TYPES = {"abstract", "summary", "claim", "description", "figure", "ocr"}
VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_INFERENCE = {"direct", "moderate", "speculative"}


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise AssertionError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return records


def assert_unique(records: list[dict], key: str) -> None:
    seen = set()
    for record in records:
        value = record.get(key)
        if value in seen:
            raise AssertionError(f"duplicate {key}: {value}")
        seen.add(value)


def validate() -> dict:
    factors = load_jsonl(DATA / "factors.jsonl")
    evidence = load_jsonl(DATA / "evidence.jsonl")
    patents = json.loads((DATA / "patents.json").read_text(encoding="utf-8"))

    assert factors, "No factor records found"
    assert evidence, "No evidence records found"
    assert patents, "No patent records found"
    assert_unique(factors, "factor_id")
    assert_unique(evidence, "evidence_id")

    evidence_ids = {item["evidence_id"] for item in evidence}
    patent_ids = {item["patent_id"] for item in patents}

    for factor in factors:
        missing = REQUIRED_FACTOR_FIELDS - factor.keys()
        if missing:
            raise AssertionError(f"{factor.get('factor_id')}: missing fields {sorted(missing)}")
        if not factor["definition_pl"].strip():
            raise AssertionError(f"{factor['factor_id']}: empty definition")
        if factor["confidence"] not in VALID_CONFIDENCE:
            raise AssertionError(f"{factor['factor_id']}: invalid confidence {factor['confidence']}")
        if factor["seo_inference_level"] not in VALID_INFERENCE:
            raise AssertionError(f"{factor['factor_id']}: invalid inference {factor['seo_inference_level']}")
        for evidence_id in factor["evidence_ids"]:
            if evidence_id not in evidence_ids:
                raise AssertionError(f"{factor['factor_id']}: missing evidence reference {evidence_id}")
        for source in factor["source_patents"]:
            if source["patent_id"] not in patent_ids:
                raise AssertionError(f"{factor['factor_id']}: source patent not indexed {source['patent_id']}")
        if factor["confidence"] == "high" and not factor["evidence_ids"]:
            raise AssertionError(f"{factor['factor_id']}: high confidence without local evidence")

    for item in evidence:
        missing = REQUIRED_EVIDENCE_FIELDS - item.keys()
        if missing:
            raise AssertionError(f"{item.get('evidence_id')}: missing fields {sorted(missing)}")
        if item["evidence_type"] not in VALID_EVIDENCE_TYPES:
            raise AssertionError(f"{item['evidence_id']}: invalid evidence_type {item['evidence_type']}")
        if item["patent_id"] not in patent_ids:
            raise AssertionError(f"{item['evidence_id']}: patent not indexed {item['patent_id']}")
        if item["evidence_type"] == "figure" and not item.get("figure_file"):
            raise AssertionError(f"{item['evidence_id']}: figure evidence without figure_file")

    scanned = [item for item in patents if item.get("extraction_method") == "ocr"]
    for patent in scanned:
        if patent.get("ocr_status") != "ok" and not patent.get("text_characters"):
            raise AssertionError(f"{patent['patent_id']}: scanned PDF has no OCR text")

    figure_evidence = [item for item in evidence if item["evidence_type"] == "figure"]
    if not figure_evidence:
        raise AssertionError("No figure evidence generated")

    return {
        "factor_count": len(factors),
        "evidence_count": len(evidence),
        "patent_count": len(patents),
        "figure_evidence_count": len(figure_evidence),
        "missing_source_count": len([p for p in patents if p.get("source_status") == "missing_source"]),
    }


def main() -> int:
    try:
        report = validate()
    except AssertionError as exc:
        print(f"VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
