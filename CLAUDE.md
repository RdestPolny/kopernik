# CLAUDE.md — Kopernik (AI SEO Audit)

> Mapa repo dla agentów LLM pracujących w tym kodzie. Cel: zorientować się w architekturze i znaleźć właściwy fragment kodu bez czytania `main.py` (5200+ linii) od deski do deski. Sprawdź najpierw tutaj, dopiero potem otwórz konkretny plik/linię.

## Co to jest

Kopernik to appka do audytu "AI SEO" (widoczność strony w ChatGPT/Perplexity/Google AI Overviews) dla polskiego rynku, produkt Strategiczni. Backend: FastAPI (Python), monolit w jednym pliku `main.py`. Frontend: jeden statyczny plik HTML z vanilla JS (`static/index.html`), bez build stepu i bez frameworka JS. Wejście: URL klienta. Wyjście: JSON z ocenami (technical/performance/onpage/eeat/patents/ai_aeo), listą priorytetowych akcji i podsumowaniem strategicznym wygenerowanym przez LLM; pełny raport odblokowuje się po zostawieniu maila (lead-gate). Wdrożenie: Docker → Cloud Run (`kopernik`, europe-west1) za Firebase Hosting, pod ścieżką `/llms-audit`.

## Mapa katalogów — co czytać, a co pominąć

| Ścieżka | Co to jest | Kiedy czytać |
|---|---|---|
| `main.py` (5214 linii) | Cała logika appki: config, scraping, scoring, wywołania LLM, e-mail, Firestore, routes | Prawie zawsze — ale użyj indeksu linii niżej, nie czytaj liniowo |
| `static/index.html` (2384 linii) | Cały frontend inline (HTML+CSS+JS), Chart.js z CDN | Zmiany UI/UX: hero z wykresem orbitalnym, krok wyboru podstron, dashboard wyników, formularz lead-gate |
| `google-patent-seo-skill/references/factors.jsonl` | 30 czynników SEO wyprowadzonych z patentów Google — wczytywane w runtime przez `main.py` | Dodanie/zmiana czynnika grupy "patents" w scoringu |
| `google-patent-seo-skill/` (reszta) | Skill Claude/Codex opakowujący powyższe (`SKILL.md`, CLI do wyszukiwania czynników) | Praca z czynnikami z poziomu sesji agenta, nie zmiana kodu appki |
| `seo-patent-kb/` (~16 MB) | OFFLINE pipeline generujący `factors.jsonl` z PDF-ów patentowych (OCR, ekstrakcja evidence → factors) | Tylko przy regeneracji czynników od zera z patentów. Nie czytaj `figures/`, `extracted_text/`, `.cache/` bez potrzeby — to duże pliki binarne/OCR |
| `fixed_reports/<domena>.json` | Gotowy, predefiniowany wynik audytu — omija cały pipeline dla danej domeny. Dziś tylko `strategiczni.pl` (wynik 91) | Debug "dlaczego zmiana w kodzie nie wpłynęła na audyt strategiczni.pl" — bo to statyczny plik, nie live audyt |
| `scripts/gen_fixed_report_strategiczni.py` | Generator pliku wyżej; importuje `main.py` jako moduł, żeby użyć prawdziwych funkcji scoringowych + ręcznie dopisane treści LLM-owych sekcji | Aktualizacja predefiniowanego raportu strategiczni.pl |
| `senuto_aio/<domena>.json` | Cache widoczności w AI Overviews (Senuto), uzupełniany ręcznie przez operatora (skill `kopernik` / MCP Senuto), łączony z danymi z żywego API Senuto | Sekcja `senuto_aio` w wyniku audytu |
| `docs/audyt-do-oferty.md` | Opis (PL) pipeline'u: eksport JSON z appki + dane Senuto (MCP) + skill `seoai-offer-local` → oferta dla klienta | Łączenie audytu z generowaniem oferty sprzedażowej |
| `audit.py` (653 linie) | Samodzielny, STARY prototyp CLI (`python audit.py <url>`). NIE jest importowany przez `main.py`, nie działa w produkcji | Tylko jako historyczny punkt odniesienia |
| `add_explanations.py` | Jednorazowy skrypt, który kiedyś wstrzyknął słownik `CLIENT_FACTOR_EXPLANATIONS` do `main.py` jako tekst. Efekt jest już na stałe w `main.py` (linia 794) | Nieużywany w runtime — ignoruj |
| `patent-seo-markdown-fix.patch` | Jednorazowy, historyczny patch | Ignoruj |
| `public/index.html` (11 linii) | Stub przekierowania dla domyślnego katalogu Firebase Hosting → `/llms-audit/` | Tylko przy problemach z routingiem Firebase |
| `firebase.json`, `.firebaserc`, `.firebase/` | Konfiguracja Firebase Hosting: rewrite `/llms-audit/**` → Cloud Run service `kopernik` (europe-west1) | Pytania o deployment/routing |
| `Dockerfile` | `python:3.12-slim`, instaluje `requirements.txt`, uruchamia `uvicorn main:root` | Build/deploy |
| `.env.example` | Lista zmiennych env (patrz tabela niżej) | Konfiguracja/sekrety |
| `.claude/settings.local.json` | Lokalny allowlist uprawnień Claude Code dla tego repo | Nieistotne dla logiki appki |
| `__pycache__/` | Skompilowany bytecode | Nie czytaj |

