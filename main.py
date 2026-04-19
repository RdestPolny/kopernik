#!/usr/bin/env python3
"""AI SEO Audit — FastAPI web application"""

import json
import os
import re
from datetime import datetime
from urllib.parse import urlparse, urljoin

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

FIRECRAWL_KEY = os.environ["FIRECRAWL_KEY"]
GEMINI_KEY = os.environ["GEMINI_KEY"]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
FIRECRAWL_URL = "https://api.firecrawl.dev/v1/scrape"

AI_BOTS = ["GPTBot", "PerplexityBot", "OAI-SearchBot", "ClaudeBot", "anthropic-ai", "Google-Extended"]

app = FastAPI(title="AI SEO Audit")
app.mount("/static", StaticFiles(directory="static"), name="static")


class AuditRequest(BaseModel):
    url: str


# --- SCRAPING ---

def scrape_with_firecrawl(url: str) -> dict:
    resp = requests.post(
        FIRECRAWL_URL,
        headers={"Authorization": f"Bearer {FIRECRAWL_KEY}", "Content-Type": "application/json"},
        json={"url": url, "formats": ["markdown", "html", "rawHtml", "links"], "onlyMainContent": False},
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise ValueError(f"Firecrawl error: {data}")
    return data.get("data", {})


def _classify_links(base_url: str, links: list) -> list[str]:
    """Pick up to 4 additional pages: service, about, contact, article."""
    parsed = urlparse(base_url)
    domain = parsed.netloc
    service_kw = ["/uslugi", "/oferta", "/services", "/service", "/produkty", "/produkt", "/cennik"]
    about_kw = ["/o-nas", "/about", "/o-firmie", "/kim-jestesmy", "/o-mnie", "/zespol", "/team"]
    contact_kw = ["/kontakt", "/contact"]
    article_kw = ["/blog/", "/artykul", "/article/", "/post/", "/news/", "/poradnik", "/wiedza"]
    buckets: dict[str, list] = {"article": [], "service": [], "about": [], "contact": []}
    for link in (links or []):
        if not link:
            continue
        lp = urlparse(link)
        if lp.netloc and lp.netloc != domain:
            continue
        path = lp.path.lower()
        if any(k in path for k in article_kw) and path.count("/") >= 2:
            buckets["article"].append(link)
        elif any(k in path for k in contact_kw):
            buckets["contact"].append(link)
        elif any(k in path for k in about_kw):
            buckets["about"].append(link)
        elif any(k in path for k in service_kw):
            buckets["service"].append(link)
    selected = []
    for cat in ["service", "about", "contact", "article"]:
        if buckets[cat]:
            selected.append(buckets[cat][0])
    return selected[:4]


def crawl_domain_pages(url: str) -> tuple[dict, str, list[str]]:
    """Scrape homepage + up to 4 classified subpages. Returns (homepage_data, combined_md, crawled_urls)."""
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    homepage = scrape_with_firecrawl(url)
    additional_urls = _classify_links(base_url, homepage.get("links", []))
    crawled = [url]
    parts = [f"=== STRONA GŁÓWNA: {url} ===\n{homepage.get('markdown', '')}"]
    for extra in additional_urls:
        try:
            data = scrape_with_firecrawl(extra)
            md = data.get("markdown", "")
            if md:
                parts.append(f"=== PODSTRONA ({extra}) ===\n{md}")
                crawled.append(extra)
        except Exception:
            pass
    return homepage, "\n\n".join(parts), crawled


# --- TECHNICAL CHECKS ---

def check_robots_txt(base_url: str) -> dict:
    result = {"accessible": False, "bots": {}, "sitemap_in_robots": False, "crawl_delay": None}
    try:
        resp = requests.get(urljoin(base_url, "/robots.txt"), timeout=15)
        if resp.status_code == 200:
            result["accessible"] = True
            text = resp.text
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
    lines = robots_text.splitlines()
    applies = False
    disallowed_root = False
    mentioned = False
    for line in lines:
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
            resp = requests.get(urljoin(base_url, path), timeout=15, allow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 100:
                return {"exists": True, "url": urljoin(base_url, path), "size_kb": round(len(resp.content) / 1024, 1)}
        except Exception:
            pass
    return {"exists": False}


def analyze_html(html: str, url: str, raw_html: str = "") -> dict:
    if not html and not raw_html:
        return {}
    # Use rawHtml for meta/schema (has full <head>), fall back to html
    full = raw_html if raw_html else html
    body = html if html else raw_html
    sem = {
        "article": bool(re.search(r"<article[\s>]", body, re.I)),
        "main": bool(re.search(r"<main[\s>]", body, re.I)),
        "section": bool(re.search(r"<section[\s>]", body, re.I)),
    }
    h1 = len(re.findall(r"<h1[\s>]", body, re.I))
    h2 = len(re.findall(r"<h2[\s>]", body, re.I))
    h3 = len(re.findall(r"<h3[\s>]", body, re.I))
    meta = {
        "description": bool(re.search(r'<meta\s[^>]*name=["\']description["\']', full, re.I)),
        "og_title": bool(re.search(r'<meta\s[^>]*property=["\']og:title["\']', full, re.I)),
        "og_description": bool(re.search(r'<meta\s[^>]*property=["\']og:description["\']', full, re.I)),
        "og_image": bool(re.search(r'<meta\s[^>]*property=["\']og:image["\']', full, re.I)),
        "canonical": bool(re.search(r'<link\s[^>]*rel=["\']canonical["\']', full, re.I)),
    }
    schema = {
        "any": bool(re.search(r"application/ld\+json", full, re.I)),
        "faq": bool(re.search(r'"FAQPage"', full)),
        "article": bool(re.search(r'"(Article|NewsArticle|BlogPosting)"', full)),
        "breadcrumb": bool(re.search(r'"BreadcrumbList"', full)),
        "organization": bool(re.search(r'"Organization"', full)),
    }
    return {
        "semantic_html5": sem,
        "headings": {"h1_count": h1, "h1_single": h1 == 1, "h2_count": h2, "h3_count": h3, "hierarchy_ok": h2 > 0},
        "meta": meta,
        "schema": schema,
        "https": url.startswith("https://"),
        "html_size_kb": round(len(html.encode("utf-8")) / 1024, 1),
    }


def build_tech_scores(robots: dict, sitemap: dict, html_checks: dict) -> dict:
    s = {}
    s["robots_txt_accessible"] = 2 if robots.get("accessible") else 0
    s["gptbot_not_blocked"] = 2 if robots.get("bots", {}).get("GPTBot", {}).get("allowed", True) else 0
    s["perplexitybot_not_blocked"] = 2 if robots.get("bots", {}).get("PerplexityBot", {}).get("allowed", True) else 0
    s["claudebot_not_blocked"] = 2 if robots.get("bots", {}).get("ClaudeBot", {}).get("allowed", True) else 0
    delay = robots.get("crawl_delay")
    s["crawl_delay_ok"] = 2 if delay is None or delay < 10 else (1 if delay < 30 else 0)
    s["sitemap_present"] = 2 if sitemap.get("exists") else 0
    s["https_enabled"] = 2 if html_checks.get("https") else 0
    sem = html_checks.get("semantic_html5", {})
    s["semantic_html5_tags"] = 2 if (sem.get("article") or sem.get("main")) else (1 if sem.get("section") else 0)
    heads = html_checks.get("headings", {})
    s["h1_single"] = 2 if heads.get("h1_single") else (1 if heads.get("h1_count", 0) > 0 else 0)
    s["heading_hierarchy_h2"] = 2 if heads.get("hierarchy_ok") else 0
    meta = html_checks.get("meta", {})
    s["meta_description"] = 2 if meta.get("description") else 0
    s["og_tags"] = 2 if (meta.get("og_title") and meta.get("og_description")) else (1 if meta.get("og_title") else 0)
    s["canonical_tag"] = 2 if meta.get("canonical") else 0
    schema = html_checks.get("schema", {})
    s["any_schema_markup"] = 2 if schema.get("any") else 0
    s["faq_schema"] = 2 if schema.get("faq") else 0
    s["article_schema"] = 2 if schema.get("article") else 0
    s["breadcrumb_schema"] = 2 if schema.get("breadcrumb") else 0
    size_kb = html_checks.get("html_size_kb", 0)
    s["response_size_ok"] = 0 if size_kb > 500 else (1 if size_kb > 200 else 2)
    return s


# --- GEMINI ANALYSIS ---

GEMINI_PROMPT = """Jesteś ekspertem AI SEO audytorem specjalizującym się w optymalizacji treści dla crawlerów LLM (GPTBot, PerplexityBot, ClaudeBot), systemów RAG i algorytmów cytowania AI używanych przez ChatGPT, Perplexity AI i podobne systemy.

WAŻNE: Wszystkie wartości tekstowe w odpowiedzi JSON (pola "note", "overall_assessment", elementy "content_gaps" i "top_recommendations") MUSZĄ być napisane w języku polskim.

Oceń na podstawie:
- E-E-A-T z modelem REAL (Relevant, Evidence, Accessible, Legitimate)
- Topical Authority i architektura klastrów/filarów treści
- RAG Extractability — jak łatwo LLM może wyodrębnić i cytować fakty
- Query fan-out coverage — czy treść odpowiada na wiele powiązanych podzapytań?

Oceń każdy czynnik: 0=brak/słaby, 1=częściowy, 2=dobry/obecny. Bądź krytyczny i obiektywny.
Analizujesz treść z WIELU PODSTRON tej samej domeny — oceniaj domenę jako całość.

GŁÓWNY URL: {url}
TYTUŁ STRONY: {title}
META DESCRIPTION: {meta_desc}
ŁĄCZNA LICZBA SŁÓW: {word_count}

TREŚĆ STRON (markdown, wiele podstron):
---
{content}
---

Zwróć TYLKO poprawny JSON (bez markdown, bez wyjaśnień):
{{
  "eeat": {{
    "author_bio_present": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "author_credentials_stated": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "publication_date_visible": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "last_updated_date_visible": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "external_authoritative_citations": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "firsthand_experience_signals": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "unique_data_or_original_statistics": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "sources_cited_inline": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "about_or_contact_page_linked": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "content_not_generic_ai_fluff": {{"score": 0, "note": "konkretna obserwacja po polsku"}}
  }},
  "topical_authority": {{
    "single_clear_topic_focus": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "internal_links_to_related_content": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "pillar_or_cluster_page_structure_signals": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "content_depth_comprehensive": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "multiple_subtopics_via_h2_sections": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "direct_definitions_or_answers_present": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "fan_out_query_coverage": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "content_freshness_or_timeliness_signals": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "unique_angle_or_original_pov": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "clear_user_value_proposition": {{"score": 0, "note": "konkretna obserwacja po polsku"}}
  }},
  "rag_extractability": {{
    "headings_formatted_as_questions": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "faq_section_present": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "direct_answer_near_content_start": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "numbered_lists_or_bullets_for_steps": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "table_of_contents_present": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "key_facts_or_stats_scannable": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "summary_or_tldr_section": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "concise_extractable_definitions": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "data_tables_present": {{"score": 0, "note": "konkretna obserwacja po polsku"}},
    "overall_scannable_structure": {{"score": 0, "note": "konkretna obserwacja po polsku"}}
  }},
  "content_gaps": [
    "Konkretny brakujący temat lub kąt widzenia po polsku",
    "Konkretny brakujący temat lub kąt widzenia po polsku",
    "Konkretny brakujący temat lub kąt widzenia po polsku",
    "Konkretny brakujący temat lub kąt widzenia po polsku"
  ],
  "top_recommendations": [
    "Priorytet 1 (największy wpływ): konkretne działanie po polsku",
    "Priorytet 2: konkretne działanie po polsku",
    "Priorytet 3: konkretne działanie po polsku",
    "Priorytet 4: konkretne działanie po polsku",
    "Priorytet 5: konkretne działanie po polsku"
  ],
  "overall_assessment": "Ocena 2-3 zdania po polsku: gotowość AI, główne mocne strony i krytyczne blokery cytowania przez LLM."
}}"""


def analyze_with_gemini(url: str, markdown: str, title: str, meta_desc: str) -> dict:
    content = markdown[:12000] if len(markdown) > 12000 else markdown
    prompt = GEMINI_PROMPT.format(
        url=url, title=title, meta_desc=meta_desc,
        word_count=len(markdown.split()), content=content,
    )
    resp = requests.post(
        GEMINI_URL,
        params={"key": GEMINI_KEY},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096},
        },
        timeout=90,
    )
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    return json.loads(text)


