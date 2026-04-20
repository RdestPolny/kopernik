#!/usr/bin/env python3
"""AI SEO Audit — FastAPI app. Smart URL discovery, Firecrawl full, Gemini specialized calls."""

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

app = FastAPI(title="AI SEO Audit")
app.mount("/static", StaticFiles(directory="static"), name="static")


class AuditRequest(BaseModel):
    url: str


# --- URL DISCOVERY ---

def fetch_sitemap_urls(base_url: str) -> list[str]:
    """Parse sitemap.xml / sitemap_index.xml. Returns up to SITEMAP_CAP URLs."""
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
    # dedupe keep order
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
    # sitemap index → recurse
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
    # URL set
    for loc in root.findall(".//sm:url/sm:loc", ns):
        if loc.text:
            urls.append(loc.text.strip())
    return urls


def fetch_firecrawl_map(base_url: str) -> list[str]:
    """Fallback when no sitemap. Returns URL list from Firecrawl /map."""
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


def select_representative_urls(all_urls: list[str], homepage_url: str, base_url: str) -> list[str]:
    """Ask Gemini to pick MAX_AUDIT_PAGES-1 representative URLs from list."""
    # Pre-filter: same domain, strip fragments/trackers
    domain = urlparse(base_url).netloc
    clean: list[str] = []
    seen = set()
    for u in all_urls:
        pu = urlparse(u)
        if pu.netloc and pu.netloc != domain:
            continue
        # skip binary assets
        if re.search(r"\.(jpg|jpeg|png|gif|webp|svg|ico|css|js|xml|woff2?|ttf|mp4|zip)(\?|$)", u, re.I):
            continue
        key = (pu.path.rstrip("/"), pu.query)
        if key in seen or pu.path in ("", "/"):
            continue
        seen.add(key)
        clean.append(u)

    if not clean:
        return []

    # Cap input to Gemini: first 80 URLs is plenty
    candidates = clean[:80]

    prompt = f"""Jesteś ekspertem SEO. Wybierz {MAX_AUDIT_PAGES - 1} URL-i z poniższej listy które dają NAJLEPSZĄ reprezentację domeny do audytu AI-SEO (E-E-A-T, Topical Authority, RAG).

Kryteria wyboru:
- RÓŻNORODNOŚĆ typów: kategoria/usługa, artykuł/blog, strona o autorze/zespole, kontakt/o-firmie, produkt/case study
- UNIKAJ: polityki prywatności, regulaminów, stron paginacji, tagów, kategorii technicznych, duplikatów
- PRIORYTET: strony treściowe z potencjałem cytowania przez LLM

Homepage (już wybrana, nie wliczać): {homepage_url}

URL-e kandydaci:
{chr(10).join(f"- {u}" for u in candidates)}

Zwróć TYLKO JSON (bez markdown, bez komentarzy):
{{
  "selected": [
    {{"url": "https://...", "reason": "krótkie uzasadnienie po polsku"}},
    ...
  ]
}}

Dokładnie {MAX_AUDIT_PAGES - 1} URL-i, każdy z unikatowego segmentu."""

    try:
        text = _gemini_call(prompt, temperature=0.2, max_tokens=1024)
        parsed = _extract_json(text)
        picked = [item["url"] for item in parsed.get("selected", []) if item.get("url") in set(candidates)]
        return picked[: MAX_AUDIT_PAGES - 1]
    except Exception:
        # heuristic fallback
        return _heuristic_pick(candidates)


