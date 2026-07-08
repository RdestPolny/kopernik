#!/usr/bin/env python3
"""Masowy audyt AI SEO przez API Kopernika — równoległy klient batch (np. 70 domen).

Auto-pick podstron (żadnych --picks) korzysta z uszczelnionego, kuloodpornego
auto-discovery w main.py (sitemap → robots.txt → Firecrawl /map → nav linki →
Firecrawl scrape homepage + LLM referee + walidacja HTTP) — patrz main.py, sekcja
"AUTO-PICK" i CLAUDE.md. Ten skrypt jest tylko klientem async job API:
  GET /audit/start?url=...   -> {job_id}
  GET /audit/result?job_id=  -> polling aż status == "done"/"error"

UWAGA (Cloud Run): instancja appki zamraża CPU zaraz po odpowiedzi HTTP, więc audyt
NIE MOŻE być liczony w tle po stronie serwera bez klienta, który go odpytuje — stąd
ten skrypt, a nie webhook/callback.

Użycie:
  python scripts/batch_audit.py --input domeny.txt
  python scripts/batch_audit.py --input domeny.csv --concurrency 6 --out batch_results/ --force

Plik --input: txt (jedna domena/URL na linię, '#' = komentarz) albo csv (pierwsza
kolumna, nagłówek 'domain'/'domena'/'url' opcjonalny i pomijany).

RESUME: domeny, dla których out/<domena>.json już istnieje, są pomijane (chyba że --force).
Na koniec zapisuje out/summary.csv: domain, status, score, duration_s, error.
"""
import argparse
import csv
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests

DEFAULT_BASE_URL = "https://strategiczni.ai/llms-audit"


def _report_key(domain_or_url: str) -> str:
    """Ta sama normalizacja co main.py:_report_key — nazwa pliku spójna z kluczem
    raportu na serwerze (bez www, bez schematu), żeby łatwo powiązać wynik z /report."""
    import re

    s = (domain_or_url or "").strip().lower()
    try:
        if "://" in s:
            s = urlparse(s).netloc or s
    except Exception:
        pass
    s = s.split("/")[0].replace("www.", "")
    return re.sub(r"[^a-z0-9.-]", "_", s)[:200] or "unknown"


def read_domains(path: str) -> list[str]:
    with open(path, encoding="utf-8-sig") as fh:
        content = fh.read()
    domains: list[str] = []
    if path.lower().endswith(".csv"):
        for row in csv.reader(content.splitlines()):
            if not row:
                continue
            val = row[0].strip()
            if not val or val.lower() in ("domain", "domena", "url"):
                continue
            domains.append(val)
    else:
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            domains.append(line)
    seen: set[str] = set()
    out: list[str] = []
    for d in domains:
        key = d.lower()
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


class DomainResult:
    __slots__ = ("domain", "status", "score", "duration_s", "error")

    def __init__(self, domain: str):
        self.domain = domain
        self.status = "pending"
        self.score = None
        self.duration_s = 0.0
        self.error = ""