## Topologia wdrożenia

`main.py` definiuje DWIE aplikacje FastAPI:

- `app` (linia 297) — cała logika, wszystkie route'y, montuje `/static`
- `root` (linia 5208) — właściwy punkt wejścia ASGI (`uvicorn main:root`), montuje `app` pod prefiksem `/llms-audit`, a `GET /` przekierowuje na `/llms-audit/`

Firebase Hosting robi rewrite `/llms-audit/**` → Cloud Run service `kopernik`. Appka "myśli", że żyje pod `/`, ale publicznie stoi pod `/llms-audit/` (stąd `<base href="/llms-audit/">` w `static/index.html`).

## Indeks main.py wg linii (stan na commit `97e9784`)

Numery linii mogą się przesunąć wraz z kolejnymi commitami — jeśli się nie zgadzają, szukaj po nazwie funkcji (`grep -n "def nazwa" main.py`), nie licz offsetów na sztywno.

| Linie | Sekcja |
|---|---|
| 1–104 | Importy, config ze zmiennych env, stałe (`AI_BOTS`, limity, ścieżki) |
| 106–301 | Integracja Senuto: token, statystyki domeny, konkurenci, cache plikowy vs live API (`load_senuto_aio`) |
| 297 | `app = FastAPI(...)` + mount `/static` |
| 301–338 | `AuditRequest` (pydantic) |
| 339–583 | Klasyfikacja typu strony i normalizacja URL (`classify_page_type_heuristic`, `normalize_input_url`) |
| 584–793 | Prompty i id czynników patentowych |
| 794–910 | `CLIENT_FACTOR_EXPLANATIONS` (statyczny słownik PL) + `_build_patent_client_explanations()` |
| 913–1151 | `DOMAIN_TECH_META`, etykiety grup (`UI_GROUP_ORDER/LABELS/WEIGHTS`, linia 968–978), `SCORE_VALUE_MAP` (1754, w dalszej części pliku) |
| 1152–1801 | `_inject_fail_labels()` — ogromna funkcja, głównie statyczne stringi PL (etykiety fail per czynnik). Skanuj, nie czytaj w całości |
| 1802–2052 | Helpery scoringu: `_clamp_score`, `_ui_group_for_factor`, `_impact_effort_for_factor`, `_generic_detail`, `_enrich_factor_metadata` |
| 2053–2309 | Generowanie notatek PL per czynnik: `_tech_specific_note`, `_domain_tech_specific_note` |
| 2310–2569 | Budowa wyniku: `build_factor_index`, `calculate_scope_scores`, `build_top_actions` (priorytet = severity×impact/effort), `build_dashboard` |
| 2570–2683 | Discovery URL-i: `fetch_sitemap_urls`, `_parse_sitemap_xml`, `fetch_homepage_nav_links` |
| 2684–2938 | Wybór i klasyfikacja podstron do audytu: `select_and_classify_urls`, `propose_page_candidates` |
| 2939–2976 | Scraping przez Firecrawl (równoległy, `scrape_pages_parallel`) |
| 2977–3178 | Sprawdzenia techniczne: robots.txt, sitemap, llms.txt, nagłówki HTTP, PageSpeed Insights (Core Web Vitals) |
| 3178–3415 | Analiza HTML przez BeautifulSoup: meta, nagłówki, JSON-LD schema, obrazy/alt, semantic html5, `rag_signals` |
| 3416–3500 | Scoring techniczny strony/domeny (`build_page_tech_scores`, `tech_score_pct`) |
| 3501–3610 | Klienci LLM: `_gemini_call`, `_openai_call`, `_perplexity_brand_call` |
| 3611–3704 | Brand perception (jak marka jest postrzegana przez AI) |
| 3705–3874 | E-mail: SMTP, link odblokowujący raport, treść maila (`_report_link_email_html`) |
| 3875–3976 | Firestore: deduplikacja powracających leadów |
| 3977–4099 | Predefiniowane raporty (`fixed_report_for`) + cache raportów w Firestore (gzip+base64, limit ~1 MiB/dokument) |
| 4100–4267 | Analiza treści strony przez LLM: `analyze_page`, `generate_fan_out` (query fan-out), `generate_ai_snippet_preview` |
| 4268–4360 | `synthesize_findings` — synteza/priorytetyzacja rekomendacji |
| 4361–4417 | `translate_for_client_mode` — tłumaczenie na język biznesowy dla klienta |
| 4418–4486 | `generate_strategic_overview` — streszczenie wykonawcze |
| 4519–4880 | `audit_stream()` — GŁÓWNY orkiestrator: discovery → scrape → analiza → scoring → synteza, yielduje eventy SSE (`data: {...}\n\n`) |
| 4880–5098 | ROUTES (patrz tabela API niżej) |
| 5099–5213 | Model `LeadRequest`, routes `/lead*`, mount `root` + redirect |