def _heuristic_pick(urls: list[str]) -> list[str]:
    """Fallback: one URL per bucket."""
    buckets = {"service": [], "blog": [], "about": [], "contact": [], "other": []}
    for u in urls:
        p = urlparse(u).path.lower()
        if any(k in p for k in ["/uslugi", "/oferta", "/services", "/produkt", "/cennik", "/pricing"]):
            buckets["service"].append(u)
        elif any(k in p for k in ["/blog/", "/artykul", "/article/", "/post/", "/news/", "/poradnik", "/wiedza"]):
            buckets["blog"].append(u)
        elif any(k in p for k in ["/o-nas", "/about", "/o-firmie", "/zespol", "/team"]):
            buckets["about"].append(u)
        elif any(k in p for k in ["/kontakt", "/contact"]):
            buckets["contact"].append(u)
        else:
            buckets["other"].append(u)
    out = []
    for b in ["service", "blog", "about", "contact", "other"]:
        if buckets[b]:
            out.append(buckets[b][0])
    return out[: MAX_AUDIT_PAGES - 1]


# --- SCRAPING ---

def scrape_with_firecrawl(url: str, full: bool = True) -> dict:
    payload = {
        "url": url,
        "formats": ["markdown", "html", "rawHtml", "links"] if full else ["markdown"],
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
    """Scrape homepage (full) + subpages (markdown only for budget)."""
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(scrape_with_firecrawl, u, False): u for u in urls}
        for f in as_completed(futures):
            u = futures[f]
            try:
                results[u] = f.result()
            except Exception:
                results[u] = {}
    return results


# --- TECHNICAL CHECKS ---

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
    """llms.txt standard — AI-friendly content map."""
    for path in ["/llms.txt", "/llms-full.txt"]:
        try:
            r = requests.get(urljoin(base_url, path), timeout=10)
            if r.status_code == 200 and len(r.text.strip()) > 20:
                return {"exists": True, "path": path, "size_kb": round(len(r.content) / 1024, 1)}
        except Exception:
            pass
    return {"exists": False}


# --- HTML ANALYSIS (BeautifulSoup) ---

def analyze_html_bs4(html: str, url: str, raw_html: str = "") -> dict:
    """Proper DOM parse. rawHtml for head (meta/schema), html for body semantics."""
    if not html and not raw_html:
        return {}

    head_soup = BeautifulSoup(raw_html or html, "lxml")
    body_soup = BeautifulSoup(html or raw_html, "lxml")

    # Semantic HTML5
    sem = {
        "article": bool(body_soup.find("article")),
        "main": bool(body_soup.find("main")),
        "section": bool(body_soup.find("section")),
        "nav": bool(body_soup.find("nav")),
        "header": bool(body_soup.find("header")),
        "footer": bool(body_soup.find("footer")),
    }

    # Headings
    h1s = body_soup.find_all("h1")
    h2s = body_soup.find_all("h2")
    h3s = body_soup.find_all("h3")
    heads_order_ok = _headings_order_ok(body_soup)

    # Meta
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
    }

    # Images alt
    imgs = body_soup.find_all("img")
    img_with_alt = sum(1 for i in imgs if i.get("alt"))

    # Schema.org JSON-LD
    schema = _extract_schema(head_soup)

    # Links (internal vs external)
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

    # Content signals
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
    """h2 must precede any h3; h1 must come first if present."""
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
    """Parse JSON-LD blocks, catalog types and key entities."""
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
        "person": "person" in types_lower,
        "product": "product" in types_lower,
        "has_author": has_author,
        "has_datepublished": has_datepub,
        "has_datemodified": has_dateupd,
    }


