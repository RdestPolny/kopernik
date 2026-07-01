#!/usr/bin/env python3
"""Generator predefiniowanego raportu Kopernika dla strategiczni.pl.

Buduje pełny, spójny `result` używając PRAWDZIWYCH funkcji-builderów z main.py
(scoring, factor_index, dashboard) + syntetycznych, ale realistycznych danych
wejściowych. Sekcje generowane normalnie przez LLM (synteza, overview, klient,
fan-out, snippet, brand) są wypełnione ręcznie dopracowaną treścią PL.

Dostraja wynik ogólny do 91.
"""
import os, sys, json, datetime

os.environ.setdefault("FIRECRAWL_KEY", "x")
os.environ.setdefault("GEMINI_KEY", "x")

KOPERNIK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOPERNIK)
os.chdir(KOPERNIK)  # main.py montuje static/ względem CWD
import main  # noqa

DOMAIN = "strategiczni.pl"
BASE = "https://strategiczni.pl"
NOW = "2026-07-01 09:24"

# ---------------------------------------------------------------------------
# 1. Syntetyczne, dobrze zbudowane strony (near-perfect markup -> wysokie tech)
# ---------------------------------------------------------------------------

def _org_schema():
    return {
        "@context": "https://schema.org", "@type": "Organization",
        "name": "Strategiczni.pl", "url": BASE,
        "sameAs": ["https://www.linkedin.com/company/strategiczni",
                   "https://www.facebook.com/strategiczni"],
        "address": {"@type": "PostalAddress", "addressLocality": "Wrocław",
                    "postalCode": "50-001", "addressCountry": "PL"},
        "telephone": "+48 71 000 00 00", "email": "kontakt@strategiczni.pl",
    }

PAGES = [
    {
        "url": BASE + "/", "page_type": "homepage",
        "title": "Strategiczni.pl — Agencja SEO i AI SEO dla firm B2B",
        "meta_desc": "Zwiększamy widoczność firm w Google i wyszukiwarkach AI (ChatGPT, Perplexity, Gemini). Strategia SEO, AI SEO i content oparte na danych.",
        "reason": "strona główna",
        "extra_schema": [{"@context": "https://schema.org", "@type": "WebSite",
                          "name": "Strategiczni.pl", "url": BASE}],
        "words": 720, "imgs": 8, "internal": 24,
    },
    {
        "url": BASE + "/uslugi/ai-seo", "page_type": "service",
        "title": "AI SEO — optymalizacja pod ChatGPT, Perplexity i Google AI Overviews",
        "meta_desc": "Usługa AI SEO: sprawiamy, że AI cytuje i poleca Twoją firmę. Audyt AEO/GEO, wdrożenie i mierzalne efekty w odpowiedziach AI.",
        "reason": "reprezentatywna strona usługowa (oferta AI SEO)",
        "extra_schema": [
            {"@context": "https://schema.org", "@type": "Service",
             "name": "AI SEO", "provider": {"@type": "Organization", "name": "Strategiczni.pl"},
             "areaServed": "PL", "offers": {"@type": "Offer", "price": "4900",
                                            "priceCurrency": "PLN"}},
            {"@context": "https://schema.org", "@type": "BreadcrumbList",
             "itemListElement": [{"@type": "ListItem", "position": 1, "name": "Usługi"},
                                 {"@type": "ListItem", "position": 2, "name": "AI SEO"}]},
        ],
        "words": 1180, "imgs": 6, "internal": 18,
    },
    {
        "url": BASE + "/blog/jak-rankowac-w-chatgpt", "page_type": "article",
        "title": "Jak rankować w ChatGPT i Perplexity? Przewodnik AEO/GEO 2026",
        "meta_desc": "Kompletny przewodnik, jak sprawić, by ChatGPT i Perplexity cytowały Twoją stronę. AEO, GEO, llms.txt i dane strukturalne krok po kroku.",
        "reason": "reprezentatywny artykuł ekspercki (blog)",
        "extra_schema": [
            {"@context": "https://schema.org", "@type": "Article",
             "headline": "Jak rankować w ChatGPT i Perplexity?",
             "author": {"@type": "Person", "name": "Marcin Zieliński"},
             "datePublished": "2026-02-12", "dateModified": "2026-06-20",
             "publisher": {"@type": "Organization", "name": "Strategiczni.pl"}},
            {"@context": "https://schema.org", "@type": "BreadcrumbList",
             "itemListElement": [{"@type": "ListItem", "position": 1, "name": "Blog"}]},
        ],
        "words": 2340, "imgs": 5, "internal": 21,
    },
    {
        "url": BASE + "/o-nas", "page_type": "about",
        "title": "O nas — zespół ekspertów SEO i AI SEO | Strategiczni.pl",
        "meta_desc": "Poznaj zespół Strategiczni.pl. Konsultanci SEO i AI SEO z certyfikatami, realnym doświadczeniem i mierzalnymi wynikami dla klientów B2B.",
        "reason": "strona 'o firmie' (sygnały E-E-A-T)",
        "extra_schema": [
            {"@context": "https://schema.org", "@type": "Organization",
             "name": "Strategiczni.pl", "url": BASE,
             "sameAs": ["https://www.linkedin.com/company/strategiczni"]},
            {"@context": "https://schema.org", "@type": "Person",
             "name": "Marcin Zieliński", "jobTitle": "Head of AI SEO",
             "worksFor": {"@type": "Organization", "name": "Strategiczni.pl"}},
        ],
        "words": 940, "imgs": 7, "internal": 15,
    },
]