## HTTP API

Wszystko poniżej żyje na `app`, publicznie dostępne pod `/llms-audit/...`.

| Metoda + ścieżka | Rola |
|---|---|
| `GET /` | Serwuje `static/index.html` |
| `GET /audit/candidates?url=` | Proponuje podstrony do audytu (sitemap → Firecrawl `/map` → nav linki); skraca do `fixed_report` jeśli istnieje |
| `GET /audit/stream?url=&picks=` | Generator SSE — pełny audyt krok po kroku (używany wewnętrznie przez `/audit/start`) |
| `GET /audit/start?url=&picks=` | Startuje audyt w wątku w tle, zwraca `job_id` natychmiast |
| `GET /audit/result?job_id=&fields=` | Poll: status joba / wynik. `fields` (lista po przecinku) filtruje sekcje wyniku |
| `GET /report?domain=&url=` | Pobiera zapisany/predefiniowany raport dla domeny |
| `GET /reports?token=` | Lista zapisanych raportów (pamięć instancji + Firestore), chronione `LEADS_TOKEN` — np. przed deployem |
| `POST /report/import?token=` | Import/przywrócenie raportu z backupu JSON (pamięć + synchroniczny Firestore), chronione `LEADS_TOKEN` |
| `GET /health` | Health check |
| `POST /lead` | Zapis leada (formularz odblokowania raportu) → e-mail do operatora + e-mail z linkiem dla leada, Firestore |
| `GET /leads?token=` | Lista leadów (chronione `LEADS_TOKEN`) |
| `GET /lead/test?token=&to=` | Test wysyłki e-maila |

