# Source gaps and anomalies

The v1 build is intentionally conservative: if a source is not available as a local PDF and extracted into `data/evidence.jsonl`, related factors stay low-confidence or keep explicit missing evidence.

## Still missing after v1 build

- `US20120016870A1`: referenced by the local LinkedIn article, but no local PDF is present and it was not confirmed/downloaded during this build.
- `US20250356223A1`: Google Patents resolves this number as "Machine-Learning Systems and Methods for Conversational Recommendations". It was not downloaded as a local PDF in v1, so `source-authority-generative-context` remains low-confidence.
- `US9317592B1`: not confirmed as the topical-coherence/information-gain source during search. Search results for "topical coherence" point to other publications such as `US7577652B1` / patent family records, so the article reference may need correction before this factor is upgraded.

## Downloaded during v1 build

- `US8788477B1`: downloaded from Google Patents PDF link and extracted locally.
- `US9195944B1`: downloaded from Google Patents PDF link and extracted locally.

## Policy

Do not upgrade a factor to `confidence=high` from a source-gap note. Upgrade only after the source exists locally and has at least one usable `abstract`, `summary`, `claim`, `description`, `ocr`, or `figure` evidence record.