def _request_with_backoff(method: str, url: str, *, params=None, timeout=60, max_attempts=5):
    """GET/POST z exponential backoff na 429/5xx i błędy sieciowe. Rzuca po wyczerpaniu prób."""
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            r = requests.request(method, url, params=params, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                last_exc = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(min(60, 2 ** attempt))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last_exc = e
            time.sleep(min(60, 2 ** attempt))
    raise last_exc or RuntimeError("Nieznany błąd HTTP")


def run_one_audit(base_url: str, domain: str, poll_interval: int, timeout_s: int) -> dict:
    """Startuje audyt (bez picks -> auto-pick) i polluje do status done/error. Zwraca 'result'."""
    start_r = _request_with_backoff("GET", f"{base_url}/audit/start", params={"url": domain}, timeout=60)
    job = start_r.json()
    job_id = job.get("job_id")
    if not job_id:
        raise RuntimeError(f"Brak job_id w odpowiedzi /audit/start: {job}")

    deadline = time.time() + timeout_s
    while True:
        if time.time() > deadline:
            raise TimeoutError(f"Przekroczono timeout {timeout_s}s (job_id={job_id})")
        r = _request_with_backoff("GET", f"{base_url}/audit/result", params={"job_id": job_id}, timeout=60)
        data = r.json()
        status = data.get("status")
        if status == "done":
            result = data.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("status=done ale brak pola 'result' w odpowiedzi")
            return result
        if status == "error":
            raise RuntimeError(data.get("error") or "Audyt zakończony błędem (bez szczegółów)")
        time.sleep(poll_interval)


def process_domain(base_url: str, domain: str, out_dir: str, poll_interval: int, timeout_s: int, retries: int, force: bool) -> DomainResult:
    res = DomainResult(domain)
    out_path = os.path.join(out_dir, f"{_report_key(domain)}.json")

    if os.path.exists(out_path) and not force:
        res.status = "skipped"
        return res

    start_t = time.time()
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            result = run_one_audit(base_url, domain, poll_interval, timeout_s)
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False)
            res.status = "ok"
            res.score = (result.get("dashboard") or {}).get("overall")
            if res.score is None:
                res.score = (result.get("scores") or {}).get("overall")
            res.duration_s = round(time.time() - start_t, 1)
            return res
        except Exception as e:  # noqa: BLE001 — jedna domena nie może wywrócić całego batcha
            last_err = e
            if attempt < retries:
                time.sleep(min(30, 5 * (attempt + 1)))
                continue
    res.status = "error"
    res.error = f"{type(last_err).__name__}: {last_err}"
    res.duration_s = round(time.time() - start_t, 1)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="plik z domenami: txt (1/linia) albo csv (1. kolumna)")
    ap.add_argument("--base-url", default=os.getenv("KOPERNIK_BASE_URL", DEFAULT_BASE_URL).rstrip("/"))
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", default="batch_results")
    ap.add_argument("--poll-interval", type=int, default=15, help="sekundy między pollingiem /audit/result")
    ap.add_argument("--timeout", type=int, default=1800, help="max sekund na jedną domenę (jedna próba)")
    ap.add_argument("--retries", type=int, default=1, help="dodatkowe próby przy błędzie danej domeny")
    ap.add_argument("--force", action="store_true", help="nadpisz istniejące pliki wyników (domyślnie: resume/skip)")
    args = ap.parse_args()

    domains = read_domains(args.input)
    if not domains:
        sys.exit(f"Brak domen w {args.input}")

    os.makedirs(args.out, exist_ok=True)
    print(f"Batch audit: {len(domains)} domen -> {args.base_url} (concurrency={args.concurrency}, out={args.out}/)")

    results: list[DomainResult] = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        futs = {
            ex.submit(process_domain, args.base_url, d, args.out, args.poll_interval, args.timeout, args.retries, args.force): d
            for d in domains
        }
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                res = fut.result()
            except Exception as e:  # noqa: BLE001 — process_domain już łapie wszystko, to tylko siatka bezpieczeństwa
                res = DomainResult(d)
                res.status = "error"
                res.error = f"nieoczekiwany wyjątek: {e}\n{traceback.format_exc()[-500:]}"
            results.append(res)
            extra = f" — {res.error}" if res.error else ""
            print(f"  [{res.status}] {d} — score={res.score} duration={res.duration_s}s{extra}")

    summary_path = os.path.join(args.out, "summary.csv")
    with open(summary_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["domain", "status", "score", "duration_s", "error"])
        for res in sorted(results, key=lambda r: r.domain.lower()):
            writer.writerow([res.domain, res.status, res.score if res.score is not None else "", res.duration_s, res.error])

    ok = sum(1 for r in results if r.status == "ok")
    skipped = sum(1 for r in results if r.status == "skipped")
    errored = sum(1 for r in results if r.status == "error")
    print(f"\nPodsumowanie: {ok} OK, {skipped} pominiętych (resume), {errored} błędów, razem {len(results)}.")
    print(f"Wyniki: {args.out}/, podsumowanie: {summary_path}")
    if errored:
        sys.exit(1)


if __name__ == "__main__":
    main()
