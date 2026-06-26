# Pipeline: Audyt AI SEO → dane Senuto → oferta strategiczna

Dokument opisuje powtarzalny przepływ łączący aplikację audytową (Kopernik) ze
skillem `seoai-offer-local`, wzbogacony o twarde dane rynkowe z Senuto (przez
konektor MCP, po stronie agenta — nie w kodzie aplikacji).

```
URL klienta
   │
   ├─(1)─►  Audyt w aplikacji  ──►  eksport „Pakiet do oferty (.json)”
   │                                 = audit_data (Phase 1B skilla)
   │
   ├─(2)─►  Dane Senuto (MCP, agent)  ──►  trend widoczności + luki + konkurencja
   │                                        = dane do Phase 2 skilla
   │
   └─(3)─►  skill seoai-offer-local  ──►  oferta HTML/PDF (A4)
```

---

## (1) Pakiet do oferty — eksport z aplikacji

W trybie **SEO** w pasku „Eksport raportu" dostępny jest przycisk
**↓ Pakiet do oferty (.json)**. Zapisuje plik `oferta-pakiet-<domena>.json`
o strukturze zgodnej z `audit_data` z Fazy 1B skilla — bez konieczności
scrapowania klas CSS z HTML.

Struktura pliku:

```jsonc
{
  "_format": "kopernik-offer-package",
  "_version": "1.0",
  "domain": "klinika-przyklad.pl",
  "url": "https://www.klinika-przyklad.pl/",
  "generated_at": "2026-06-25T21:07:48Z",
  "overall": 58,
  "homepage_title": "…",
  "scores": {                      // 0–100, do orientacji wewnętrznej (NIE pokazywać w ofercie)
    "eeat": 42, "onpage": 55, "patents": 60,
    "ai_aeo": 38, "technical": 80, "performance": 70
  },
  "issues": {                      // luki z obserwacjami — surowiec dla diagnozy
    "eeat_missing":   [{ "label": "...", "observation": "..." }],
    "eeat_partial":   [ ... ],
    "onpage_missing": [ ... ], "onpage_partial": [ ... ],
    "patents_missing":[ ... ], "patents_partial":[ ... ],
    "ai_aeo_missing": [ ... ], "ai_aeo_partial": [ ... ],
    "technical_missing":[ ... ], "technical_partial":[ ... ],
    "performance_missing":[ ... ], "performance_partial":[ ... ]
  },
  "content_gaps": [ "..." ],       // luki treści
  "fanout_missing": [ "..." ]      // zapytania query fan-out bez pokrycia (coverage != covered)
}
```

**Jak używa tego skill oferty (Phase 1B):**
zamiast parsować HTML, wczytaj ten JSON bezpośrednio do `audit_data`. Klucze
`scores`/`issues`/`content_gaps`/`fanout_missing` mapują się 1:1.
Priorytet obszarów w diagnozie: **E-E-A-T → On-page → Patents** (primary),
AI/AEO i Technical tylko jako uzupełnienie strukturalne. **Wyników liczbowych
nie pokazuj w ofercie** — służą wyłącznie do wyboru argumentów.