def _walk_schema(node):
    """Recurse through JSON-LD structures."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_schema(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_schema(item)


def build_tech_scores(robots: dict, sitemap: dict, llms: dict, hc: dict) -> dict:
    s = {}
    s["robots_txt_accessible"] = 2 if robots.get("accessible") else 0
    s["gptbot_not_blocked"] = 2 if robots.get("bots", {}).get("GPTBot", {}).get("allowed", True) else 0
    s["perplexitybot_not_blocked"] = 2 if robots.get("bots", {}).get("PerplexityBot", {}).get("allowed", True) else 0
    s["claudebot_not_blocked"] = 2 if robots.get("bots", {}).get("ClaudeBot", {}).get("allowed", True) else 0
    delay = robots.get("crawl_delay")
    s["crawl_delay_ok"] = 2 if delay is None or delay < 10 else (1 if delay < 30 else 0)
    s["sitemap_present"] = 2 if sitemap.get("exists") else 0
    s["llms_txt_present"] = 2 if llms.get("exists") else 0
    s["https_enabled"] = 2 if hc.get("https") else 0
    sem = hc.get("semantic_html5", {})
    s["semantic_html5_tags"] = 2 if (sem.get("article") or sem.get("main")) else (1 if sem.get("section") else 0)
    heads = hc.get("headings", {})
    s["h1_single"] = 2 if heads.get("h1_single") else (1 if heads.get("h1_count", 0) > 0 else 0)
    s["heading_hierarchy"] = 2 if heads.get("hierarchy_ok") else 0
    meta = hc.get("meta", {})
    s["meta_description"] = 2 if meta.get("description") else 0
    s["og_tags"] = 2 if (meta.get("og_title") and meta.get("og_description")) else (1 if meta.get("og_title") else 0)
    s["canonical_tag"] = 2 if meta.get("canonical") else 0
    s["lang_attribute"] = 2 if meta.get("lang") else 0
    s["viewport_meta"] = 2 if meta.get("viewport") else 0
    s["image_alt_coverage"] = 2 if hc.get("images", {}).get("alt_coverage_pct", 0) >= 90 else (1 if hc.get("images", {}).get("alt_coverage_pct", 0) >= 50 else 0)
    schema = hc.get("schema", {})
    s["any_schema_markup"] = 2 if schema.get("any") else 0
    s["faq_schema"] = 2 if schema.get("faq") else 0
    s["article_schema"] = 2 if schema.get("article") else 0
    s["breadcrumb_schema"] = 2 if schema.get("breadcrumb") else 0
    s["organization_schema"] = 2 if schema.get("organization") else 0
    s["schema_author_field"] = 2 if schema.get("has_author") else 0
    s["schema_dates"] = 2 if (schema.get("has_datepublished") and schema.get("has_datemodified")) else (1 if schema.get("has_datepublished") else 0)
    size_kb = hc.get("html_size_kb", 0)
    s["response_size_ok"] = 0 if size_kb > 500 else (1 if size_kb > 200 else 2)
    return s


# --- GEMINI CALLS ---

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
    # find first { ... last }
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1:
        text = text[first : last + 1]
    return json.loads(text)


EEAT_FACTORS = [
    "author_bio_present", "author_credentials_stated", "publication_date_visible",
    "last_updated_date_visible", "external_authoritative_citations",
    "firsthand_experience_signals", "unique_data_or_original_statistics",
    "sources_cited_inline", "about_or_contact_page_linked", "content_not_generic_ai_fluff",
]
TOPICAL_FACTORS = [
    "single_clear_topic_focus", "internal_links_to_related_content",
    "pillar_or_cluster_page_structure_signals", "content_depth_comprehensive",
    "multiple_subtopics_via_h2_sections", "direct_definitions_or_answers_present",
    "fan_out_query_coverage", "content_freshness_or_timeliness_signals",
    "unique_angle_or_original_pov", "clear_user_value_proposition",
]
RAG_FACTORS = [
    "headings_formatted_as_questions", "faq_section_present",
    "direct_answer_near_content_start", "numbered_lists_or_bullets_for_steps",
    "table_of_contents_present", "key_facts_or_stats_scannable",
    "summary_or_tldr_section", "concise_extractable_definitions",
    "data_tables_present", "overall_scannable_structure",
]


def _factor_prompt(role: str, domain_desc: str, factors: list[str], url: str, title: str, meta_desc: str, content: str) -> str:
    factor_spec = "\n".join(f'    "{f}": {{"score": 0, "note": "konkretna obserwacja po polsku"}}' for f in factors)
    return f"""Jesteś {role}. Analizujesz wiele podstron jednej domeny jako całość.