def build_html(p):
    """Zbuduj poprawny HTML strony — pełny <head> + semantyczny <body>."""
    schema_blocks = ""
    schemas = [_org_schema()] if p["page_type"] in ("homepage",) else []
    schemas += p.get("extra_schema", [])
    for s in schemas:
        schema_blocks += f'<script type="application/ld+json">{json.dumps(s, ensure_ascii=False)}</script>\n'

    # obrazy z alt (near-100% pokrycia)
    imgs = "\n".join(
        f'<img src="/img/{i}.webp" alt="Strategiczni.pl — ilustracja {i}" loading="lazy">'
        for i in range(1, p["imgs"] + 1)
    )
    internal_links = "\n".join(
        f'<a href="/strona-{i}">Zobacz więcej {i}</a>' for i in range(1, p["internal"] + 1)
    )
    body_words = ("Strategiczni to agencja SEO i AI SEO. " * (p["words"] // 6))

    contact_extra = ""
    if p["page_type"] in ("homepage", "about"):
        contact_extra = ('<a href="tel:+48710000000">+48 71 000 00 00</a>'
                         '<a href="mailto:kontakt@strategiczni.pl">kontakt@strategiczni.pl</a>')

    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{p['title']}</title>
<meta name="description" content="{p['meta_desc']}">
<link rel="canonical" href="{p['url']}">
<meta property="og:title" content="{p['title']}">
<meta property="og:description" content="{p['meta_desc']}">
<meta property="og:image" content="{BASE}/img/og.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="alternate" hreflang="pl" href="{p['url']}">
{schema_blocks}
</head>
<body>
<header><nav>{internal_links}</nav></header>
<main>
<article>
<h1>{p['title']}</h1>
<section><h2>Wprowadzenie</h2><p>{body_words}</p></section>
<section><h2>Szczegóły</h2><h3>Podsekcja</h3><p>{body_words}</p>
<dl><dt>Pytanie</dt><dd>Odpowiedź ekspercka.</dd></dl>
<table><tr><th scope="col">Metryka</th><th scope="col">Wynik</th></tr>
<tr><td>Widoczność AIO</td><td>31,9%</td></tr></table>
</section>
<section><h2>Podsumowanie</h2><p>{body_words}</p></section>
{imgs}
</article>
</main>
<footer>{contact_extra}<form><input name="email"></form></footer>
</body>
</html>"""


# Syntetyczny PageSpeed (mobile) — bardzo dobre Core Web Vitals
PSI_RAW = {"available": True, "performance_score": 93, "lcp_ms": 2100,
           "fcp_ms": 1500, "tbt_ms": 150, "cls": 0.04, "strategy": "mobile"}

# ---------------------------------------------------------------------------
# 2. Notatki i "obszary do poprawy" (te czynniki dostaną score=1)
#    -> dają realistyczny profil i pozwalają zejść do 91.
# ---------------------------------------------------------------------------
PARTIALS = {
    # url_index -> {factor_id: (score, note)}
    0: {  # homepage
        "external_proof_social_press_awards": (1, "Na stronie głównej są logotypy klientów, ale brakuje linków do zewnętrznych publikacji/nagród potwierdzających te wzmianki."),
        "brand-mentions-authority-proxy": (1, "Marka jest spójnie opisana, jednak liczba zewnętrznych wzmianek autorytatywnych (media branżowe) jest umiarkowana."),
    },
    1: {  # service
        "risk_reversal_guarantee_trial_or_process_clarity": (1, "Proces współpracy jest opisany, ale brakuje jednoznacznej gwarancji/odwrócenia ryzyka (np. audyt próbny, warunki rezygnacji)."),
        "citation-quality-source-verifiability": (1, "Twierdzenia o skuteczności są mocne, lecz część nie jest podparta weryfikowalnym źródłem (case study z liczbami, link)."),
    },
    2: {  # article
        "multi-source-consensus": (1, "Artykuł prezentuje autorską tezę; warto dodać odwołania do 2–3 niezależnych źródeł potwierdzających kluczowe twierdzenia."),
        "external_authoritative_citations_with_links": (1, "Są cytaty ekspertów, ale liczba linków do autorytatywnych źródeł zewnętrznych jest niższa niż w treściach konkurencji."),
        "last_updated_date_visible": (1, "Data aktualizacji jest w danych strukturalnych, ale słabo wyeksponowana wizualnie przy nagłówku artykułu."),
    },
    3: {  # about
        "external_validation_awards_partners_media": (1, "Widoczne są certyfikaty, brakuje jednak zewnętrznych potwierdzeń (nagrody, wzmianki w mediach, logotypy partnerów z linkami)."),
        "clients_or_projects_showcased": (1, "Klienci są wymienieni, ale bez rozwiniętych case studies z mierzalnymi rezultatami."),
    },
}

POSITIVE_NOTES = {
    "clear_value_proposition_above_fold": "Jasna propozycja wartości nad linią zgięcia: „SEO i AI SEO dla firm B2B” z konkretnym efektem.",
    "primary_cta_visible": "Wyraźne, kontrastowe CTA („Umów konsultację”) widoczne bez przewijania.",
    "organization_entity_clearly_stated": "Encja organizacji jednoznacznie zadeklarowana (Organization + WebSite schema, spójna nazwa NAP).",
    "verified-entity-status": "Encja marki potwierdzona spójnym sameAs (LinkedIn, Facebook) i danymi kontaktowymi.",
    "clear_offer_or_service_definition": "Usługa AI SEO zdefiniowana precyzyjnie: zakres, dla kogo, jaki efekt.",
    "faq_section_addressing_objections": "Sekcja FAQ odpowiada na realne obiekcje zakupowe (czas, cena, mierzalność).",
    "author_bio_with_name_and_credentials": "Artykuł ma podpis autora z imieniem, stanowiskiem i kwalifikacjami.",
    "direct_answer_near_content_start": "Bezpośrednia odpowiedź na pytanie tytułowe w pierwszym akapicie — sprzyja cytowaniu przez AI.",
    "founder_or_team_profiles_with_names": "Profile zespołu z imionami, rolami i linkami do LinkedIn.",
    "citable-fragment-density": "Wysoka gęstość zwięzłych, cytowalnych fragmentów (definicje, listy, tabele).",
}


def default_note(fid, label):
    return POSITIVE_NOTES.get(fid) or f"{label}: spełnione — element obecny i poprawnie wdrożony."


# ---------------------------------------------------------------------------
# 3. Budowa page_audits (jak w audit_stream)
# ---------------------------------------------------------------------------

def build_page_audit(idx, p):
    html = build_html(p)
    hc = main.analyze_html_bs4(html, p["url"], html)
    tech_scores = main.build_page_tech_scores(p["page_type"], hc)

    factor_ids = main.PAGE_TYPE_FACTORS[p["page_type"]]["factors"] + \
        main.patent_factor_ids_for_page_type(p["page_type"])
    factors = {}
    partials = PARTIALS.get(idx, {})
    for fid in factor_ids:
        meta = main.FACTOR_META.get(fid, {"label": fid})
        if fid in partials:
            sc, note = partials[fid]
        else:
            sc, note = 2, default_note(fid, meta.get("label", fid))
        factors[fid] = {"score": sc, "note": note}

    perf_scores = main.perf_to_scores(PSI_RAW)

    f_pct = main.factor_score_pct(factors)
    t_pct = main.tech_score_pct(tech_scores)

    pa = {
        "url": p["url"], "page_type": p["page_type"],
        "page_type_label": main.PAGE_TYPE_LABELS.get(p["page_type"], p["page_type"]),
        "reason": p["reason"], "title": p["title"], "meta_desc": p["meta_desc"],
        "factors": factors, "factor_score_pct": f_pct,
        "tech_scores": tech_scores, "tech_score_pct": t_pct,
        "performance_scores": perf_scores, "pagespeed_raw": PSI_RAW,
        "combined_score": main.combined_page_score(f_pct, t_pct),
        "html_checks": hc,
        "html_checks_summary": {
            "word_count": hc.get("content", {}).get("word_count", 0),
            "html_size_kb": hc.get("html_size_kb", 0),
            "schema_types": hc.get("schema", {}).get("types", []),
            "h1_count": hc.get("headings", {}).get("h1_count", 0),
            "internal_links": hc.get("links", {}).get("internal", 0),
            "external_links": hc.get("links", {}).get("external", 0),
            "images": hc.get("images", {}),
            "canonical": hc.get("meta", {}).get("canonical"),
            "rag_signals": hc.get("rag_signals", {}),
        },
    }
    return pa


page_audits = [build_page_audit(i, p) for i, p in enumerate(PAGES)]

# ---------------------------------------------------------------------------
# 4. Domain technical (near-perfect, otwarte dla botów AI, llms.txt obecny)
# ---------------------------------------------------------------------------
robots = {
    "accessible": True,
    "bots": {b: {"allowed": True} for b in main.AI_BOTS},
    "sitemap_in_robots": True, "crawl_delay": None,
}
sitemap = {"exists": True, "url": BASE + "/sitemap.xml", "url_count": 148}
llms = {"exists": True, "url": BASE + "/llms.txt", "size_bytes": 2140}
http_headers = {"hsts": True, "compression": "br", "cache_control": "public, max-age=3600",
                "x_robots_tag": None}
homepage_hc = page_audits[0]["html_checks"]
domain_tech_scores = main.build_domain_tech_scores(robots, sitemap, llms, homepage_hc, http_headers)
domain_tech_pct = main.weighted_domain_tech_score_pct(domain_tech_scores)

# ---------------------------------------------------------------------------
# 5. Fan-out (12 pytań, część partial/missing -> realistyczne pokrycie)
# ---------------------------------------------------------------------------
fan_out = {
    "audited_url": BASE + "/uslugi/ai-seo",
    "queries": [
        {"query": "co to jest AI SEO", "coverage": "covered", "gap_note": ""},
        {"query": "jak sprawić żeby ChatGPT polecał moją firmę", "coverage": "covered", "gap_note": ""},
        {"query": "ile kosztuje usługa AI SEO", "coverage": "covered", "gap_note": ""},
        {"query": "AI SEO a klasyczne SEO różnice", "coverage": "covered", "gap_note": ""},
        {"query": "jak wygląda audyt AEO/GEO", "coverage": "covered", "gap_note": ""},
        {"query": "czy AI SEO działa dla małych firm", "coverage": "partial",
         "gap_note": "Dodać sekcję/segment dla MŚP z przykładem zakresu i budżetu."},
        {"query": "jak mierzyć efekty AI SEO", "coverage": "partial",
         "gap_note": "Dodać opis konkretnych metryk (cytowania w AI, widoczność AIO) i sposobu raportowania."},
        {"query": "najlepsza agencja AI SEO w Polsce", "coverage": "partial",
         "gap_note": "Wzmocnić dowody społeczne i porównanie z konkurencją na tej podstronie."},
        {"query": "AI SEO dla sklepu internetowego", "coverage": "missing",
         "gap_note": "Brak dedykowanej treści dla e-commerce — utworzyć wariant usługi/segment."},
        {"query": "jak długo trwa wdrożenie AI SEO", "coverage": "covered", "gap_note": ""},
        {"query": "czy warto inwestować w AI SEO w 2026", "coverage": "covered", "gap_note": ""},
        {"query": "AI SEO case study wyniki", "coverage": "missing",
         "gap_note": "Brak osadzonego case study z liczbami na stronie usługi — dołączyć skrót z linkiem."},
    ],
}
fan_pct = main.fan_out_score(fan_out)

# ---------------------------------------------------------------------------
# 6. factor_index + dashboard (PRAWDZIWE buildery)
# ---------------------------------------------------------------------------
domain_tech_raw = {"robots": robots, "sitemap": sitemap, "llms": llms,
                   "http_headers": http_headers, "homepage_hc": homepage_hc}


def compute(page_audits):
    # buduje factor_index/dashboard bez mutowania oryginałów (html_checks usuwany)
    import copy
    pas = copy.deepcopy(page_audits)
    fi = main.build_factor_index(pas, domain_tech_scores, domain_tech_raw)
    for pa in pas:
        pa.pop("html_checks", None)
    dash = main.build_dashboard(fi, pas)
    return fi, dash, pas


fi, dash, _ = compute(page_audits)

# --- Tuner: dorzucaj kolejne "obszary do poprawy" aż overall == 91 ---
TUNE_CANDIDATES = [
    (1, "differentiation_vs_competition", "Wyróżniki są wymienione, ale brakuje bezpośredniego porównania z konkurencją, które AI mogłoby zacytować przy pytaniach porównawczych."),
    (2, "source-confidence-score", "Treść jest wiarygodna, jednak część twierdzeń bez linku źródłowego obniża pewność, z jaką model potraktuje je jako fakt."),
    (0, "content-data-alignment-score", "Dane liczbowe na stronie głównej są spójne z treścią, ale można je mocniej ustrukturyzować (tabele/definicje) pod ekstrakcję przez AI."),
    (1, "content_substance_over_fluff", "Treść jest merytoryczna, lecz kilka akapitów marketingowych warto zastąpić konkretami (zakres, proces, liczby)."),
    (3, "company_history_mission_or_founding_story", "Misja jest opisana skrótowo — rozbudowa historii i motywacji założycielskiej wzmocni sygnały wiarygodności."),
]

_ci = 0
while dash["overall"] > 91 and _ci < len(TUNE_CANDIDATES):
    idx, fid, note = TUNE_CANDIDATES[_ci]
    _ci += 1
    pa = page_audits[idx]
    if fid in pa["factors"] and pa["factors"][fid]["score"] == 2:
        pa["factors"][fid] = {"score": 1, "note": note}
        # przelicz zależne pola strony
        pa["factor_score_pct"] = main.factor_score_pct(pa["factors"])
        pa["combined_score"] = main.combined_page_score(pa["factor_score_pct"], pa["tech_score_pct"])
    fi, dash, _ = compute(page_audits)

print("overall po dostrojeniu:", dash["overall"])
for g in dash["groups"]:
    print("  ", g["id"], g["score"])

assert dash["overall"] == 91, f"Nie udało się trafić 91 (jest {dash['overall']})"

# ---------------------------------------------------------------------------
# 7. Agregaty scores_obj (jak w audit_stream)
# ---------------------------------------------------------------------------
import copy
_pas = copy.deepcopy(page_audits)
factor_index = main.build_factor_index(_pas, domain_tech_scores, domain_tech_raw)
for pa in _pas:
    pa.pop("html_checks", None)
dashboard = main.build_dashboard(factor_index, _pas)
category_scores = {g["id"]: (g["score"] if g["score"] is not None else 0) for g in dashboard["groups"]}
overall = dashboard["overall"]

page_scores = [pa["combined_score"] for pa in _pas]
avg_page = round(sum(page_scores) / len(page_scores))

legacy_grp = {k: {"val": 0, "max": 0} for k in ("eeat", "topical", "geo", "patent")}
for pa in _pas:
    for fk, v in (pa.get("factors") or {}).items():
        cat = main.FACTOR_META.get(fk, {}).get("category", "")
        if cat in legacy_grp:
            legacy_grp[cat]["max"] += 2
            legacy_grp[cat]["val"] += (v.get("score", 0) if isinstance(v, dict) else 0)
def _grp_pct(g): return round(g["val"] / g["max"] * 100) if g["max"] else 0
legacy_base_overall = round(avg_page * 0.55 + domain_tech_pct * 0.30 + fan_pct * 0.15)
penalties = sum(pen for f, pen in main.CRITICAL_FACTOR_PENALTIES.items() if domain_tech_scores.get(f, 2) == 0)
legacy_overall = max(0, legacy_base_overall - penalties)

scores_obj = {
    "overall": overall,
    "category": category_scores,
    "penalties": penalties,
    "page_average": avg_page,
    "domain_technical": domain_tech_pct,
    "fan_out": fan_pct,
    "legacy": {
        "overall": legacy_overall, "base_overall": legacy_base_overall,
        "category": {
            "eeat": _grp_pct(legacy_grp["eeat"]), "topical": _grp_pct(legacy_grp["topical"]),
            "geo": _grp_pct(legacy_grp["geo"]), "patent": _grp_pct(legacy_grp["patent"]),
            "accessibility": domain_tech_pct,
        },
    },
}

# ---------------------------------------------------------------------------
# 8. Sekcje narracyjne (ręcznie, spójne z profilem 91/100)
# ---------------------------------------------------------------------------
synth = {
    "top_recommendations": [
        {"priority": 1, "text": "Dodaj na stronie usługi AI SEO osadzone case study z konkretnymi liczbami (wzrost cytowań w AI, widoczność w AI Overviews) — to najmocniejszy dowód, który modele mogą cytować.", "page_url": BASE + "/uslugi/ai-seo", "page_type": "service"},
        {"priority": 2, "text": "Uzupełnij kluczowe twierdzenia w artykule o linki do 2–3 niezależnych, autorytatywnych źródeł, aby wzmocnić wiarygodność i szansę na cytowanie przez Perplexity/ChatGPT.", "page_url": BASE + "/blog/jak-rankowac-w-chatgpt", "page_type": "article"},
        {"priority": 3, "text": "Wyeksponuj wizualnie datę aktualizacji przy nagłówku artykułu (obok autora), nie tylko w danych strukturalnych.", "page_url": BASE + "/blog/jak-rankowac-w-chatgpt", "page_type": "article"},
        {"priority": 4, "text": "Dodaj sekcję z jednoznaczną gwarancją / odwróceniem ryzyka na stronie usługi (audyt próbny, warunki współpracy) — usuwa obiekcję zakupową i jest chętnie streszczana przez AI.", "page_url": BASE + "/uslugi/ai-seo", "page_type": "service"},
        {"priority": 5, "text": "Na stronie 'O nas' rozbuduj case studies klientów o mierzalne rezultaty oraz dodaj zewnętrzne potwierdzenia (nagrody, wzmianki w mediach) z linkami.", "page_url": BASE + "/o-nas", "page_type": "about"},
        {"priority": 6, "text": "Utwórz dedykowany wariant treści AI SEO dla e-commerce/MŚP — pokrywa realne pytania fan-out, które dziś nie mają odpowiedzi na stronie.", "page_url": BASE + "/uslugi/ai-seo", "page_type": "service"},
    ],
    "content_gaps": [
        "AI SEO dla sklepów internetowych — jak zwiększyć widoczność karty produktu w ChatGPT",
        "Case study: jak zwiększyliśmy cytowania klienta w Perplexity o 240%",
        "llms.txt — kompletny przewodnik wdrożenia dla polskich firm",
        "AI SEO dla firm usługowych i lokalnych — poradnik krok po kroku",
        "Jak mierzyć ROI z AI SEO — metryki, narzędzia, raportowanie",
    ],
    "overall_assessment": "Strategiczni.pl to domena o bardzo dobrych fundamentach technicznych i wysokiej gotowości pod wyszukiwarki AI — otwarty dostęp dla botów, llms.txt, poprawne dane strukturalne i szybkie Core Web Vitals. Największą siłą jest spójna, jednoznaczna encja marki oraz czytelna, cytowalna treść usługowa i ekspercka. Największy pojedynczy obszar do poprawy to twarde dowody (case studies z liczbami, zewnętrzne cytowania), które podniosłyby sygnały E-E-A-T i częstotliwość cytowania przez modele.",
}

ai_snippet = {
    "available": True, "model": "sonar-pro",
    "snippet": "Strategiczni.pl to polska agencja SEO i AI SEO specjalizująca się w zwiększaniu widoczności firm B2B zarówno w Google, jak i w wyszukiwarkach opartych na AI (ChatGPT, Perplexity, Gemini). Oferuje audyty AEO/GEO, strategię i wdrożenie AI SEO oraz content oparty na danych, z naciskiem na mierzalne efekty — m.in. cytowania marki w odpowiedziach AI i widoczność w Google AI Overviews. Wyróżnia ją łączenie klasycznego SEO z optymalizacją pod modele językowe oraz przejrzysty, oparty na danych proces współpracy.",
}

brand_perception = {
    "gemini": {"available": True, "source": "training_data",
               "text": "Strategiczni.pl to polska agencja marketingu w wyszukiwarkach skupiona na SEO oraz optymalizacji pod wyszukiwarki AI. Kładzie nacisk na strategię opartą na danych i mierzalne efekty dla klientów B2B."},
    "perplexity": {"available": True, "source": "web_search",
                   "citations": [BASE + "/", BASE + "/uslugi/ai-seo", "https://www.linkedin.com/company/strategiczni"],
                   "text": "Strategiczni.pl świadczy usługi SEO i AI SEO (AEO/GEO), pomagając firmom pojawiać się i być cytowanymi w odpowiedziach ChatGPT, Perplexity i Google AI Overviews. Publikuje treści eksperckie na blogu i prezentuje zespół konsultantów."},
    "chatgpt": {"available": True, "source": "training_data", "model": "gpt-4o",
                "text": "To agencja SEO/AI SEO oferująca audyty, strategię i wdrożenia zwiększające widoczność w wyszukiwarkach klasycznych oraz AI. Kieruje ofertę głównie do firm B2B w Polsce."},
}
brand_gaps = {
    "available": True,
    "brand_known_by": ["gemini", "perplexity", "chatgpt"],
    "discrepancies": [
        "Perplexity (z web search) precyzyjnie wskazuje usługi AEO/GEO i zespół; modele treningowe opisują markę bardziej ogólnie.",
        "Zakres cenowy i konkretne case studies nie są przywoływane przez modele — brak ich twardej ekspozycji w treści.",
    ],
    "gaps": [
        "Brak przywoływanych, mierzalnych wyników (case studies z liczbami).",
        "Brak jednoznacznej informacji o zasięgu geograficznym/segmentach (np. e-commerce).",
    ],
    "ai_brand_score": 78,
    "score_rationale": "Wszystkie trzy modele poprawnie i spójnie rozpoznają markę oraz jej specjalizację, ale brakuje twardych dowodów podnoszących wynik do maksimum.",
    "recommendation": "Opublikuj i zlinkuj case studies z liczbami oraz zadbaj o zewnętrzne wzmianki w mediach branżowych — to najszybciej podniesie rozpoznawalność marki w AI.",
}

client_mode = {
    "client_verdict": "Twoja strona jest w bardzo dobrym stanie pod kątem widoczności w AI — ChatGPT, Perplexity i Gemini bez problemu rozumieją, czym się zajmujesz, a strona jest szybka i otwarta dla botów AI. Największa szansa na jeszcze lepsze efekty to pokazanie twardych dowodów: konkretnych wyników klientów i niezależnych potwierdzeń. To sprawi, że AI będzie częściej polecać właśnie Twoją firmę zamiast konkurencji.",
    "client_recommendations": [
        {"priority": 1, "action": "Dodaj na stronie usługi konkretny przykład efektów u klienta (z liczbami).", "why_matters": "AI chętnie cytuje konkretne wyniki — zyskasz więcej poleceń z ChatGPT i Perplexity.", "page_url": BASE + "/uslugi/ai-seo", "page_type": "service"},
        {"priority": 2, "action": "W artykule dołóż odnośniki do 2–3 wiarygodnych źródeł.", "why_matters": "Zwiększa zaufanie AI do Twoich treści, więc częściej je poleci.", "page_url": BASE + "/blog/jak-rankowac-w-chatgpt", "page_type": "article"},
        {"priority": 3, "action": "Pokaż wyraźnie datę aktualizacji artykułu przy tytule.", "why_matters": "AI woli świeże treści — łatwiej trafisz do odpowiedzi na aktualne pytania.", "page_url": BASE + "/blog/jak-rankowac-w-chatgpt", "page_type": "article"},
        {"priority": 4, "action": "Dopisz na stronie usługi jasną gwarancję lub zasady współpracy.", "why_matters": "Rozwiewa wątpliwości klienta i jest chętnie streszczane przez AI.", "page_url": BASE + "/uslugi/ai-seo", "page_type": "service"},
        {"priority": 5, "action": "Na stronie 'O nas' pokaż realizacje klientów i wyróżnienia.", "why_matters": "To dowód, że jesteś ekspertem — AI będzie Cię pewniej polecać.", "page_url": BASE + "/o-nas", "page_type": "about"},
        {"priority": 6, "action": "Przygotuj osobną treść AI SEO dla sklepów internetowych.", "why_matters": "Odpowiesz na pytania, które klienci już zadają AI, a których dziś nie pokrywasz.", "page_url": BASE + "/uslugi/ai-seo", "page_type": "service"},
    ],
    "client_content_gaps": [
        "Poradnik: AI SEO dla sklepu internetowego — warto, bo coraz więcej osób pyta ChatGPT o produkty.",
        "Case study z wynikami klienta — bo konkretne liczby przekonują i są chętnie cytowane przez AI.",
        "Przewodnik po llms.txt — bo to prosty sposób na otwarcie strony dla AI, a mało kto go zna.",
        "AI SEO dla firm lokalnych i usługowych — bo to duży, niezagospodarowany temat.",
        "Jak mierzyć efekty AI SEO — bo klienci chcą wiedzieć, za co płacą.",
    ],
    "client_next_step": "Zacznij od dodania jednego konkretnego przykładu efektów u klienta (z liczbami) na stronie usługi AI SEO.",
    "client_factor_explanations": main.CLIENT_FACTOR_EXPLANATIONS,
}

overview = {
    "headline": "Domena ma bardzo dobre fundamenty techniczne i wysoką gotowość pod AI; największa rezerwa tkwi w twardych dowodach eksperckości.",
    "summary": "Strona jest szybka, otwarta dla botów AI i poprawnie opisana danymi strukturalnymi, a marka jest jednoznacznie rozpoznawalna. Treść usługowa i ekspercka jest czytelna i cytowalna. Największe luki to brak osadzonych case studies z liczbami oraz niedobór zewnętrznych, weryfikowalnych cytowań, a także brak treści pokrywającej część pytań klientów (np. e-commerce).",
    "headline_sales": "Twoja firma jest już dobrze widoczna dla ChatGPT i Perplexity — kilka usprawnień sprawi, że AI będzie polecać Cię jeszcze częściej niż konkurencję.",
    "summary_sales": "AI dobrze rozumie, czym się zajmujesz, a Twoja strona jest szybka i otwarta dla botów AI. Tracisz jednak część szans tam, gdzie brakuje twardych dowodów: konkretnych wyników klientów i niezależnych potwierdzeń. Brakuje też treści na kilka pytań, które klienci już zadają AI. Uzupełnienie tych elementów przełoży się na częstsze polecanie Twojej firmy w odpowiedziach AI.",
    "priorities": [
        {"title": "Dowody wyników", "rationale": "Brakuje osadzonych case studies z mierzalnymi liczbami na stronie usługi.", "outcome": "Twierdzenia o skuteczności stają się weryfikowalne i cytowalne.",
         "title_sales": "Brak konkretnych dowodów wyników", "rationale_sales": "Bez liczb AI rzadziej uzna Twoje twierdzenia za wiarygodne i polecane.", "outcome_sales": "AI zacznie cytować Twoje realne wyniki, budując zaufanie klientów."},
        {"title": "Zewnętrzne cytowania", "rationale": "Kluczowe twierdzenia w treści eksperckiej nie mają linków do niezależnych źródeł.", "outcome": "Wyższa wiarygodność treści dla modeli i większa szansa na cytowanie.",
         "title_sales": "Słabe potwierdzenie z zewnątrz", "rationale_sales": "AI bardziej ufa treściom podpartym niezależnymi źródłami.", "outcome_sales": "Twoje artykuły częściej trafią do odpowiedzi AI wraz z linkiem do Ciebie."},
        {"title": "Ekspozycja świeżości", "rationale": "Data aktualizacji jest w danych strukturalnych, ale słabo widoczna wizualnie.", "outcome": "Treść jest jednoznacznie postrzegana jako aktualna.",
         "title_sales": "Niewidoczna aktualność treści", "rationale_sales": "AI preferuje świeże treści, a Twoja aktualność jest ukryta.", "outcome_sales": "Łatwiej trafisz do odpowiedzi na bieżące pytania klientów."},
        {"title": "Odwrócenie ryzyka", "rationale": "Brak jednoznacznej gwarancji/warunków współpracy na stronie usługi.", "outcome": "Usunięcie obiekcji zakupowej i czytelniejszy proces.",
         "title_sales": "Brak jasnej gwarancji", "rationale_sales": "Klient i AI nie widzą, co obniża ryzyko współpracy z Tobą.", "outcome_sales": "Więcej osób decyduje się na kontakt, a AI streszcza Twoje warunki."},
        {"title": "Pokrycie pytań klientów", "rationale": "Część realnych pytań (np. e-commerce, ROI) nie ma odpowiedzi w treści.", "outcome": "Domena pokrywa pełniejszy zestaw intencji wyszukiwania w AI.",
         "title_sales": "Niepokryte pytania w AI", "rationale_sales": "Gdy klient pyta AI o temat, którego nie opisujesz, trafi do konkurencji.", "outcome_sales": "Zgarniesz ruch i polecenia z pytań, których dziś nie obsługujesz."},
    ],
}

senuto_aio = json.load(open(os.path.join(KOPERNIK, "senuto_aio", "strategiczni.pl.json"), encoding="utf-8"))

# ---------------------------------------------------------------------------
# 9. Złożenie result (kontrakt jak w audit_stream)
# ---------------------------------------------------------------------------
result = {
    "url": BASE + "/",
    "discovery_source": "sitemap",
    "timestamp": NOW,
    "homepage_title": PAGES[0]["title"],
    "homepage_meta_desc": PAGES[0]["meta_desc"],
    "scores": scores_obj,
    "dashboard": dashboard,
    "factor_index": factor_index,
    "page_audits": _pas,
    "domain_technical": {
        "scores": domain_tech_scores,
        "score_pct": domain_tech_pct,
        "robots": {k: v for k, v in robots.items() if k != "raw"},
        "sitemap": sitemap,
        "llms_txt": llms,
        "http_headers": http_headers,
        "pagespeed": PSI_RAW,
    },
    "fan_out": fan_out,
    "synthesis": synth,
    "ai_snippet_preview": ai_snippet,
    "brand_perception": brand_perception,
    "brand_gaps": brand_gaps,
    "client_mode": client_mode,
    "overview": overview,
    "senuto_aio": senuto_aio,
    "meta": {
        "factor_meta": main.FACTOR_META,
        "tech_factor_meta": main.TECH_FACTOR_META,
        "domain_tech_meta": main.DOMAIN_TECH_META,
        "category_labels": main.CATEGORY_LABELS,
        "group_labels": main.UI_GROUP_LABELS,
        "group_order": main.UI_GROUP_ORDER,
        "group_weights": main.UI_GROUP_WEIGHTS,
        "score_value_map": main.SCORE_VALUE_MAP,
        "page_type_labels": main.PAGE_TYPE_LABELS,
        "patent_factor_count": len(main.PATENT_FACTORS),
        "patent_scored_factor_count": len(main.scored_patent_factor_ids()),
    },
}

out_dir = os.path.join(KOPERNIK, "fixed_reports")
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, "strategiczni.pl.json")
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(result, fh, ensure_ascii=False, indent=2)

# sanity
blob = json.dumps(result, ensure_ascii=False)
assert "demo" not in blob.lower(), "Znaleziono słowo 'demo' w treści!"
print("overall:", overall, "| kategorie:", category_scores)
print("page_average:", avg_page, "| domain_tech:", domain_tech_pct, "| fan_out:", fan_pct)
print("rozmiar JSON: %.1f KB" % (len(blob) / 1024))
print("zapisano:", out_path)
