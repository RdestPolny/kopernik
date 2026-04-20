#!/usr/bin/env python3
"""AI SEO Audit — page-type-aware per-URL factor sets."""

import json
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

FIRECRAWL_KEY = os.environ["FIRECRAWL_KEY"]
GEMINI_KEY = os.environ["GEMINI_KEY"]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
FIRECRAWL_SCRAPE = "https://api.firecrawl.dev/v1/scrape"
FIRECRAWL_MAP = "https://api.firecrawl.dev/v1/map"

AI_BOTS = ["GPTBot", "PerplexityBot", "OAI-SearchBot", "ClaudeBot", "anthropic-ai", "Google-Extended"]
MAX_AUDIT_PAGES = 5
SITEMAP_CAP = 300
MAX_CONTENT_CHARS = 7000

app = FastAPI(title="AI SEO Audit")
app.mount("/static", StaticFiles(directory="static"), name="static")


class AuditRequest(BaseModel):
    url: str


# --- PAGE TYPE CLASSIFICATION ---

PAGE_TYPE_PATTERNS = {
    "contact": ["/kontakt", "/contact"],
    "about": ["/o-nas", "/about", "/o-firmie", "/zespol", "/team", "/kim-jestesmy", "/our-team", "/company"],
    "article": ["/blog/", "/artykul", "/article/", "/post/", "/news/", "/aktualnosci", "/poradnik", "/poradniki", "/wiedza/", "/insights/", "/case-study", "/case-studies", "/baza-wiedzy"],
    "service": ["/uslugi", "/oferta", "/services", "/produkt", "/product/", "/cennik", "/pricing", "/service/", "/usluga/"],
    "category": ["/kategoria/", "/category/", "/tag/", "/tags/", "/archive/"],
}


def classify_page_type_heuristic(url: str, base_url: str) -> str | None:
    pu = urlparse(url)
    path = pu.path.lower().rstrip("/")
    base_p = urlparse(base_url).path.rstrip("/")
    if path == base_p or path == "":
        return "homepage"
    for type_name, patterns in PAGE_TYPE_PATTERNS.items():
        if any(p in path for p in patterns):
            return type_name
    return None


PAGE_TYPE_LABELS = {
    "homepage": "Strona główna",
    "service": "Strona sprzedażowa",
    "article": "Artykuł / blog",
    "about": "O firmie",
    "contact": "Kontakt",
    "category": "Kategoria / listing",
    "other": "Inna",
}


# --- FACTOR SETS PER PAGE TYPE ---

PAGE_TYPE_FACTORS = {
    "homepage": {
        "role": "ekspertem od home page UX, brand authority i konwersji dla crawlerów LLM",
        "domain_desc": "strona główna jako centrum nawigacyjno-sprzedażowe + sygnał tożsamości marki (entity) dla LLM",
        "factors": [
            "clear_value_proposition_above_fold",
            "primary_cta_visible",
            "navigation_to_key_sections_clear",
            "trust_signals_logos_reviews_numbers",
            "organization_entity_clearly_stated",
            "contact_info_accessible_from_home",
            "brand_identity_consistent_and_unique",
            "no_generic_marketing_fluff",
            "internal_links_to_services_or_products",
            "external_proof_social_press_awards",
        ],
    },
    "service": {
        "role": "ekspertem od stron sprzedażowych (oferta/produkt/usługa) i konwersji",
        "domain_desc": "strona usługi/produktu — odpowiada na intencje zakupowe, odpowiada na obiekcje, wspiera konwersję",
        "factors": [
            "clear_offer_or_service_definition",
            "benefits_stated_explicitly_not_just_features",
            "pricing_or_price_range_indication",
            "use_cases_or_target_customer_defined",
            "social_proof_testimonials_clients_case_studies",
            "faq_section_addressing_objections",
            "clear_primary_cta_to_contact_or_buy",
            "differentiation_vs_competition",
            "content_substance_over_fluff",
            "risk_reversal_guarantee_trial_or_process_clarity",
        ],
    },
    "article": {
        "role": "ekspertem od E-E-A-T, wiarygodności artykułów i RAG extractability dla ChatGPT/Perplexity",
        "domain_desc": "artykuł blogowy / edukacyjny / poradnik — fundament cytowalności przez LLM, wymaga autora, dat, źródeł, struktury",
        "factors": [
            "author_bio_with_name_and_credentials",
            "publication_date_visible_inline",
            "last_updated_date_visible",
            "external_authoritative_citations_with_links",
            "firsthand_experience_or_original_data",
            "direct_answer_near_content_start",
            "scannable_structure_headings_lists_tables",
            "unique_pov_not_generic_rehash",
            "depth_comprehensive_treatment_of_topic",
            "internal_links_to_related_content",
        ],
    },
    "about": {
        "role": "ekspertem od stron wizerunkowych i budowania autorytetu organizacji dla LLM entity recognition",
        "domain_desc": "strona O nas / zespół — sygnał tożsamości, ekspertyzy i zaufania; kluczowa dla rozpoznania marki przez LLM",
        "factors": [
            "founder_or_team_profiles_with_names",
            "credentials_certifications_or_qualifications",
            "company_history_mission_or_founding_story",
            "external_validation_awards_partners_media",
            "office_location_or_physical_presence",
            "values_or_real_differentiators",
            "links_to_linkedin_or_professional_profiles",
            "real_photos_not_stock_implied",
            "clients_or_projects_showcased",
            "contact_pathway_from_about",
        ],
    },
    "contact": {
        "role": "ekspertem od stron kontaktowych, local SEO i sygnałów NAP dla LLM",
        "domain_desc": "strona kontakt — NAP (Name/Address/Phone), lokalizacja, dostępność kanałów komunikacji",
        "factors": [
            "nap_name_address_phone_complete_and_visible",
            "contact_form_present_and_clear",
            "opening_hours_visible",
            "phone_clickable_tel_link",
            "email_clickable_mailto",
            "map_or_embedded_location",
            "multiple_contact_channels",
            "department_or_role_specific_contacts",
            "response_time_expectation",
            "physical_office_photo_or_proof",
        ],
    },
    "category": {
        "role": "ekspertem od stron kategorii/listing i architektury informacji",
        "domain_desc": "strona kategorii/archiwum — nawigacja, klastrowanie treści, unikalna wartość (nie thin content)",
        "factors": [
            "meaningful_category_intro_copy_not_thin",
            "unique_category_h1_and_title",
            "category_specific_meta_description",
            "internal_links_to_items_with_context",
            "filters_or_facets_if_applicable",
            "pagination_or_load_more_sensible",
            "subcategory_links_exposed",
            "no_boilerplate_content_duplicated",
            "visual_hierarchy_for_scannability",
            "related_categories_linked",
        ],
    },
    "other": {
        "role": "ekspertem od AI SEO",
        "domain_desc": "inna strona (landing, case study, portfolio) — podstawy AI SEO: cel, wartość, struktura, schema",
        "factors": [
            "clear_page_purpose_stated",
            "value_for_user_evident",
            "heading_hierarchy_correct",
            "meta_description_descriptive_and_unique",
            "scannable_structure_lists_or_subheadings",
            "appropriate_schema_for_content_type",
            "internal_links_to_contextual_content",
            "no_generic_ai_generated_content",
            "external_sources_or_proof_where_relevant",
            "clear_next_step_or_cta",
        ],
    },
}