<zadanie>
Oceń każdy z {len(factors)} czynników w dziedzinie: {domain_desc}
Skala: 0 = brak/słaby, 1 = częściowy/średni, 2 = dobry/pełny. Bądź krytyczny i obiektywny.
</zadanie>

<proces>
1. Najpierw przeczytaj treść.
2. Dla każdego czynnika znajdź DOWÓD w treści (lub jego brak).
3. Napisz "note" cytując/parafrazując konkretny fragment (NIE generyczne stwierdzenia).
4. Score tylko po analizie dowodów.
</proces>

<przykład czynnik="author_bio_present">
  <dobry_wynik score="2">Znaleziono "O autorze: Jan Kowalski, 12 lat w e-commerce, ex-CTO XYZ" przy każdym artykule.</dobry_wynik>
  <zły_wynik score="0">Brak jakichkolwiek informacji o autorach. Żadnego podpisu pod artykułami.</zły_wynik>
  <zły_opis>"Jest autor" — ZA OGÓLNIKOWE, brak cytatu.</zły_opis>
</przykład>

<dane_domeny>
  <url>{url}</url>
  <title>{title}</title>
  <meta_description>{meta_desc}</meta_description>
</dane_domeny>

<treść>
{content}
</treść>

WAŻNE: Wszystkie "note" MUSZĄ być po polsku, cytować lub parafrazować konkretne fragmenty.

Zwróć TYLKO poprawny JSON (bez markdown, bez wyjaśnień poza JSON):
{{
{factor_spec}
}}"""


def analyze_eeat(url: str, title: str, meta_desc: str, content: str) -> dict:
    prompt = _factor_prompt(
        role="ekspertem od E-E-A-T i wiarygodności treści dla crawlerów LLM (GPTBot, PerplexityBot, ClaudeBot)",
        domain_desc="E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness) + model REAL (Relevant, Evidence, Accessible, Legitimate)",
        factors=EEAT_FACTORS, url=url, title=title, meta_desc=meta_desc, content=content,
    )
    return _extract_json(_gemini_call(prompt))


def analyze_topical(url: str, title: str, meta_desc: str, content: str) -> dict:
    prompt = _factor_prompt(
        role="ekspertem od Topical Authority i architektury klastrów/filarów treści",
        domain_desc="Topical Authority, głębia tematyczna, query fan-out coverage, unikatowy punkt widzenia",
        factors=TOPICAL_FACTORS, url=url, title=title, meta_desc=meta_desc, content=content,
    )
    return _extract_json(_gemini_call(prompt))


def analyze_rag(url: str, title: str, meta_desc: str, content: str) -> dict:
    prompt = _factor_prompt(
        role="ekspertem od RAG (Retrieval-Augmented Generation) extractability i cytowania przez ChatGPT/Perplexity",
        domain_desc="RAG extractability — jak łatwo LLM może wyodrębnić, skanować i cytować fakty z treści",
        factors=RAG_FACTORS, url=url, title=title, meta_desc=meta_desc, content=content,
    )
    return _extract_json(_gemini_call(prompt))


def generate_fan_out(url: str, title: str, content: str) -> dict:
    """Simulate 12 queries a user might ask ChatGPT/Perplexity in this niche, check coverage."""
    prompt = f"""Jesteś ekspertem AI SEO. Symulujesz query fan-out — zestaw pytań, które użytkownicy zadają ChatGPT/Perplexity w tej niszy.

<zadanie>
1. Na podstawie treści wygeneruj 12 realistycznych pytań użytkowników (różne intencje: informacyjne, transakcyjne, porównawcze, problem-solving).
2. Dla każdego oceń czy treść zawiera wystarczającą odpowiedź: "covered", "partial", "missing".
3. Jeśli "partial" lub "missing" — napisz co trzeba dodać.
</zadanie>

<url>{url}</url>
<title>{title}</title>

<treść>
{content}
</treść>

