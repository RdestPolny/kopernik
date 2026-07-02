#!/usr/bin/env python3
"""Backup wygenerowanych raportów z żywej appki do plików JSON.

Użycie:
  python scripts/backup_reports.py domena1.pl domena2.pl        # konkretne domeny
  python scripts/backup_reports.py --all --token TWOJ_TOKEN     # wszystkie z /reports (wymaga wdrożonego endpointu)

Zapisuje do report_backups/<domena>.json (pełny wynik audytu).
Przywracanie po deployu: python scripts/restore_reports.py --token TWOJ_TOKEN
"""
import argparse
import json
import os
import sys

import requests

BASE = os.getenv("KOPERNIK_BASE_URL", "https://strategiczni.ai/llms-audit").rstrip("/")
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "report_backups")


def backup_domain(domain: str) -> bool:
    r = requests.get(f"{BASE}/report", params={"domain": domain}, timeout=60)
    r.raise_for_status()
    data = r.json()
    if not data.get("found"):
        print(f"  BRAK: {domain} — raport nie istnieje (pamięć instancji mogła się już wyczyścić)")
        return False
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{data.get('domain', domain)}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data["result"], fh, ensure_ascii=False)
    print(f"  OK: {domain} → {path} ({os.path.getsize(path) // 1024} KB)")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domains", nargs="*", help="domeny do zbackupowania")
    ap.add_argument("--all", action="store_true", help="pobierz listę z GET /reports (wymaga --token)")
    ap.add_argument("--token", default=os.getenv("LEADS_TOKEN", ""))
    args = ap.parse_args()

    domains = list(args.domains)
    if args.all:
        if not args.token:
            sys.exit("--all wymaga --token (LEADS_TOKEN)")
        r = requests.get(f"{BASE}/reports", params={"token": args.token}, timeout=60)
        r.raise_for_status()
        data = r.json()
        domains += data.get("memory", [])
        domains += [d.get("domain", "") for d in data.get("firestore", [])]
    domains = sorted({d.strip().lower() for d in domains if d.strip()})
    if not domains:
        sys.exit("Podaj domeny albo użyj --all --token ...")

    print(f"Backup {len(domains)} raportów z {BASE}:")
    ok = sum(backup_domain(d) for d in domains)
    print(f"Zapisano {ok}/{len(domains)}.")


if __name__ == "__main__":
    main()