# --- URL DISCOVERY ---

def fetch_sitemap_urls(base_url: str) -> list[str]:
    urls: list[str] = []
    for path in ["/sitemap.xml", "/sitemap_index.xml"]:
        try:
            r = requests.get(urljoin(base_url, path), timeout=15, allow_redirects=True)
            if r.status_code != 200 or len(r.content) < 50:
                continue
            urls.extend(_parse_sitemap_xml(r.text, base_url))
            if urls:
                break
        except Exception:
            continue
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= SITEMAP_CAP:
            break
    return out


def _parse_sitemap_xml(xml_text: str, base_url: str) -> list[str]:
    urls: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return urls
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    for sm in root.findall(".//sm:sitemap/sm:loc", ns):
        if sm.text:
            try:
                r = requests.get(sm.text.strip(), timeout=15)
                if r.status_code == 200:
                    urls.extend(_parse_sitemap_xml(r.text, base_url))
            except Exception:
                continue
            if len(urls) >= SITEMAP_CAP:
                return urls
    for loc in root.findall(".//sm:url/sm:loc", ns):
        if loc.text:
            urls.append(loc.text.strip())
    return urls


def fetch_firecrawl_map(base_url: str) -> list[str]:
    try:
        r = requests.post(
            FIRECRAWL_MAP,
            headers={"Authorization": f"Bearer {FIRECRAWL_KEY}", "Content-Type": "application/json"},
            json={"url": base_url, "limit": SITEMAP_CAP, "includeSubdomains": False},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("links", []) or []
    except Exception:
        return []


def select_and_classify_urls(all_urls: list[str], homepage_url: str, base_url: str) -> list[dict]:
    """Gemini picks MAX_AUDIT_PAGES-1 URLs with types. Returns [{url, page_type, reason}]."""
    domain = urlparse(base_url).netloc
    clean: list[str] = []
    seen = set()
    for u in all_urls:
        pu = urlparse(u)
        if pu.netloc and pu.netloc != domain:
            continue
        if re.search(r"\.(jpg|jpeg|png|gif|webp|svg|ico|css|js|xml|woff2?|ttf|mp4|zip|pdf)(\?|$)", u, re.I):
            continue
        key = (pu.path.rstrip("/"), pu.query)
        if key in seen or pu.path in ("", "/"):
            continue
        seen.add(key)
        clean.append(u)

    if not clean:
        return []

    candidates = clean[:80]

    prompt = f"""Jesteś ekspertem SEO. Wybierz {MAX_AUDIT_PAGES - 1} URL-i z listy i sklasyfikuj typ każdej strony.

<kryteria_wyboru>
- RÓŻNORODNOŚĆ typów: mieszanka service, article, about, contact, category
- UNIKAJ: polityki prywatności, regulaminy, strony paginacji, tag pages, logowania, koszyka
- PRIORYTET: strony reprezentatywne (najważniejsze sprzedażowe + flagowe artykuły + strony zaufania)
</kryteria_wyboru>

<typy_stron>
- service: oferta/usługa/produkt/cennik (sprzedażowa)
- article: artykuł blogowy/poradnik/case study/news (edukacyjna)
- about: o nas/zespół/historia (wizerunkowa)
- contact: kontakt/formularz/NAP
- category: kategoria/listing/archiwum
- other: inne istotne (landing, portfolio, itp.)
</typy_stron>

<homepage_już_wybrana_nie_wliczaj>{homepage_url}</homepage_już_wybrana_nie_wliczaj>

<kandydaci>
{chr(10).join(f"- {u}" for u in candidates)}
</kandydaci>

Zwróć TYLKO JSON (bez markdown, bez komentarzy poza JSON):
{{
  "selected": [
    {{"url": "https://...", "page_type": "service|article|about|contact|category|other", "reason": "krótkie uzasadnienie po polsku"}}
  ]
}}

Dokładnie {MAX_AUDIT_PAGES - 1} pozycji, każda z unikatowego segmentu jeśli to możliwe."""

    try:
        text = _gemini_call(prompt, temperature=0.2, max_tokens=1536)
        parsed = _extract_json(text)
        cand_set = set(candidates)
        picked: list[dict] = []
        for item in parsed.get("selected", []):
            u = item.get("url")
            if u in cand_set:
                pt = item.get("page_type", "other")
                if pt not in PAGE_TYPE_FACTORS:
                    pt = "other"
                picked.append({"url": u, "page_type": pt, "reason": item.get("reason", "")})
        return picked[: MAX_AUDIT_PAGES - 1]
    except Exception:
        return _heuristic_pick_and_classify(candidates, base_url)


def _heuristic_pick_and_classify(urls: list[str], base_url: str) -> list[dict]:
    buckets: dict[str, list[str]] = {k: [] for k in ["service", "article", "about", "contact", "category", "other"]}
    for u in urls:
        pt = classify_page_type_heuristic(u, base_url)
        if pt is None or pt == "homepage":
            buckets["other"].append(u)
        else:
            buckets[pt].append(u)
    out: list[dict] = []
    for bucket_name in ["service", "article", "about", "contact", "category", "other"]:
        if buckets[bucket_name]:
            out.append({"url": buckets[bucket_name][0], "page_type": bucket_name, "reason": "heurystyka URL"})
    return out[: MAX_AUDIT_PAGES - 1]


# --- SCRAPING ---

def scrape_with_firecrawl(url: str) -> dict:
    payload = {
        "url": url,
        "formats": ["markdown", "html", "rawHtml", "links"],
        "onlyMainContent": False,
        "waitFor": 2000,
        "blockAds": True,
        "parsePDF": True,
        "timeout": 30000,
    }
    r = requests.post(
        FIRECRAWL_SCRAPE,
        headers={"Authorization": f"Bearer {FIRECRAWL_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise ValueError(f"Firecrawl error: {data}")
    return data.get("data", {})


def scrape_pages_parallel(urls: list[str]) -> dict[str, dict]:
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(scrape_with_firecrawl, u): u for u in urls}
        for f in as_completed(futures):
            u = futures[f]
            try:
                results[u] = f.result()
            except Exception:
                results[u] = {}
    return results


# --- DOMAIN-LEVEL CHECKS ---

def check_robots_txt(base_url: str) -> dict:
    result = {"accessible": False, "bots": {}, "sitemap_in_robots": False, "crawl_delay": None}
    try:
        r = requests.get(urljoin(base_url, "/robots.txt"), timeout=15)
        if r.status_code == 200:
            result["accessible"] = True
            text = r.text
            if "sitemap:" in text.lower():
                result["sitemap_in_robots"] = True
            m = re.search(r"(?i)crawl-delay:\s*(\d+)", text)
            if m:
                result["crawl_delay"] = int(m.group(1))
            for bot in AI_BOTS:
                result["bots"][bot] = _parse_bot_access(text, bot)
    except Exception as e:
        result["error"] = str(e)
    return result


def _parse_bot_access(robots_text: str, bot_name: str) -> dict:
    bot_lower = bot_name.lower()
    applies = False
    disallowed_root = False
    mentioned = False
    for line in robots_text.splitlines():
        line = line.strip()
        if line.lower().startswith("user-agent:"):
            agent = line.split(":", 1)[1].strip().lower()
            applies = bot_lower in agent or agent == "*"
            if bot_lower in agent:
                mentioned = True
        elif line.lower().startswith("disallow:") and applies:
            path = line.split(":", 1)[1].strip()
            if path in ("/", "/*"):
                disallowed_root = True
        elif line == "":
            applies = False
    return {"mentioned": mentioned, "allowed": not disallowed_root}


def check_sitemap(base_url: str) -> dict:
    for path in ["/sitemap.xml", "/sitemap_index.xml"]:
        try:
            r = requests.get(urljoin(base_url, path), timeout=15, allow_redirects=True)
            if r.status_code == 200 and len(r.content) > 100:
                return {"exists": True, "url": urljoin(base_url, path), "size_kb": round(len(r.content) / 1024, 1)}
        except Exception:
            pass
    return {"exists": False}


def check_llms_txt(base_url: str) -> dict:
    for path in ["/llms.txt", "/llms-full.txt"]:
        try:
            r = requests.get(urljoin(base_url, path), timeout=10)
            if r.status_code == 200 and len(r.text.strip()) > 20:
                return {"exists": True, "path": path, "size_kb": round(len(r.content) / 1024, 1)}
        except Exception:
            pass
    return {"exists": False}


def build_domain_tech_scores(robots: dict, sitemap: dict, llms: dict, homepage_html_checks: dict) -> dict:
    s = {}
    s["robots_txt_accessible"] = 2 if robots.get("accessible") else 0
    s["gptbot_not_blocked"] = 2 if robots.get("bots", {}).get("GPTBot", {}).get("allowed", True) else 0
    s["perplexitybot_not_blocked"] = 2 if robots.get("bots", {}).get("PerplexityBot", {}).get("allowed", True) else 0
    s["claudebot_not_blocked"] = 2 if robots.get("bots", {}).get("ClaudeBot", {}).get("allowed", True) else 0
    s["google_extended_not_blocked"] = 2 if robots.get("bots", {}).get("Google-Extended", {}).get("allowed", True) else 0
    delay = robots.get("crawl_delay")
    s["crawl_delay_ok"] = 2 if delay is None or delay < 10 else (1 if delay < 30 else 0)
    s["sitemap_present"] = 2 if sitemap.get("exists") else 0
    s["sitemap_in_robots"] = 2 if robots.get("sitemap_in_robots") else 0
    s["llms_txt_present"] = 2 if llms.get("exists") else 0
    s["https_enabled"] = 2 if homepage_html_checks.get("https") else 0
    hreflang_count = homepage_html_checks.get("meta", {}).get("hreflang_count", 0)
    s["hreflang_used"] = 2 if hreflang_count > 0 else 1  # optional-ish — no penalty if single-lang
    return s


# --- HTML ANALYSIS ---

def analyze_html_bs4(html: str, url: str, raw_html: str = "") -> dict:
    if not html and not raw_html:
        return {}

    head_soup = BeautifulSoup(raw_html or html, "lxml")
    body_soup = BeautifulSoup(html or raw_html, "lxml")

    sem = {
        "article": bool(body_soup.find("article")),
        "main": bool(body_soup.find("main")),
        "section": bool(body_soup.find("section")),
        "nav": bool(body_soup.find("nav")),
        "header": bool(body_soup.find("header")),
        "footer": bool(body_soup.find("footer")),
    }

    h1s = body_soup.find_all("h1")
    h2s = body_soup.find_all("h2")
    h3s = body_soup.find_all("h3")
    heads_order_ok = _headings_order_ok(body_soup)

    meta = {
        "description": _get_meta(head_soup, name="description"),
        "og_title": _get_meta(head_soup, prop="og:title"),
        "og_description": _get_meta(head_soup, prop="og:description"),
        "og_image": _get_meta(head_soup, prop="og:image"),
        "twitter_card": _get_meta(head_soup, name="twitter:card"),
        "canonical": _get_link_href(head_soup, "canonical"),
        "viewport": _get_meta(head_soup, name="viewport"),
        "lang": head_soup.find("html").get("lang") if head_soup.find("html") else None,
        "hreflang_count": len(head_soup.find_all("link", rel="alternate", hreflang=True)),
        "title": head_soup.find("title").get_text(strip=True) if head_soup.find("title") else None,
    }

    imgs = body_soup.find_all("img")
    img_with_alt = sum(1 for i in imgs if i.get("alt"))

    schema = _extract_schema(head_soup)

    domain = urlparse(url).netloc
    anchors = body_soup.find_all("a", href=True)
    internal = external = 0
    for a in anchors:
        href = a["href"]
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        target = urlparse(urljoin(url, href)).netloc
        if target == domain:
            internal += 1
        elif target:
            external += 1

    tel_links = bool(body_soup.find("a", href=re.compile(r"^tel:", re.I)))
    mailto_links = bool(body_soup.find("a", href=re.compile(r"^mailto:", re.I)))
    forms = len(body_soup.find_all("form"))
    iframes = body_soup.find_all("iframe")
    has_map = any(re.search(r"(google\.com/maps|openstreetmap|mapy\.cz)", i.get("src", ""), re.I) for i in iframes)

    text = body_soup.get_text(separator=" ", strip=True)
    words = len(text.split()) if text else 0

    return {
        "semantic_html5": sem,
        "headings": {
            "h1_count": len(h1s),
            "h1_single": len(h1s) == 1,
            "h2_count": len(h2s),
            "h3_count": len(h3s),
            "hierarchy_ok": len(h2s) > 0 and heads_order_ok,
        },
        "meta": meta,
        "images": {"total": len(imgs), "with_alt": img_with_alt, "alt_coverage_pct": round(img_with_alt / len(imgs) * 100) if imgs else 100},
        "links": {"internal": internal, "external": external},
        "schema": schema,
        "content": {"word_count": words},
        "contact_signals": {"tel": tel_links, "mailto": mailto_links, "forms": forms, "map": has_map},
        "https": url.startswith("https://"),
        "html_size_kb": round(len(html.encode("utf-8")) / 1024, 1) if html else 0,
    }


def _get_meta(soup, name: str = None, prop: str = None) -> str | None:
    if name:
        t = soup.find("meta", attrs={"name": name})
    else:
        t = soup.find("meta", attrs={"property": prop})
    return (t.get("content") or "").strip() if t else None


def _get_link_href(soup, rel: str) -> str | None:
    t = soup.find("link", rel=rel)
    return t.get("href") if t else None


def _headings_order_ok(soup) -> bool:
    order = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        order.append(int(tag.name[1]))
    if not order:
        return False
    prev = 0
    for lvl in order:
        if prev and lvl > prev + 1:
            return False
        prev = lvl
    return True


def _extract_schema(soup) -> dict:
    scripts = soup.find_all("script", type="application/ld+json")
    found_types: set[str] = set()
    has_author = has_datepub = has_dateupd = False
    for s in scripts:
        if not s.string:
            continue
        try:
            data = json.loads(s.string)
        except json.JSONDecodeError:
            continue
        for node in _walk_schema(data):
            t = node.get("@type") if isinstance(node, dict) else None
            if isinstance(t, str):
                found_types.add(t)
            elif isinstance(t, list):
                for x in t:
                    if isinstance(x, str):
                        found_types.add(x)
            if isinstance(node, dict):
                if "author" in node:
                    has_author = True
                if "datePublished" in node:
                    has_datepub = True
                if "dateModified" in node:
                    has_dateupd = True
    types_lower = {t.lower() for t in found_types}
    return {
        "any": bool(found_types),
        "types": sorted(found_types),
        "faq": any("faqpage" in t for t in types_lower),
        "article": any(t in types_lower for t in ["article", "newsarticle", "blogposting"]),
        "breadcrumb": "breadcrumblist" in types_lower,
        "organization": "organization" in types_lower or "localbusiness" in types_lower,
        "localbusiness": "localbusiness" in types_lower,
        "person": "person" in types_lower,
        "product": "product" in types_lower,
        "service": "service" in types_lower,
        "website": "website" in types_lower,
        "itemlist": "itemlist" in types_lower,
        "has_author": has_author,
        "has_datepublished": has_datepub,
        "has_datemodified": has_dateupd,
    }


def _walk_schema(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_schema(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_schema(item)


# --- PAGE-TYPE-AWARE TECH SCORING ---

def build_page_tech_scores(page_type: str, hc: dict) -> dict:
    """Per-page tech scores with schema requirements dependent on page type."""
    if not hc:
        return {}
    s = {}
    meta = hc.get("meta", {})
    heads = hc.get("headings", {})
    schema = hc.get("schema", {})
    contact_sig = hc.get("contact_signals", {})
    imgs = hc.get("images", {})

    # Universal (every page type)
    s["meta_title_present"] = 2 if meta.get("title") else 0
    s["meta_description"] = 2 if meta.get("description") else 0
    s["canonical_tag"] = 2 if meta.get("canonical") else 0
    s["h1_single"] = 2 if heads.get("h1_single") else (1 if heads.get("h1_count", 0) > 0 else 0)
    s["heading_hierarchy"] = 2 if heads.get("hierarchy_ok") else 0
    s["og_tags"] = 2 if (meta.get("og_title") and meta.get("og_description")) else (1 if meta.get("og_title") else 0)
    s["viewport_meta"] = 2 if meta.get("viewport") else 0
    s["lang_attribute"] = 2 if meta.get("lang") else 0
    s["image_alt_coverage"] = 2 if imgs.get("alt_coverage_pct", 0) >= 90 else (1 if imgs.get("alt_coverage_pct", 0) >= 50 else 0)
    sem = hc.get("semantic_html5", {})
    s["semantic_html5_tags"] = 2 if (sem.get("article") or sem.get("main")) else (1 if sem.get("section") else 0)
    size_kb = hc.get("html_size_kb", 0)
    s["response_size_ok"] = 0 if size_kb > 500 else (1 if size_kb > 200 else 2)

    # Page-type specific schema requirements
    if page_type == "homepage":
        s["organization_schema"] = 2 if schema.get("organization") else 0
        s["website_schema"] = 2 if schema.get("website") else 1  # optional, soft
        s["any_schema"] = 2 if schema.get("any") else 0
    elif page_type == "service":
        has_product_or_service = schema.get("product") or schema.get("service")
        s["product_or_service_schema"] = 2 if has_product_or_service else 0
        s["faq_schema_bonus"] = 2 if schema.get("faq") else 1  # bonus
        s["breadcrumb_schema"] = 2 if schema.get("breadcrumb") else 1
    elif page_type == "article":
        s["article_schema"] = 2 if schema.get("article") else 0
        s["schema_author_field"] = 2 if schema.get("has_author") else 0
        s["schema_dates"] = 2 if (schema.get("has_datepublished") and schema.get("has_datemodified")) else (1 if schema.get("has_datepublished") else 0)
        s["faq_schema_bonus"] = 2 if schema.get("faq") else 1
        s["breadcrumb_schema"] = 2 if schema.get("breadcrumb") else 1
    elif page_type == "about":
        s["organization_schema"] = 2 if schema.get("organization") else 0
        s["person_schema_team"] = 2 if schema.get("person") else 1  # optional
        s["breadcrumb_schema"] = 2 if schema.get("breadcrumb") else 1
    elif page_type == "contact":
        s["localbusiness_or_organization_schema"] = 2 if (schema.get("localbusiness") or schema.get("organization")) else 0
        s["tel_link_present"] = 2 if contact_sig.get("tel") else 0
        s["mailto_link_present"] = 2 if contact_sig.get("mailto") else 0
        s["contact_form_present"] = 2 if contact_sig.get("forms", 0) > 0 else 0
    elif page_type == "category":
        s["breadcrumb_schema"] = 2 if schema.get("breadcrumb") else 0
        s["itemlist_schema"] = 2 if schema.get("itemlist") else 1  # optional-ish
        s["any_schema"] = 2 if schema.get("any") else 0
    else:  # other
        s["any_schema"] = 2 if schema.get("any") else 0
        s["breadcrumb_schema"] = 2 if schema.get("breadcrumb") else 1

    return s


def tech_score_pct(tech_scores: dict) -> int:
    if not tech_scores:
        return 0
    return round((sum(tech_scores.values()) / (len(tech_scores) * 2)) * 100)


# --- GEMINI ---

def _gemini_call(prompt: str, temperature: float = 0.1, max_tokens: int = 4096) -> str:
    r = requests.post(
        GEMINI_URL,
        params={"key": GEMINI_KEY},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        },
        timeout=90,
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1:
        text = text[first : last + 1]
    return json.loads(text)


def _page_factor_prompt(page_type: str, url: str, title: str, meta_desc: str, content: str) -> str:
    spec = PAGE_TYPE_FACTORS[page_type]
    factors = spec["factors"]
    factor_spec = "\n".join(f'    "{f}": {{"score": 0, "note": "konkretna obserwacja po polsku (cytat/parafraza fragmentu)"}}' for f in factors)
    label = PAGE_TYPE_LABELS.get(page_type, page_type)

    return f"""Jesteś {spec["role"]}. Analizujesz POJEDYNCZĄ stronę danego typu.

<kontekst>
Typ strony: {label} ({page_type})
Dziedzina oceny: {spec["domain_desc"]}
</kontekst>

<zadanie>
Oceń dokładnie {len(factors)} czynników ZGODNIE z typem strony. Skala:
- 0 = brak/słaby/krytyczna luka
- 1 = częściowy/średni/wymaga poprawy
- 2 = dobry/pełny/wzorowo

WAŻNE: Oceniasz czynniki DOPASOWANE do typu strony. NIE penalizuj braku dat publikacji na stronie sprzedażowej, NIE penalizuj braku testimoniali na artykule blogowym. Kontekst decyduje.
</zadanie>

<proces_myślenia>
1. Przeczytaj treść (poniżej).
2. Dla każdego czynnika znajdź DOWÓD w treści (lub jego brak).
3. "note" MUSI zawierać konkretny cytat, parafrazę lub precyzyjną obserwację — NIE ogólniki.
4. Dopiero po analizie dowodu — ustal score.
</proces_myślenia>

<przykład_dobra_note>
"Znaleziono 'Karolina Nowak, 8 lat jako analityk SEO w X, ex-Y' + zdjęcie przy artykule."
</przykład_dobra_note>
<przykład_zła_note>
"Jest sekcja o autorze." — ZA OGÓLNIKOWE, brak konkretu.
</przykład_zła_note>

<dane_strony>
  <url>{url}</url>
  <title>{title}</title>
  <meta_description>{meta_desc}</meta_description>
  <typ_strony>{page_type}</typ_strony>
</dane_strony>

<treść>
{content}
</treść>

WSZYSTKIE "note" po polsku. Konkret > ogólnik.

Zwróć TYLKO poprawny JSON (bez markdown):
{{
{factor_spec}
}}"""


def analyze_page(url: str, page_type: str, title: str, meta_desc: str, content: str) -> dict:
    prompt = _page_factor_prompt(page_type, url, title, meta_desc, content)
    return _extract_json(_gemini_call(prompt, temperature=0.15, max_tokens=3000))


def generate_fan_out(homepage_url: str, title: str, content: str) -> dict:
    prompt = f"""Jesteś ekspertem AI SEO. Symulujesz query fan-out — zestaw pytań, które użytkownicy zadają ChatGPT/Perplexity w niszy tej marki.

<zadanie>
1. Na podstawie treści (homepage + kluczowe podstrony) wygeneruj 12 realistycznych pytań użytkowników. Różne intencje: informacyjne, transakcyjne, porównawcze, problem-solving.
2. Oceń każde pod kątem pokrycia przez treść witryny: "covered" | "partial" | "missing".
3. Jeśli partial/missing — napisz co trzeba dodać.
</zadanie>

<url>{homepage_url}</url>
<title>{title}</title>

<treść>
{content}
</treść>

Zwróć TYLKO JSON (po polsku):
{{
  "queries": [
    {{"query": "pytanie użytkownika", "coverage": "covered|partial|missing", "gap_note": "co dodać (pusty jeśli covered)"}}
  ]
}}

Dokładnie 12 pytań, różnorodne intencje."""
    return _extract_json(_gemini_call(prompt, temperature=0.4, max_tokens=3000))


def synthesize_findings(page_audits: list[dict], domain_tech: dict, domain_tech_scores: dict, fan_out: dict, homepage_url: str, site_title: str) -> dict:
    """Generate prioritized recommendations with URL + page_type refs."""
    weak_per_page: list[str] = []
    for pa in page_audits:
        url = pa.get("url", "")
        pt = pa.get("page_type", "other")
        for k, v in (pa.get("factors") or {}).items():
            if isinstance(v, dict) and v.get("score", 0) == 0:
                note = v.get("note", "")
                weak_per_page.append(f"[{pt} | {url}] {k}: {note}")
    weak_per_page = weak_per_page[:12]

    weak_tech_domain = [k for k, v in domain_tech_scores.items() if v == 0][:6]
    weak_tech_pages: list[str] = []
    for pa in page_audits:
        url = pa.get("url", "")
        pt = pa.get("page_type", "other")
        for k, v in (pa.get("tech_scores") or {}).items():
            if v == 0:
                weak_tech_pages.append(f"[{pt} | {url}] {k}")
    weak_tech_pages = weak_tech_pages[:8]

    missing_queries = [q["query"] for q in fan_out.get("queries", []) if q.get("coverage") in ("missing", "partial")][:6]

    pages_list = "\n".join(f"- {pa.get('page_type')}: {pa.get('url')}" for pa in page_audits)

    prompt = f"""Jesteś starszym konsultantem AI SEO. Masz wyniki per-URL audytu. Stwórz syntetyczny werdykt + rekomendacje z przypisaniem do KONKRETNEJ podstrony.

<input>
<strona>{homepage_url}</strona>
<tytuł_domeny>{site_title}</tytuł_domeny>

<audytowane_podstrony>
{pages_list}
</audytowane_podstrony>

<krytyczne_luki_treści_per_strona>
{chr(10).join(f"- {s}" for s in weak_per_page) or "(brak krytycznych)"}
</krytyczne_luki_treści_per_strona>

<krytyczne_luki_techniczne_domena>
{", ".join(weak_tech_domain) or "(brak)"}
</krytyczne_luki_techniczne_domena>

<krytyczne_luki_techniczne_per_strona>
{chr(10).join(f"- {s}" for s in weak_tech_pages) or "(brak)"}
</krytyczne_luki_techniczne_per_strona>

<brakujące_pytania_fan_out>
{chr(10).join(f"- {q}" for q in missing_queries) or "(brak)"}
</brakujące_pytania_fan_out>
</input>

<zasady>
- "top_recommendations": 6 działań UPORZĄDKOWANYCH wg IMPACT × EASE (najpierw szybkie wygrane z wysokim wpływem).
- KAŻDA rekomendacja: pole "page_url" wskazujące konkretną podstronę (lub "domain" dla zmian globalnych jak robots/sitemap/llms.txt).
- KAŻDA rekomendacja: pole "page_type" wskazujące typ (homepage/service/article/about/contact/category/domain).
- Tekst rekomendacji musi być KONKRETNY i DOPASOWANY do typu strony. NIE rekomenduj dat publikacji na stronie sprzedażowej.
- "content_gaps": 5 tematów których brakuje domenie jako całości (luki topical authority).
- "overall_assessment": 3 zdania po polsku: (1) werdykt ogólny, (2) największa mocna strona, (3) największy bloker.
</zasady>

Zwróć TYLKO JSON (po polsku):
{{
  "top_recommendations": [
    {{"priority": 1, "text": "Konkretne działanie...", "page_url": "https://... lub 'domain'", "page_type": "homepage|service|article|about|contact|category|domain"}},
    ...
  ],
  "content_gaps": ["...", "...", "...", "...", "..."],
  "overall_assessment": "3 zdania po polsku."
}}"""
    return _extract_json(_gemini_call(prompt, temperature=0.3, max_tokens=2500))


# --- SCORING ---

def factor_score_pct(factors: dict) -> int:
    if not factors:
        return 0
    valid = [v for v in factors.values() if isinstance(v, dict) and "score" in v]
    if not valid:
        return 0
    total = sum(v.get("score", 0) for v in valid)
    return round((total / (len(valid) * 2)) * 100)


def fan_out_score(fan_out: dict) -> int:
    queries = fan_out.get("queries", [])
    if not queries:
        return 0
    pts = sum({"covered": 2, "partial": 1, "missing": 0}.get(q.get("coverage", "missing"), 0) for q in queries)
    return round(pts / (len(queries) * 2) * 100)


def combined_page_score(factor_pct: int, tech_pct: int) -> int:
    return round(factor_pct * 0.6 + tech_pct * 0.4)


# --- SSE AUDIT STREAM ---

def audit_stream(url: str):
    def event(step: str, data: dict):
        return f"data: {json.dumps({'step': step, **data})}\n\n"

    try:
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        # 1. URL discovery
        yield event("progress", {"message": "Wykrywanie podstron (sitemap.xml → Firecrawl /map)...", "pct": 5})
        sitemap_urls = fetch_sitemap_urls(base_url)
        discovery_source = "sitemap" if sitemap_urls else "firecrawl-map"
        if not sitemap_urls:
            sitemap_urls = fetch_firecrawl_map(base_url)
        yield event("progress", {"message": f"Znaleziono {len(sitemap_urls)} URL-i ({discovery_source}). Gemini klasyfikuje + wybiera reprezentację...", "pct": 12})

        # 2. Gemini selects + classifies URLs
        selected = select_and_classify_urls(sitemap_urls, url, base_url) if sitemap_urls else []

        # Always include homepage first
        homepage_entry = {"url": url, "page_type": "homepage", "reason": "strona główna"}
        url_entries: list[dict] = [homepage_entry]
        seen = {url}
        for s in selected:
            if s["url"] not in seen:
                url_entries.append(s)
                seen.add(s["url"])
            if len(url_entries) >= MAX_AUDIT_PAGES:
                break

        yield event("progress", {"message": f"Scrapowanie {len(url_entries)} podstron (Firecrawl, parallel)...", "pct": 18})

        # 3. Scrape all selected URLs (full: markdown + html + rawHtml + links)
        all_urls = [e["url"] for e in url_entries]
        scrape_results = scrape_pages_parallel(all_urls)

        yield event("progress", {"message": "Analiza techniczna domeny (robots, sitemap, llms.txt) + HTML per strona...", "pct": 32})

        # 4. Domain-level checks
        robots = check_robots_txt(base_url)
        sitemap = check_sitemap(base_url)
        llms = check_llms_txt(base_url)

        # Per-page HTML analysis + tech scoring
        page_data: list[dict] = []
        for entry in url_entries:
            u = entry["url"]
            pt = entry["page_type"]
            scraped = scrape_results.get(u, {})
            if not scraped:
                continue
            meta = scraped.get("metadata", {})
            title = meta.get("title", "") or ""
            meta_desc = meta.get("description", "") or ""
            html = scraped.get("html", "") or ""
            raw_html = scraped.get("rawHtml", "") or ""
            markdown = scraped.get("markdown", "") or ""
            hc = analyze_html_bs4(html, u, raw_html)
            page_tech = build_page_tech_scores(pt, hc)
            page_data.append({
                "url": u,
                "page_type": pt,
                "reason": entry.get("reason", ""),
                "title": title,
                "meta_desc": meta_desc,
                "html_checks": hc,
                "tech_scores": page_tech,
                "markdown": markdown,
            })

        if not page_data:
            yield event("error", {"message": "Nie udało się scrapować żadnej podstrony."})
            return

        # Domain tech scores (uses homepage html_checks for https/hreflang)
        homepage_data = next((p for p in page_data if p["page_type"] == "homepage"), page_data[0])
        domain_tech_scores = build_domain_tech_scores(robots, sitemap, llms, homepage_data["html_checks"])

        yield event("progress", {"message": "Per-URL analiza: każda strona z factor setem dopasowanym do typu (Gemini, parallel)...", "pct": 48})

        # 5. Parallel per-page Gemini analysis
        def _analyze_one(pd):
            content = pd["markdown"][:MAX_CONTENT_CHARS]
            try:
                factors = analyze_page(pd["url"], pd["page_type"], pd["title"], pd["meta_desc"], content)
            except Exception as e:
                factors = {"error": {"score": 0, "note": f"Analiza nieudana: {str(e)[:200]}"}}
            return pd["url"], factors

        with ThreadPoolExecutor(max_workers=5) as ex:
            fut_analyze = {ex.submit(_analyze_one, pd): pd["url"] for pd in page_data}
            analysis_map: dict[str, dict] = {}
            for fut in as_completed(fut_analyze):
                u, factors = fut.result()
                analysis_map[u] = factors

        yield event("progress", {"message": "Symulacja query fan-out dla całej domeny (Gemini)...", "pct": 68})

        # 6. Fan-out (global, on combined content)
        combined_parts = []
        for pd in page_data:
            combined_parts.append(f"=== {pd['page_type'].upper()}: {pd['url']} ===\n{pd['markdown'][:3000]}")
        combined_content = "\n\n".join(combined_parts)[:15000]
        homepage_title = homepage_data["title"]
        try:
            fan_out = generate_fan_out(url, homepage_title, combined_content)
        except Exception as e:
            fan_out = {"queries": [], "error": str(e)}

        yield event("progress", {"message": "Budowanie per-URL audytów i obliczanie wyników...", "pct": 82})

        # 7. Build per-page audits with scores
        page_audits = []
        for pd in page_data:
            factors = analysis_map.get(pd["url"], {})
            f_pct = factor_score_pct(factors)
            t_pct = tech_score_pct(pd["tech_scores"])
            page_audits.append({
                "url": pd["url"],
                "page_type": pd["page_type"],
                "page_type_label": PAGE_TYPE_LABELS.get(pd["page_type"], pd["page_type"]),
                "reason": pd["reason"],
                "title": pd["title"],
                "meta_desc": pd["meta_desc"],
                "factors": factors,
                "factor_score_pct": f_pct,
                "tech_scores": pd["tech_scores"],
                "tech_score_pct": t_pct,
                "combined_score": combined_page_score(f_pct, t_pct),
                "html_checks_summary": {
                    "word_count": pd["html_checks"].get("content", {}).get("word_count", 0),
                    "html_size_kb": pd["html_checks"].get("html_size_kb", 0),
                    "schema_types": pd["html_checks"].get("schema", {}).get("types", []),
                    "h1_count": pd["html_checks"].get("headings", {}).get("h1_count", 0),
                    "internal_links": pd["html_checks"].get("links", {}).get("internal", 0),
                    "external_links": pd["html_checks"].get("links", {}).get("external", 0),
                    "images": pd["html_checks"].get("images", {}),
                    "canonical": pd["html_checks"].get("meta", {}).get("canonical"),
                },
            })

        yield event("progress", {"message": "Synteza: priorytetyzowane rekomendacje per strona...", "pct": 92})

        try:
            synth = synthesize_findings(page_audits, {"robots": robots, "sitemap": sitemap, "llms": llms}, domain_tech_scores, fan_out, url, homepage_title)
        except Exception as e:
            synth = {"top_recommendations": [], "content_gaps": [], "overall_assessment": f"Synteza nieudana: {e}"}

        # 8. Aggregate scores
        domain_tech_pct = tech_score_pct(domain_tech_scores)
        fan_pct = fan_out_score(fan_out)
        page_scores = [pa["combined_score"] for pa in page_audits] if page_audits else [0]
        avg_page = round(sum(page_scores) / len(page_scores))
        overall = round(avg_page * 0.6 + domain_tech_pct * 0.25 + fan_pct * 0.15)

        result = {
            "url": url,
            "discovery_source": discovery_source,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "homepage_title": homepage_title,
            "homepage_meta_desc": homepage_data["meta_desc"],
            "scores": {
                "overall": overall,
                "page_average": avg_page,
                "domain_technical": domain_tech_pct,
                "fan_out": fan_pct,
            },
            "page_audits": page_audits,
            "domain_technical": {
                "scores": domain_tech_scores,
                "score_pct": domain_tech_pct,
                "robots": {k: v for k, v in robots.items() if k != "raw"},
                "sitemap": sitemap,
                "llms_txt": llms,
            },
            "fan_out": fan_out,
            "synthesis": synth,
        }
        yield event("done", {"result": result, "pct": 100})

    except Exception as e:
        yield event("error", {"message": str(e)})


# --- ROUTES ---

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/audit/stream")
async def audit_endpoint(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="URL required")
    if not url.startswith("http"):
        url = "https://" + url
    return StreamingResponse(
        audit_stream(url),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