# --- SCORING ---

def category_score(factors: dict) -> int:
    if not factors:
        return 0
    total = sum(v.get("score", 0) for v in factors.values() if isinstance(v, dict))
    return round((total / (len(factors) * 2)) * 100)


def tech_score_pct(tech_scores: dict) -> int:
    if not tech_scores:
        return 0
    return round((sum(tech_scores.values()) / (len(tech_scores) * 2)) * 100)


# --- SSE AUDIT STREAM ---

def audit_stream(url: str):
    def event(step: str, data: dict):
        return f"data: {json.dumps({'step': step, **data})}\n\n"

    try:
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        yield event("progress", {"message": "Wykrywanie i scrapowanie podstron domeny (max 5)...", "pct": 10})
        homepage, combined_markdown, crawled_urls = crawl_domain_pages(url)
        meta = homepage.get("metadata", {})
        title = meta.get("title", "")
        meta_desc = meta.get("description", "")
        raw_html = homepage.get("rawHtml", "")
        html = homepage.get("html", "")
        yield event("progress", {"message": f"Scrapowano {len(crawled_urls)} podstron: {', '.join(crawled_urls)}", "pct": 30})

        yield event("progress", {"message": "Analiza techniczna (robots.txt, sitemap, HTML)...", "pct": 40})
        robots = check_robots_txt(base_url)
        sitemap = check_sitemap(base_url)
        html_checks = analyze_html(html, url, raw_html)
        tech_scores = build_tech_scores(robots, sitemap, html_checks)

        yield event("progress", {"message": f"Analiza treści przez Gemini ({GEMINI_MODEL})...", "pct": 60})
        gemini = analyze_with_gemini(url, combined_markdown, title, meta_desc)

        yield event("progress", {"message": "Obliczanie wyników...", "pct": 90})
        eeat = category_score(gemini.get("eeat", {}))
        topical = category_score(gemini.get("topical_authority", {}))
        rag = category_score(gemini.get("rag_extractability", {}))
        tech = tech_score_pct(tech_scores)
        overall = round((eeat + topical + rag + tech) / 4)

        result = {
            "url": url,
            "crawled_urls": crawled_urls,
            "title": title,
            "meta_desc": meta_desc,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "scores": {"overall": overall, "eeat": eeat, "topical_authority": topical, "rag_extractability": rag, "technical": tech},
            "gemini": gemini,
            "tech_scores": tech_scores,
            "robots": {k: v for k, v in robots.items() if k != "raw"},
            "sitemap": sitemap,
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
    return StreamingResponse(audit_stream(url), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/health")
async def health():
    return {"status": "ok"}
