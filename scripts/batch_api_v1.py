#!/usr/bin/env python3
"""Submit up to 100 domains to Kopernik API v1 and download their results."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests


def _domains(path: str) -> list[str]:
    values = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        value = line.strip().split(",", 1)[0].strip()
        if value and not value.startswith("#") and value.lower() not in ("domain", "domena", "url"):
            values.append(value)
    return list(dict.fromkeys(values))


def _request(method: str, url: str, api_key: str, **kwargs) -> requests.Response:
    headers = dict(kwargs.pop("headers", {}))
    headers["Authorization"] = f"Bearer {api_key}"
    for attempt in range(6):
        response = requests.request(method, url, headers=headers, timeout=90, **kwargs)
        if response.status_code != 429 and response.status_code < 500:
            response.raise_for_status()
            return response
        time.sleep(min(30, 2 ** attempt))
    response.raise_for_status()
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="TXT/CSV with one domain in the first column")
    parser.add_argument("--api-key", default=os.getenv("KOPERNIK_API_KEY", ""))
    parser.add_argument("--base-url", default="https://strategiczni.ai/llms-audit/v1")
    parser.add_argument("--out", default="kopernik_api_results")
    parser.add_argument("--poll-interval", type=int, default=20)
    parser.add_argument("--idempotency-key", default="")
    args = parser.parse_args()
    if not args.api_key:
        sys.exit("Set KOPERNIK_API_KEY or pass --api-key.")
    domains = _domains(args.input)
    if not 1 <= len(domains) <= 100:
        sys.exit(f"Expected 1–100 unique domains, received {len(domains)}.")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    idem = args.idempotency_key or f"batch-{Path(args.input).stem}-{int(time.time())}"
    response = _request(
        "POST",
        f"{args.base_url.rstrip('/')}/batches",
        args.api_key,
        headers={"Idempotency-Key": idem},
        json={"domains": domains},
    )
    batch = response.json()
    batch_id = batch["batch_id"]
    print(f"Batch {batch_id}: {batch['total']} audits accepted")
    while batch["status"] not in ("completed", "completed_with_errors"):
        time.sleep(max(2, args.poll_interval))
        batch = _request(
            "GET", f"{args.base_url.rstrip('/')}/batches/{batch_id}", args.api_key
        ).json()
        print(
            f"queued={batch['queued']} running={batch['running']} "
            f"completed={batch['completed']} failed={batch['failed']}"
        )
    (out_dir / "batch.json").write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in batch["audits"]:
        audit_id = item["audit_id"]
        record = dict(item)
        if item["status"] == "completed":
            for section in ("summary", "findings", "pages"):
                record[section] = _request(
                    "GET", f"{args.base_url.rstrip('/')}/audits/{audit_id}/{section}", args.api_key
                ).json()
        (out_dir / f"{audit_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"Results saved in {out_dir}")


if __name__ == "__main__":
    main()
