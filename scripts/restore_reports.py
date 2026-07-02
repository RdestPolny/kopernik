#!/usr/bin/env python3
"""Przywraca zbackupowane raporty do appki (POST /report/import — pamięć + Firestore).

Użycie (po deployu nowej wersji):
  python scripts/restore_reports.py --token TWOJ_LEADS_TOKEN
  python scripts/restore_reports.py --token TOKEN report_backups/klient.pl.json  # pojedynczy plik
"""
import argparse
import glob
import json
import os
import sys

import requests

BASE = os.getenv("KOPERNIK_BASE_URL", "https://strategiczni.ai/llms-audit").rstrip("/")
BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "report_backups")


def restore_file(path: str, token: str) -> bool:
    with open(path, encoding="utf-8") as fh:
        result = json.load(fh)
    r = requests.post(f"{BASE}/report/import", params={"token": token}, json=result, timeout=120)
    if r.status_code != 200:
        print(f"  FAIL: {path} — HTTP {r.status_code}: {r.text[:200]}")
        return False
    data = r.json()
    fs = "Firestore OK" if data.get("firestore") else "UWAGA: tylko pamięć instancji (Firestore zawiódł)"
    print(f"  OK: {data.get('domain')} — {fs}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="pliki JSON (domyślnie: report_backups/*.json)")
    ap.add_argument("--token", default=os.getenv("LEADS_TOKEN", ""), required=False)
    args = ap.parse_args()
    if not args.token:
        sys.exit("Wymagany --token (LEADS_TOKEN)")
    files = args.files or sorted(glob.glob(os.path.join(BACKUP_DIR, "*.json")))
    if not files:
        sys.exit(f"Brak plików do przywrócenia w {BACKUP_DIR}")
    print(f"Przywracam {len(files)} raportów do {BASE}:")
    ok = sum(restore_file(f, args.token) for f in files)
    print(f"Przywrócono {ok}/{len(files)}.")


if __name__ == "__main__":
    main()
