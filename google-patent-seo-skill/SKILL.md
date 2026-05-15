---
name: google-patent-seo-skill
description: Evidence-first SEO content strategy and audit skill based on Google patents, patent applications, OCR, and figure evidence. Use when Codex needs to create SEO briefs, audit existing content, design topical authority clusters, evaluate schema/content alignment, improve AI Overview citability, or check patent-derived SEO signals such as entity coverage, factual corroboration, source authority, behavioral hypotheses, and generative-AI content quality.
---

# Google Patent SEO Skill

## Core Rule

Treat patents as evidence for mechanisms, not proof of current Google ranking behavior. Always separate:

- `evidence`: what the patent/figure/OCR says.
- `seo_inference`: the practical SEO interpretation.
- `confidence`: how strongly the local evidence supports that interpretation.

Never present `confidence=low` or `seo_inference_level=speculative` factors as confirmed ranking factors.

## Quick Start

Use `references/factors.jsonl` as the primary source. Each line is one measurable SEO factor with Polish definitions, audit checks, examples, source patents, evidence links, confidence, and tags.

Use `references/factors.md` when a human-readable overview is enough.

Use `references/evidence.jsonl` only when you need source support, claim-level detail, OCR text, or figure context.

Use `references/source-gaps.md` before upgrading low-confidence factors or citing missing patents.

For fast lookup, run:

```bash
python scripts/search_factors.py entity
python scripts/search_factors.py --category generative_ai
python scripts/search_factors.py --confidence high
```

## Content Brief Workflow

1. Identify the target query, audience, page type, primary entity, and intent.
2. Select relevant factors from `factors.jsonl`; prioritize `confidence=high` and `confidence=medium`.
3. Build the brief around measurable outputs:
   - entity coverage and entity disambiguation,
   - information gain and missing attributes,
   - source/citation requirements,
   - citable fragment density,
   - schema/content-data alignment,
   - human-likeness and original experience.
4. Include the chosen `factor_id` next to every recommendation.
5. If a factor has missing evidence or low confidence, label it as an experiment or hypothesis.

## Content Audit Workflow

1. Extract title, H1, headings, claims, statistics, author/source signals, schema, citations, and main entities.
2. Score the content against relevant factors from `factors.jsonl`.
3. For each issue, return:
   - `factor_id`,
   - observed problem,
   - evidence or missing evidence,
   - recommended fix,
   - example rewrite,
   - priority: high, medium, low, or experiment.
4. Do not over-penalize content using speculative behavioral factors unless the user provides GSC/analytics data.

## Figure Evidence

`references/evidence.jsonl` contains `evidence_type=figure` records from rendered patent pages. These records include OCR and a local visual summary when available.

Use figure evidence to understand workflows, data flow, and system components. Do not use figure evidence alone for high-confidence SEO claims unless it is paired with abstract, summary, claim, or description evidence.

If a figure record has `vision_status=local_ocr_only_needs_vision_review`, treat its OCR as incomplete for arrows, layout, and rotated labels.

## Output Standards

When creating or auditing content, return concrete operational guidance rather than patent summaries.

Good output:

- cites `factor_id`,
- says what to change,
- gives a before/after or implementation example,
- states confidence and evidence limits.

Bad output:

- claims a patent proves an active ranking factor,
- cites a missing patent as strong evidence,
- gives generic SEO advice without measurable checks,
- ignores schema/content and source/citation alignment.