Zwróć TYLKO JSON po polsku:
{{
  "queries": [
    {{"query": "pytanie użytkownika po polsku", "coverage": "covered|partial|missing", "gap_note": "co dodać jeśli partial/missing (pusty string jeśli covered)"}}
  ]
}}

Dokładnie 12 pytań, różnorodne."""
    return _extract_json(_gemini_call(prompt, temperature=0.4, max_tokens=3000))


def synthesize_findings(eeat: dict, topical: dict, rag: dict, fan_out: dict, tech_scores: dict, url: str, title: str) -> dict:
    """Final pass: recommendations + gaps + overall assessment based on previous analyses."""
    def low_factors(analysis: dict, cat: str) -> list[str]:
        return [f"{cat}.{k}: {v.get('note', '')}" for k, v in analysis.items() if isinstance(v, dict) and v.get("score", 0) == 0][:3]

    weak_signals = (
        low_factors(eeat, "E-E-A-T")
        + low_factors(topical, "Topical")
        + low_factors(rag, "RAG")
    )
    missing_queries = [q["query"] for q in fan_out.get("queries", []) if q.get("coverage") in ("missing", "partial")][:6]
    weak_tech = [k for k, v in tech_scores.items() if v == 0][:5]

    prompt = f"""Jesteś starszym konsultantem AI SEO przygotowującym raport dla klienta. Dostałeś wyniki analiz specjalistycznych. Twoje zadanie: syntetyczny werdykt + priorytetyzowane rekomendacje.

<input>
<strona>{url}</strona>
<tytuł>{title}</tytuł>

<krytyczne_słabe_punkty_eeat_topical_rag>
{chr(10).join(f"- {s}" for s in weak_signals) or "(brak krytycznych)"}
</krytyczne_słabe_punkty_eeat_topical_rag>

<brakujące_lub_częściowe_odpowiedzi_na_zapytania>
{chr(10).join(f"- {q}" for q in missing_queries) or "(brak)"}
</brakujące_lub_częściowe_odpowiedzi_na_zapytania>

<krytyczne_braki_techniczne>
{", ".join(weak_tech) or "(brak)"}
</krytyczne_braki_techniczne>
</input>

<zasady>
- "top_recommendations": 5 działań UPORZĄDKOWANYCH wg IMPACT × EASE (najpierw szybkie wygrane z wysokim wpływem).
- Każda rekomendacja: konkretne działanie, nie ogólnik. Np. "Dodaj sekcję FAQ z 8 pytaniami dopasowanymi do intencji użytkowników" a NIE "Popraw strukturę".
- "content_gaps": 5 konkretnych luk tematycznych (co strona powinna pokryć a nie pokrywa).
- "overall_assessment": 2-3 zdania, werdykt + 1 największa mocna strona + 1 największy bloker.
- Odniesienia do konkretnych słabych punktów powyżej.
</zasady>