> Mapowanie w kodzie: `buildOfferPackage()` w `static/index.html`
> (sekcja „Eksport »Pakiet do oferty«"). Grupy audytu mają id:
> `technical, performance, onpage, eeat, patents, ai_aeo`.

---

## (2) Dane Senuto przez MCP (po stronie agenta)

Skill oferty (Phase 2) oczekuje trzech zestawów danych. Poniżej, którym
narzędziem MCP je zdobyć i jak zmapować. Dla Polski używaj `country_id: "200"`
(Base 2.0), z wyjątkiem `get_keywords`, które wymaga `country_id: "1"`.

### A. Trend widoczności → wykres SVG (strona 1 oferty)
- `mcp__senuto__get_domain_statistics(domain, fetch_mode:"topLevelDomain", country_id:"200")`
  → liczby na stronę tytułową: `top3`, `top10`, `top50`, `visibility`,
  `ads_equivalent` (równowartość PPC), `domain_rank`, oraz **`aio_keywords` /
  `aio_visible_keywords`** (pokrycie w Google AI Overviews — mocny argument AI SEO).
- `mcp__senuto__get_positions_history_chart(...)` → szereg czasowy TOP3/TOP10/TOP50
  do wykresu trendu (potwierdź nazwy pól przy wywołaniu).

### B. Tabela luk słów kluczowych → strona 2 (8–12 fraz)
Senuto MCP nie ma jednego „content gap"; najpewniejsze podejście:
1. Wyznacz głównego konkurenta z kroku C.
2. `mcp__senuto__get_keywords(country_id:"1", match_mode:"narrow",
   parameters:[{data_fetch_mode:"domain", value:[<domena_konkurenta>]}])`
   → frazy, na które rankuje konkurent (`keyword`, `searches`, `cpc`, `trends[12]`,
   `snippets` = SERP features).
3. Odfiltruj frazy lokalne/transakcyjne z realnym wolumenem; sprawdź pozycję
   klienta (`get_positions_data` lub `get_keywords` domain-mode na domenie klienta) —
   jeśli brak / >20, to luka.
4. Zbuduj 8–12 wierszy: `Fraza | Wyszukiwania/mies. | Pozycja (lub „brak") | Intencja`.

### C. Luki konkurencyjne → kontekst diagnozy
- `mcp__senuto__get_competitors(domain, fetch_mode:"topLevelDomain", country_id:"200")`
  → `top_competitors` posortowani po `common_keywords`; weź 1 głównego + 3–5
  przykładów fraz, na które konkurent jest, a klient nie.

### Format przekazania do skilla
Skill akceptuje CSV/XLSX. Najprościej: zapisz wynik powyższych zapytań do
`senuto-<domena>.csv` (kolumny: `keyword, searches, position_client, competitor,
intent`) + osobno liczby do strony tytułowej i serię trendu. Alternatywnie
przekaż dane wprost w treści — skill i tak je kuratoruje (Phase 2/4).

---

## (3) Uruchomienie skilla oferty

Wywołaj `seoai-offer-local` i podaj:
- **Pakiet JSON** z kroku (1) jako wynik audytu (Phase 1B).
- **Dane Senuto** z kroku (2) (CSV + liczby + trend).
- Brief klienta: nazwa, miasto, branża, kolory, własne obserwacje (Phase 1).

Skill wygeneruje wielostronicowy dokument HTML (A4, gotowy do `Ctrl+P → PDF`)
i opcjonalnie mockup podstrony usługowej.

---

## Sekcja „Widoczność w AI Overviews" w audycie (dane Senuto)

Audyt online wyświetla kartę **„Widoczność w AI Overviews"** zasilaną danymi Senuto.
Są dwa źródła danych, łączone automatycznie:

1. **Live REST API Senuto** (jeśli skonfigurowane dane logowania) — świeże liczby
   `aio_keywords` / `aio_visible_keywords` pobierane przy każdym audycie.
2. **Per-domenowy plik cache** `senuto_aio/<domena>.json` (opcjonalny) — uzupełnia
   to, czego API w tym endpoincie nie zwraca: rozkład TOP3/10/50, udział w widoczności,
   ewentualną średnią pozycję.

**Mechanizm:** przy audycie backend wywołuje `load_senuto_aio(url)` (`main.py`):
pobiera dane live z API, wczytuje plik cache i **scala** je (świeże liczby z API mają
priorytet, rozkład z cache zostaje), po czym dołącza wynik do `result.senuto_aio`.
Gdy API nie jest skonfigurowane — używany jest sam cache; gdy brak obu źródeł —
sekcja się nie renderuje. Frontend: `renderSenutoAio` w `static/index.html`.

### Konfiguracja API (zmienne środowiskowe)

Klucza/hasła NIE umieszczamy w kodzie — aplikacja czyta je ze środowiska:

```
# Wariant A (zalecany na start): gotowy token (ważny 30 dni)
SENUTO_BEARER_TOKEN=eG....            # token z metody /users/token

# Wariant B: logowanie automatyczne (aplikacja sama odświeża token co ~30 dni)
SENUTO_EMAIL=twoj@email.pl
SENUTO_PASSWORD=••••••

# Opcjonalne:
SENUTO_COUNTRY_ID=200                 # 200 = PL (Base 2.0); domyślne
SENUTO_API_BASE=https://api.senuto.com/api
```

### Jak zdobyć token (POST /users/token)

Bearer token uzyskuje się raz, podając e-mail i hasło konta Senuto z dostępem do API:

```bash
curl --location 'https://api.senuto.com/api/users/token' \
  --header 'Content-Type: application/json' \
  --header 'Lang: pl-PL' \
  --data '{"email":"TWOJ_EMAIL","password":"TWOJE_HASLO"}'
# → {"success":true,"data":{"token":"eG....","country_id":1,...}}
```

Skopiuj `data.token` do `SENUTO_BEARER_TOKEN` (ważny 30 dni). Albo ustaw
`SENUTO_EMAIL` + `SENUTO_PASSWORD`, a aplikacja zaloguje się sama i będzie
odświeżać token. Endpointy danych używają nagłówka `Authorization: Bearer <token>`.

Endpoint live wykorzystywany przez aplikację:
`GET /api/visibility_analysis/reports/dashboard/getDomainStatistics?domain=&fetch_mode=topLevelDomain&country_id=200`
→ `data.statistics.aio_keywords`, `data.statistics.aio_visible_keywords`.

**Format pliku** `senuto_aio/<domena>.json`:

```jsonc
{
  "source": "senuto",
  "country": "PL (Base 2.0)",
  "fetched_at": "2026-06-25",
  "aio_keywords": 2951,            // domain_statistics.full_statistics.aio_keywords
  "aio_visible_keywords": 1736,    // domain_statistics.full_statistics.aio_visible_keywords
  "aio_visibility": 8215,          // characteristics_table(serp_params) → segment ai_overview .visibility
  "aio_visibility_percent": 31.92, // ...→ .visibility_percent
  "aio_distribution": { "top3": 234, "top10": 694, "top50": 2951 },  // ...→ .top3/.top10/.top50
  "avg_position": null             // średnia pozycja: brak w MCP (patrz niżej)
}
```

**Jak zdobyć dane (MCP):**
1. `mcp__senuto__get_domain_statistics(domain, "topLevelDomain", "200")`
   → `aio_keywords`, `aio_visible_keywords`.
2. `mcp__senuto__get_characteristics_table(domain, "topLevelDomain", "200", "serp_params")`
   → znajdź segment `ai_overview`: `top3/top10/top50`, `visibility`, `visibility_percent`.
3. Zapisz do `senuto_aio/<domena>.json`.

**O „średniej pozycji w AIO":** dostępne narzędzia MCP nie zwracają tej wartości
jako pojedynczej liczby (Senuto liczy ją w module API „Widoczność w AI"). Do czasu
włączenia tego modułu pole `avg_position` zostaw `null` — karta pokaże wtedy rozkład
pozycji TOP 3 / 4–10 / 11–50 dla fraz z AIO, który oddaje to samo. Po włączeniu API
wystarczy uzupełnić `avg_position` w pliku cache (lub podpiąć REST — osobny krok).

---

## Skrót dla operatora

> „Zrób ofertę dla <klient>, domena <url>":
> 1) audyt w apce → kliknij **Pakiet do oferty (.json)**;
> 2) ja pobieram Senuto (statystyki + konkurencja + luki) przez MCP;
> 3) odpalam `seoai-offer-local` z pakietem + danymi Senuto + briefem.
