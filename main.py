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

FIRECRAWL_KEY = os.getenv("FIRECRAWL_KEY", "fc-c8d4232ea9ce4562821cc5f29723bde3")
GEMINI_KEY = os.getenv("GEMINI_KEY", "AIzaSyBeRdBiiG9cVE-mzRoxY7-X4VdrXcMEKPg")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
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
        json={"url": url, "formats": ["markdown", "html"], "onlyMainContent": False},
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise ValueError(f"Firecrawl error: {data}")
    return data.get("data", {})


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


def analyze_html(html: str, url: str) -> dict:
    if not html:
        return {}
    sem = {
        "article": bool(re.search(r"<article[\s>]", html, re.I)),
        "main": bool(re.search(r"<main[\s>]", html, re.I)),
        "section": bool(re.search(r"<section[\s>]", html, re.I)),
    }
    h1 = len(re.findall(r"<h1[\s>]", html, re.I))
    h2 = len(re.findall(r"<h2[\s>]", html, re.I))
    h3 = len(re.findall(r"<h3[\s>]", html, re.I))
    meta = {
        "description": bool(re.search(r'<meta\s[^>]*name=["\']description["\']', html, re.I)),
        "og_title": bool(re.search(r'<meta\s[^>]*property=["\']og:title["\']', html, re.I)),
        "og_description": bool(re.search(r'<meta\s[^>]*property=["\']og:description["\']', html, re.I)),
        "og_image": bool(re.search(r'<meta\s[^>]*property=["\']og:image["\']', html, re.I)),
        "canonical": bool(re.search(r'<link\s[^>]*rel=["\']canonical["\']', html, re.I)),
    }
    schema = {
        "any": bool(re.search(r"application/ld\+json", html, re.I)),
        "faq": bool(re.search(r'"FAQPage"', html)),
        "article": bool(re.search(r'"(Article|NewsArticle|BlogPosting)"', html)),
        "breadcrumb": bool(re.search(r'"BreadcrumbList"', html)),
        "organization": bool(re.search(r'"Organization"', html)),
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

GEMINI_PROMPT = """You are an expert AI SEO auditor specializing in optimizing content for LLM crawlers (GPTBot, PerplexityBot, ClaudeBot), RAG retrieval systems, and AI citation algorithms used by ChatGPT, Perplexity AI, and similar systems.

Evaluate based on:
- E-E-A-T with REAL model (Relevant, Evidence, Accessible, Legitimate)
- Topical Authority and cluster/pillar content architecture
- RAG Extractability — how easily LLMs can extract and cite facts
- Query fan-out coverage — does content answer multiple related sub-questions?

Score each factor: 0=absent/poor, 1=partial, 2=good/present. Be critical and objective.

PAGE URL: {url}
PAGE TITLE: {title}
META DESCRIPTION: {meta_desc}
WORD COUNT: {word_count}

SCRAPED CONTENT (markdown):
---
{content}
---

Return ONLY valid JSON (no markdown, no explanation):
{{
  "eeat": {{
    "author_bio_present": {{"score": 0, "note": "specific observation"}},
    "author_credentials_stated": {{"score": 0, "note": "specific observation"}},
    "publication_date_visible": {{"score": 0, "note": "specific observation"}},
    "last_updated_date_visible": {{"score": 0, "note": "specific observation"}},
    "external_authoritative_citations": {{"score": 0, "note": "specific observation"}},
    "firsthand_experience_signals": {{"score": 0, "note": "specific observation"}},
    "unique_data_or_original_statistics": {{"score": 0, "note": "specific observation"}},
    "sources_cited_inline": {{"score": 0, "note": "specific observation"}},
    "about_or_contact_page_linked": {{"score": 0, "note": "specific observation"}},
    "content_not_generic_ai_fluff": {{"score": 0, "note": "specific observation"}}
  }},
  "topical_authority": {{
    "single_clear_topic_focus": {{"score": 0, "note": "specific observation"}},
    "internal_links_to_related_content": {{"score": 0, "note": "specific observation"}},
    "pillar_or_cluster_page_structure_signals": {{"score": 0, "note": "specific observation"}},
    "content_depth_comprehensive": {{"score": 0, "note": "specific observation"}},
    "multiple_subtopics_via_h2_sections": {{"score": 0, "note": "specific observation"}},
    "direct_definitions_or_answers_present": {{"score": 0, "note": "specific observation"}},
    "fan_out_query_coverage": {{"score": 0, "note": "specific observation"}},
    "content_freshness_or_timeliness_signals": {{"score": 0, "note": "specific observation"}},
    "unique_angle_or_original_pov": {{"score": 0, "note": "specific observation"}},
    "clear_user_value_proposition": {{"score": 0, "note": "specific observation"}}
  }},
  "rag_extractability": {{
    "headings_formatted_as_questions": {{"score": 0, "note": "specific observation"}},
    "faq_section_present": {{"score": 0, "note": "specific observation"}},
    "direct_answer_near_content_start": {{"score": 0, "note": "specific observation"}},
    "numbered_lists_or_bullets_for_steps": {{"score": 0, "note": "specific observation"}},
    "table_of_contents_present": {{"score": 0, "note": "specific observation"}},
    "key_facts_or_stats_scannable": {{"score": 0, "note": "specific observation"}},
    "summary_or_tldr_section": {{"score": 0, "note": "specific observation"}},
    "concise_extractable_definitions": {{"score": 0, "note": "specific observation"}},
    "data_tables_present": {{"score": 0, "note": "specific observation"}},
    "overall_scannable_structure": {{"score": 0, "note": "specific observation"}}
  }},
  "content_gaps": [
    "Specific missing topic or angle",
    "Specific missing topic or angle",
    "Specific missing topic or angle",
    "Specific missing topic or angle"
  ],
  "top_recommendations": [
    "Priority 1 (highest impact): concrete action",
    "Priority 2: concrete action",
    "Priority 3: concrete action",
    "Priority 4: concrete action",
    "Priority 5: concrete action"
  ],
  "overall_assessment": "2-3 sentence evaluation of AI-readiness with main strengths and critical blockers."
}}"""


def analyze_with_gemini(url: str, markdown: str, title: str, meta_desc: str) -> dict:
    content = markdown[:9000] if len(markdown) > 9000 else markdown
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

        yield event("progress", {"message": "Scrapowanie strony przez Firecrawl...", "pct": 10})
        scraped = scrape_with_firecrawl(url)
        markdown = scraped.get("markdown", "")
        html = scraped.get("html", "")
        meta = scraped.get("metadata", {})
        title = meta.get("title", "")
        meta_desc = meta.get("description", "")

        yield event("progress", {"message": "Analiza techniczna (robots.txt, sitemap, HTML)...", "pct": 35})
        robots = check_robots_txt(base_url)
        sitemap = check_sitemap(base_url)
        html_checks = analyze_html(html, url)
        tech_scores = build_tech_scores(robots, sitemap, html_checks)

        yield event("progress", {"message": f"Analiza treści przez Gemini ({GEMINI_MODEL})...", "pct": 60})
        gemini = analyze_with_gemini(url, markdown, title, meta_desc)

        yield event("progress", {"message": "Obliczanie wyników...", "pct": 90})
        eeat = category_score(gemini.get("eeat", {}))
        topical = category_score(gemini.get("topical_authority", {}))
        rag = category_score(gemini.get("rag_extractability", {}))
        tech = tech_score_pct(tech_scores)
        overall = round((eeat + topical + rag + tech) / 4)

        result = {
            "url": url,
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