To jest ten sam "async job API" opisany w skillu `kopernik` (pobieranie audytu przez `/audit/start` + polling `/audit/result`).

## Kształt wyniku audytu (`result` w `audit_stream` / `/audit/result`)

Top-level klucze: `url, discovery_source, timestamp, homepage_title, homepage_meta_desc, scores, dashboard, factor_index, page_audits, domain_technical{scores,score_pct,robots,sitemap,llms_txt,http_headers,pagespeed}, fan_out, synthesis, ai_snippet_preview, brand_perception, brand_gaps, client_mode, overview, senuto_aio, meta{factor_meta,tech_factor_meta,domain_tech_meta,category_labels,group_labels,group_order,group_weights,score_value_map,page_type_labels,patent_factor_count,patent_scored_factor_count}`.

## Model scoringu

6 grup, wagi sumują się do 100 (`UI_GROUP_WEIGHTS`): `technical` 20, `performance` 10, `onpage` 10, `eeat` 25, `patents` 15, `ai_aeo` 20.

Filozofia "asymetrii podstaw": trywialne/podstawowe czynniki (sitemap, robots, HTTPS, canonical, niezablokowane boty AI itd.) mają zaniżony impact w `LOW_IMPACT_FACTORS` (0.25–1.0 zamiast 2–3) — sama ich obecność prawie nie podnosi wyniku. Za to BRAK krytycznych czynników domenowych odejmuje punkty od wyniku głównego przez `CRITICAL_FACTOR_PENALTIES` (stosowane w `audit_stream` do `dashboard.overall`; `raw_overall` przechowuje wartość sprzed kar). `llms_txt_present` celowo zachowuje impact 3 (rzadki wyróżnik). Skala score czynnika: `SCORE_VALUE_MAP` {0: 0, 1: 0.35, 2: 1.0}.

Priorytet akcji (`build_top_actions`) = `severity × impact / effort` + korekta za liczbę wystąpień.

UWAGA przy zmianach wag: zapisane raporty (Firestore) i `fixed_reports/` mają wyniki policzone starym modelem — nie porównuj 1:1 ze świeżymi audytami po strojeniu.

## Typowy przepływ audytu

1. `GET /audit/candidates` — użytkownik podaje URL, appka proponuje podstrony (albo od razu zwraca `fixed_report`)
2. `GET /audit/start` — start joba w tle → `job_id`
3. Frontend polluje `GET /audit/result?job_id=` do `status: done`
4. Wewnątrz `audit_stream()`: discovery → scrape (Firecrawl) → analiza HTML (BS4) + PageSpeed → scoring techniczny → analiza treści (Gemini) → fan-out/snippet/brand (Gemini/OpenAI/Perplexity) → `synthesize_findings` → `generate_strategic_overview` → zapis (`save_report`: in-memory + Firestore best-effort)
5. Frontend renderuje wynik częściowo (teaser); pełny raport odblokowuje `POST /lead` (e-mail)

## Zmienne środowiskowe

| Zmienna | Rola |
|---|---|
| `FIRECRAWL_KEY` | Scraping stron (wymagane) |
| `GEMINI_KEY`, `GEMINI_MODEL` | Główny LLM audytu (wymagane) |
| `PAGESPEED_KEY` | Core Web Vitals (opcjonalne) |
| `PERPLEXITY_KEY`, `GPT_KEY` | Brand perception — dodatkowe silniki (opcjonalne) |
| `SENUTO_BEARER_TOKEN` / `SENUTO_EMAIL`+`SENUTO_PASSWORD`, `SENUTO_COUNTRY_ID` | Live dane AIO z Senuto (opcjonalne, fallback do `senuto_aio/*.json`) |
| `LEADS_TOKEN` | Chroni `/leads`, `/lead/test` |
| `FIRESTORE_PROJECT` | Persystencja leadów/raportów (opcjonalne — appka działa bez tego, tylko in-memory) |
| `SMTP_USER`, `SMTP_PASS`, `SMTP_HOST`, `SMTP_PORT` | Wysyłka maili do leadów/operatora |
| `LEADS_EMAIL`, `CONTACT_*`, `BRAND_*`, `CLUTCH_PROFILE_URL` | Branding maili i stopki |
| `PUBLIC_BASE_URL` | Bazowy URL appki (do linków w mailach), domyślnie `https://strategiczni.ai/llms-audit` |
| `PORT` | Port uvicorn (Cloud Run) |