Zwróć TYLKO JSON po polsku:
{{
  "top_recommendations": ["Priorytet 1: ...", "Priorytet 2: ...", "Priorytet 3: ...", "Priorytet 4: ...", "Priorytet 5: ..."],
  "content_gaps": ["...", "...", "...", "...", "..."],
  "overall_assessment": "..."
}}"""
    return _extract_json(_gemini_call(prompt, temperature=0.3, max_tokens=2000))


# --- SCORING ---

def category_score(factors: dict) -> int:
    if not factors:
        return 0
    valid = [v for v in factors.values() if isinstance(v, dict) and "score" in v]
    if not valid:
        return 0
    total = sum(v.get("score", 0) for v in valid)
    return round((total / (len(valid) * 2)) * 100)


def tech_score_pct(tech_scores: dict) -> int:
    if not tech_scores:
        return 0
    return round((sum(tech_scores.values()) / (len(tech_scores) * 2)) * 100)


def fan_out_score(fan_out: dict) -> int:
    queries = fan_out.get("queries", [])
    if not queries:
        return 0
    pts = sum({"covered": 2, "partial": 1, "missing": 0}.get(q.get("coverage", "missing"), 0) for q in queries)
    return round(pts / (len(queries) * 2) * 100)


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
        yield event("progress", {"message": f"Znaleziono {len(sitemap_urls)} URL-i ({discovery_source}). Gemini wybiera reprezentację...", "pct": 12})

        # 2. Gemini URL selection
        selected = select_representative_urls(sitemap_urls, url, base_url) if sitemap_urls else []
        audit_urls = [url] + [u for u in selected if u != url][: MAX_AUDIT_PAGES - 1]
        yield event("progress", {"message": f"Scrapowanie {len(audit_urls)} podstron (Firecrawl, parallel)...", "pct": 18})

        # 3. Homepage full + subpages markdown-only
        homepage = scrape_with_firecrawl(url, full=True)
        sub_urls = [u for u in audit_urls if u != url]
        subs = scrape_pages_parallel(sub_urls) if sub_urls else {}

        meta = homepage.get("metadata", {})
        title = meta.get("title", "")
        meta_desc = meta.get("description", "")
        raw_html = homepage.get("rawHtml", "")
        html = homepage.get("html", "")

        parts = [f"=== STRONA GŁÓWNA: {url} ===\n{homepage.get('markdown', '')}"]
        crawled = [url]
        for u in sub_urls:
            md = (subs.get(u) or {}).get("markdown", "")
            if md:
                parts.append(f"=== PODSTRONA ({u}) ===\n{md}")
                crawled.append(u)
        combined_md = "\n\n".join(parts)
        content = combined_md[:15000]

        yield event("progress", {"message": f"Scrapowano {len(crawled)} podstron. Analiza techniczna...", "pct": 30})

        # 4. Tech checks
        robots = check_robots_txt(base_url)
        sitemap = check_sitemap(base_url)
        llms = check_llms_txt(base_url)
        html_checks = analyze_html_bs4(html, url, raw_html)
        tech_scores = build_tech_scores(robots, sitemap, llms, html_checks)

        yield event("progress", {"message": "Analiza E-E-A-T, Topical Authority, RAG + query fan-out (równolegle, Gemini)...", "pct": 50})

        # 5. Parallel Gemini calls
        with ThreadPoolExecutor(max_workers=4) as ex:
            f_eeat = ex.submit(analyze_eeat, url, title, meta_desc, content)
            f_top = ex.submit(analyze_topical, url, title, meta_desc, content)
            f_rag = ex.submit(analyze_rag, url, title, meta_desc, content)
            f_fan = ex.submit(generate_fan_out, url, title, content)
            eeat = f_eeat.result()
            topical = f_top.result()
            rag = f_rag.result()
            fan_out = f_fan.result()

        yield event("progress", {"message": "Synteza: rekomendacje + content gaps...", "pct": 85})
        synth = synthesize_findings(eeat, topical, rag, fan_out, tech_scores, url, title)

        yield event("progress", {"message": "Obliczanie wyników...", "pct": 95})
        s_eeat = category_score(eeat)
        s_top = category_score(topical)
        s_rag = category_score(rag)
        s_fan = fan_out_score(fan_out)
        s_tech = tech_score_pct(tech_scores)
        overall = round((s_eeat + s_top + s_rag + s_fan + s_tech) / 5)

        result = {
            "url": url,
            "crawled_urls": crawled,
            "discovery_source": discovery_source,
            "title": title,
            "meta_desc": meta_desc,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "scores": {
                "overall": overall, "eeat": s_eeat, "topical_authority": s_top,
                "rag_extractability": s_rag, "fan_out": s_fan, "technical": s_tech,
            },
            "eeat": eeat,
            "topical_authority": topical,
            "rag_extractability": rag,
            "fan_out": fan_out,
            "synthesis": synth,
            "tech_scores": tech_scores,
            "robots": {k: v for k, v in robots.items() if k != "raw"},
            "sitemap": sitemap,
            "llms_txt": llms,
            "html_checks": html_checks,
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