## Rzeczy nieoczywiste (gotchas)

- **Fixed reports = pułapka debugowa.** Audyt dla `strategiczni.pl` NIE odpala prawdziwego pipeline'u — zwraca `fixed_reports/strategiczni.pl.json` natychmiast (`fixed_report_for`, wołane w 3 miejscach: `/audit/candidates`, `/audit/start`, `audit_stream`).
- **Dwie appki FastAPI w jednym pliku** (`app` + `root`) — patrz "Topologia wdrożenia".
- **Firestore jest best-effort/soft-fail.** Appka działa bez `FIRESTORE_PROJECT` (leady/raporty tylko in-memory, gubią się przy restarcie). Zapis raportu do Firestore jest gzipowany+base64 (limit ~1 MiB/dokument), **synchroniczny z retry** i **bez sekcji `meta`** (statyczna — odtwarzana przez `_result_meta()` przy odczycie). NIE wracaj do zapisu w wątku daemon: Cloud Run zamraża CPU po odpowiedzi i zapis nigdy się nie wykonuje (dawna przyczyna "znikania" raportów po deployu).
- **Backup/restore raportów przy deployu:** `scripts/backup_reports.py` (per domena lub `--all --token`) → `report_backups/*.json`; po deployu `scripts/restore_reports.py --token` (POST `/report/import`).
- **Fan-out i czynniki cen/opinii są per-podstrona.** `generate_fan_out`, `_page_factor_prompt` (`_domain_context_section`) i `synthesize_findings` dostają slugi z sitemapy: zamiast "brak w domenie" model wskazuje istniejącą podstronę (np. `/cennik`) w `elsewhere_url`/note i rekomenduje podlinkowanie/streszczenie na audytowanej stronie.
- **Senuto: live + cache się łączą, nie nadpisują.** `load_senuto_aio()` bierze plik cache jako bazę i nadpisuje polami z live API tam, gdzie live faktycznie coś zwróciło.
- **`CLIENT_FACTOR_EXPLANATIONS` jest już częścią `main.py`** (linia 794) — `add_explanations.py` to zarchiwizowany generator jednorazowy, nie trzeba go uruchamiać ponownie.
- **`google-patent-seo-skill/references/factors.jsonl` to zależność runtime**, nie tylko dokumentacja skilla — zmiana tego pliku zmienia realny scoring grupy "patents" w appce.
- Remote gita zawiera osadzony token dostępu w URL (`git remote -v`) — warto zrotować token i przejść na SSH/credential helper zamiast trzymać go w URL remote'a.

## Częste zadania → gdzie edytować

- Zmiana treści maila do leada → `_report_link_email_html` (main.py:3768)
- Zmiana wag scoringu → `UI_GROUP_WEIGHTS` (main.py:978)
- Nowy czynnik grupy "patents" → `google-patent-seo-skill/references/factors.jsonl` (albo od zera z PDF-a: `seo-patent-kb/scripts/build_kb.py`)
- Nowa domena z predefiniowanym raportem → wzoruj się na `scripts/gen_fixed_report_strategiczni.py`, wynik do `fixed_reports/<domena>.json`
- Zmiana UI/hero/wykresu/formularza → `static/index.html` (całość frontendu w jednym pliku)
- Nowy route API → `main.py`, sekcja ROUTES (linia ~4880+); pamiętaj, że musi wisieć na `app`, nie na `root`
