#!/usr/bin/env python3
"""
AI SEO Audit Tool
Audits website AI-readiness: E-E-A-T, Topical Authority, RAG Extractability, Technical bot access
Usage: python audit.py <URL> [--output report.html]
"""

import sys
import json
import re
import argparse
import requests
from datetime import datetime
from urllib.parse import urlparse, urljoin

# --- CONFIG ---
FIRECRAWL_KEY = "fc-c8d4232ea9ce4562821cc5f29723bde3"
GEMINI_KEY = "AIzaSyBeRdBiiG9cVE-mzRoxY7-X4VdrXcMEKPg"
GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
FIRECRAWL_URL = "https://api.firecrawl.dev/v1/scrape"

AI_BOTS = ["GPTBot", "PerplexityBot", "OAI-SearchBot", "ClaudeBot", "anthropic-ai", "Google-Extended"]


# --- SCRAPING ---

def scrape_with_firecrawl(url):
    print(f"  → Firecrawl: {url}")
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

def check_robots_txt(base_url):
    results = {"accessible": False, "bots": {}, "sitemap_in_robots": False, "crawl_delay": None, "raw": ""}
    try:
        resp = requests.get(urljoin(base_url, "/robots.txt"), timeout=15)
        if resp.status_code == 200:
            results["accessible"] = True
            results["raw"] = resp.text
            text = resp.text
            text_lower = text.lower()

            if "sitemap:" in text_lower:
                results["sitemap_in_robots"] = True

            delay_match = re.search(r"(?i)crawl-delay:\s*(\d+)", text)
            if delay_match:
                results["crawl_delay"] = int(delay_match.group(1))

            for bot in AI_BOTS:
                results["bots"][bot] = _parse_bot_access(text, bot)
    except Exception as e:
        results["error"] = str(e)
    return results


def _parse_bot_access(robots_text, bot_name):
    """Check if a specific bot is allowed/disallowed in robots.txt"""
    bot_lower = bot_name.lower()
    lines = robots_text.splitlines()
    current_agents = []
    applies = False
    disallowed_root = False

    for line in lines:
        line = line.strip()
        if line.lower().startswith("user-agent:"):
            agent = line.split(":", 1)[1].strip().lower()
            current_agents = [agent]
            applies = bot_lower in agent or agent == "*"
        elif line.lower().startswith("disallow:") and applies:
            path = line.split(":", 1)[1].strip()
            if path in ("/", "/*", ""):
                if path in ("/", "/*"):
                    disallowed_root = True
        elif line == "":
            current_agents = []
            applies = False

    mentioned = any(bot_lower in a for a in [
        line.split(":", 1)[1].strip().lower()
        for line in robots_text.splitlines()
        if line.lower().startswith("user-agent:")
    ])
    return {"mentioned": mentioned, "allowed": not disallowed_root}


def check_sitemap(base_url):
    for path in ["/sitemap.xml", "/sitemap_index.xml"]:
        try:
            resp = requests.get(urljoin(base_url, path), timeout=15, allow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 100:
                return {"exists": True, "url": urljoin(base_url, path), "size_kb": round(len(resp.content) / 1024, 1)}
        except Exception:
            pass
    return {"exists": False}


def analyze_html(html, url):
    checks = {}
    if not html:
        return checks

    checks["semantic_html5"] = {
        "article": bool(re.search(r"<article[\s>]", html, re.I)),
        "main": bool(re.search(r"<main[\s>]", html, re.I)),
        "section": bool(re.search(r"<section[\s>]", html, re.I)),
    }
    h1 = len(re.findall(r"<h1[\s>]", html, re.I))
    h2 = len(re.findall(r"<h2[\s>]", html, re.I))
    h3 = len(re.findall(r"<h3[\s>]", html, re.I))
    checks["headings"] = {"h1_count": h1, "h1_single": h1 == 1, "h2_count": h2, "h3_count": h3, "hierarchy_ok": h2 > 0}
    checks["meta"] = {
        "description": bool(re.search(r'<meta\s[^>]*name=["\']description["\']', html, re.I)),
        "og_title": bool(re.search(r'<meta\s[^>]*property=["\']og:title["\']', html, re.I)),
        "og_description": bool(re.search(r'<meta\s[^>]*property=["\']og:description["\']', html, re.I)),
        "og_image": bool(re.search(r'<meta\s[^>]*property=["\']og:image["\']', html, re.I)),
        "canonical": bool(re.search(r'<link\s[^>]*rel=["\']canonical["\']', html, re.I)),
    }
    checks["schema"] = {
        "any": bool(re.search(r"application/ld\+json", html, re.I)),
        "faq": bool(re.search(r'"FAQPage"', html)),
        "article": bool(re.search(r'"(Article|NewsArticle|BlogPosting)"', html)),
        "breadcrumb": bool(re.search(r'"BreadcrumbList"', html)),
        "organization": bool(re.search(r'"Organization"', html)),
    }
    checks["https"] = url.startswith("https://")
    checks["html_size_kb"] = round(len(html.encode("utf-8")) / 1024, 1)
    return checks


def build_tech_scores(robots, sitemap, html_checks):
    s = {}
    s["robots_txt_accessible"] = 2 if robots.get("accessible") else 0
    s["gptbot_not_blocked"] = 2 if robots.get("bots", {}).get("GPTBot", {}).get("allowed", True) else 0
    s["perplexitybot_not_blocked"] = 2 if robots.get("bots", {}).get("PerplexityBot", {}).get("allowed", True) else 0
    s["claudebot_not_blocked"] = 2 if robots.get("bots", {}).get("ClaudeBot", {}).get("allowed", True) else 0

    delay = robots.get("crawl_delay")
    if delay is None:
        s["crawl_delay_ok"] = 2
    elif delay < 10:
        s["crawl_delay_ok"] = 2
    elif delay < 30:
        s["crawl_delay_ok"] = 1
    else:
        s["crawl_delay_ok"] = 0

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

GEMINI_PROMPT_TEMPLATE = """You are an expert AI SEO auditor specializing in optimizing content for LLM crawlers (GPTBot, PerplexityBot, ClaudeBot), RAG retrieval systems, and AI citation algorithms used by ChatGPT, Perplexity AI, and similar systems.

You are evaluating AI-readiness based on:
- E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness) with REAL model (Relevant, Evidence, Accessible, Legitimate)
- Topical Authority and cluster/pillar content architecture
- RAG Extractability — how easily LLMs can extract and cite facts from this page
- Query fan-out coverage — does content answer multiple related sub-questions?

Analyze the scraped webpage below. Be critical and objective. Score each factor 0-2:
  0 = absent or poor
  1 = partial / needs improvement
  2 = good / clearly present

PAGE URL: {url}
PAGE TITLE: {title}
META DESCRIPTION: {meta_desc}
WORD COUNT (approx): {word_count}

SCRAPED CONTENT (markdown):
---
{content}
---

Return ONLY valid JSON matching this structure exactly (no markdown, no explanation):
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
    "Specific missing topic or angle that AI searchers would expect",
    "Specific missing topic or angle that AI searchers would expect",
    "Specific missing topic or angle that AI searchers would expect",
    "Specific missing topic or angle that AI searchers would expect"
  ],
  "top_recommendations": [
    "Priority 1 (highest impact): concrete specific action",
    "Priority 2: concrete specific action",
    "Priority 3: concrete specific action",
    "Priority 4: concrete specific action",
    "Priority 5: concrete specific action"
  ],
  "overall_assessment": "2-3 sentence evaluation of AI-readiness. State current main strengths and critical blockers for LLM citation."
}}"""


def analyze_with_gemini(url, markdown, title, meta_desc):
    content = markdown[:9000] if len(markdown) > 9000 else markdown
    word_count = len(markdown.split())

    prompt = GEMINI_PROMPT_TEMPLATE.format(
        url=url,
        title=title,
        meta_desc=meta_desc,
        word_count=word_count,
        content=content,
    )

    resp = requests.post(
        GEMINI_URL,
        params={"key": GEMINI_KEY},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
            },
        },
        timeout=90,
    )
    resp.raise_for_status()
    result = resp.json()

    text = result["candidates"][0]["content"]["parts"][0]["text"]
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    return json.loads(text)


# --- SCORING ---

def category_score(factors):
    if not factors:
        return 0
    total = sum(v.get("score", 0) for v in factors.values() if isinstance(v, dict))
    max_total = len(factors) * 2
    return round((total / max_total) * 100) if max_total else 0


def tech_score_pct(tech_scores):
    if not tech_scores:
        return 0
    return round((sum(tech_scores.values()) / (len(tech_scores) * 2)) * 100)


# --- HTML REPORT ---

def score_color(s):
    if s >= 70:
        return "#16a34a"
    if s >= 45:
        return "#d97706"
    return "#dc2626"


def score_label(s):
    if s >= 70:
        return "Dobry"
    if s >= 45:
        return "Wymaga pracy"
    return "Krytyczny"


def factor_row(name, score, note=""):
    bg = {0: "#fef2f2", 1: "#fefce8", 2: "#f0fdf4"}.get(score, "#f9fafb")
    icon = {0: "✗", 1: "~", 2: "✓"}.get(score, "?")
    label_color = {0: "#dc2626", 1: "#b45309", 2: "#16a34a"}.get(score, "#6b7280")
    display_name = name.replace("_", " ").replace("-", " ").title()
    return f"""<tr style="background:{bg}">
      <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-size:0.9em">{display_name}</td>
      <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;text-align:center;font-weight:700;color:{label_color}">{icon}</td>
      <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#6b7280;font-size:0.85em">{note or "—"}</td>
    </tr>"""


def tech_factor_row(name, score):
    bg = {0: "#fef2f2", 1: "#fefce8", 2: "#f0fdf4"}.get(score, "#f9fafb")
    icon = {0: "✗", 1: "~", 2: "✓"}.get(score, "?")
    label_color = {0: "#dc2626", 1: "#b45309", 2: "#16a34a"}.get(score, "#6b7280")
    display_name = name.replace("_", " ").title()
    return f"""<tr style="background:{bg}">
      <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-size:0.9em">{display_name}</td>
      <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;text-align:center;font-weight:700;color:{label_color}">{icon}</td>
    </tr>"""


def generate_html_report(url, robots, sitemap, html_checks, tech_scores, gemini, timestamp):
    eeat_score = category_score(gemini.get("eeat", {}))
    topical_score = category_score(gemini.get("topical_authority", {}))
    rag_score = category_score(gemini.get("rag_extractability", {}))
    tech_score = tech_score_pct(tech_scores)
    overall = round((eeat_score + topical_score + rag_score + tech_score) / 4)

    eeat_rows = "".join(factor_row(k, v["score"], v.get("note", "")) for k, v in gemini.get("eeat", {}).items())
    topical_rows = "".join(factor_row(k, v["score"], v.get("note", "")) for k, v in gemini.get("topical_authority", {}).items())
    rag_rows = "".join(factor_row(k, v["score"], v.get("note", "")) for k, v in gemini.get("rag_extractability", {}).items())
    tech_rows = "".join(tech_factor_row(k, v) for k, v in tech_scores.items())

    gaps_html = "".join(f'<li style="margin:6px 0;color:#b91c1c">{g}</li>' for g in gemini.get("content_gaps", []))
    recs_html = "".join(f'<li style="margin:8px 0">{r}</li>' for r in gemini.get("top_recommendations", []))

    bots_html = ""
    for bot, info in robots.get("bots", {}).items():
        allowed = info.get("allowed", True)
        color = "#16a34a" if allowed else "#dc2626"
        status = "✓" if allowed else "✗"
        bots_html += f'<span style="margin:3px;padding:4px 10px;background:{color};color:white;border-radius:4px;font-size:0.82em;display:inline-block">{status} {bot}</span>'

    sitemap_info = f'✓ {sitemap.get("url", "")} ({sitemap.get("size_kb", "?")} KB)' if sitemap.get("exists") else "✗ Nie znaleziono"
    crawl_delay_info = f'{robots.get("crawl_delay")}s' if robots.get("crawl_delay") else "Nie ustawiono"

    def score_circle(score, label):
        color = score_color(score)
        status = score_label(score)
        return f"""<div class="score-card">
          <div class="score-label">{label}</div>
          <div class="score-num" style="color:{color}">{score}%</div>
          <div class="score-status" style="color:{color}">{status}</div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI SEO Audit — {url}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f1f5f9;color:#334155;line-height:1.6}}
.container{{max-width:1100px;margin:0 auto;padding:24px}}
.header{{background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);color:#fff;padding:36px;border-radius:14px;margin-bottom:24px}}
.header h1{{font-size:1.7em;font-weight:800;letter-spacing:-0.3px}}
.header .url{{color:#7dd3fc;font-size:0.9em;margin-top:6px;word-break:break-all}}
.header .ts{{color:#94a3b8;font-size:0.8em;margin-top:4px}}
.score-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:24px}}
.score-card{{background:#fff;border-radius:10px;padding:20px;text-align:center;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
.score-label{{font-size:0.78em;color:#64748b;text-transform:uppercase;letter-spacing:.4px;margin-bottom:8px}}
.score-num{{font-size:2.6em;font-weight:800;line-height:1}}
.score-status{{font-size:0.78em;margin-top:6px;font-weight:600}}
.card{{background:#fff;border-radius:10px;padding:24px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
.card h2{{font-size:1.1em;font-weight:700;color:#0f172a;margin-bottom:16px;display:flex;align-items:center;gap:8px}}
table{{width:100%;border-collapse:collapse}}
th{{background:#f8fafc;padding:9px 12px;text-align:left;font-size:0.78em;color:#64748b;text-transform:uppercase;letter-spacing:.3px;border-bottom:2px solid #e2e8f0}}
.assessment{{background:#f0f9ff;border-left:4px solid #0284c7;padding:14px 18px;border-radius:0 8px 8px 0;color:#0c4a6e;font-size:0.95em}}
ul,ol{{padding-left:22px}}
.gap-item{{margin:5px 0;color:#b91c1c}}
.rec-item{{margin:7px 0}}
.badge{{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:20px;font-size:0.8em;font-weight:600}}
.info-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px}}
.info-item{{background:#f8fafc;border-radius:6px;padding:10px 14px;font-size:0.88em}}
.info-item strong{{display:block;color:#64748b;font-size:0.82em;text-transform:uppercase;margin-bottom:3px}}
@media(max-width:600px){{.info-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="container">

<div class="header">
  <h1>🤖 AI SEO Audit</h1>
  <div class="url">{url}</div>
  <div class="ts">Wygenerowano: {timestamp}</div>
</div>

<div class="score-grid">
  {score_circle(overall, "Wynik Ogólny")}
  {score_circle(eeat_score, "E-E-A-T")}
  {score_circle(topical_score, "Topical Authority")}
  {score_circle(rag_score, "RAG Extractability")}
  {score_circle(tech_score, "Techniczny AI-Bot")}
</div>

<div class="card">
  <h2>💬 Ogólna Ocena AI-Readiness</h2>
  <div class="assessment">{gemini.get("overall_assessment", "—")}</div>
</div>

<div class="card">
  <h2>📋 E-E-A-T / Wiarygodność (Model REAL)</h2>
  <table>
    <tr><th>Czynnik</th><th style="text-align:center;width:60px">Status</th><th>Obserwacja</th></tr>
    {eeat_rows}
  </table>
</div>

<div class="card">
  <h2>🏛️ Topical Authority</h2>
  <table>
    <tr><th>Czynnik</th><th style="text-align:center;width:60px">Status</th><th>Obserwacja</th></tr>
    {topical_rows}
  </table>
</div>

<div class="card">
  <h2>⚡ RAG Extractability (Gotowość na Cytowania)</h2>
  <table>
    <tr><th>Czynnik</th><th style="text-align:center;width:60px">Status</th><th>Obserwacja</th></tr>
    {rag_rows}
  </table>
</div>

<div class="card">
  <h2>🤖 Dostępność dla Botów AI (Techniczny)</h2>

  <div class="info-grid">
    <div class="info-item"><strong>robots.txt</strong>{"✓ Dostępny" if robots.get("accessible") else "✗ Niedostępny"}</div>
    <div class="info-item"><strong>Crawl-Delay</strong>{crawl_delay_info}</div>
    <div class="info-item"><strong>Sitemap XML</strong>{sitemap_info}</div>
    <div class="info-item"><strong>Rozmiar HTML</strong>{html_checks.get("html_size_kb", "?")} KB</div>
  </div>

  <div style="margin-bottom:16px">
    <strong style="display:block;font-size:0.82em;color:#64748b;text-transform:uppercase;margin-bottom:6px">Status botów AI w robots.txt</strong>
    {bots_html or '<span style="color:#94a3b8;font-size:0.9em">Brak wpisów dla botów AI</span>'}
  </div>

  <table>
    <tr><th>Czynnik Techniczny</th><th style="text-align:center;width:60px">Status</th></tr>
    {tech_rows}
  </table>
</div>

<div class="card">
  <h2 style="color:#b91c1c">🔍 Luki w Treści (Content GAPs)</h2>
  <ul>{"".join(f'<li class="gap-item">{g}</li>' for g in gemini.get("content_gaps", []))}</ul>
</div>

<div class="card">
  <h2 style="color:#0369a1">💡 Top Rekomendacje (Priorytetowe)</h2>
  <ol>{"".join(f'<li class="rec-item">{r}</li>' for r in gemini.get("top_recommendations", []))}</ol>
</div>

<div style="text-align:center;padding:24px 0;color:#94a3b8;font-size:0.8em">
  Powered by Firecrawl + Gemini ({GEMINI_MODEL}) | AI SEO Audit Tool
</div>

</div>
</body>
</html>"""


# --- TERMINAL OUTPUT ---

def print_results(url, overall, eeat, topical, rag, tech):
    def bar(s):
        filled = round(s / 10)
        return "█" * filled + "░" * (10 - filled)

    def rating(s):
        if s >= 70: return "✓ Dobry"
        if s >= 45: return "~ Wymaga pracy"
        return "✗ Krytyczny"

    print("\n" + "═" * 62)
    print(f"  AI SEO AUDIT RESULTS")
    print(f"  {url}")
    print("═" * 62)
    print(f"  Ogólny wynik:      {overall:3d}%  {bar(overall)}  {rating(overall)}")
    print(f"  E-E-A-T:           {eeat:3d}%  {bar(eeat)}  {rating(eeat)}")
    print(f"  Topical Authority: {topical:3d}%  {bar(topical)}  {rating(topical)}")
    print(f"  RAG Extractability:{rag:3d}%  {bar(rag)}  {rating(rag)}")
    print(f"  Techniczny AI-Bot: {tech:3d}%  {bar(tech)}  {rating(tech)}")
    print("═" * 62)


# --- MAIN ---

def main():
    parser = argparse.ArgumentParser(description="AI SEO Audit — checks AI-readiness for LLM crawlers")
    parser.add_argument("url", help="URL strony do audytu")
    parser.add_argument("--output", "-o", default="audit_report.html", help="Plik HTML raportu (domyślnie: audit_report.html)")
    parser.add_argument("--json", "-j", action="store_true", help="Zapisz też dane JSON")
    args = parser.parse_args()

    url = args.url
    if not url.startswith("http"):
        url = "https://" + url

    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n[1/4] Scrapowanie przez Firecrawl...")
    scraped = scrape_with_firecrawl(url)
    markdown = scraped.get("markdown", "")
    html = scraped.get("html", "")
    meta = scraped.get("metadata", {})
    title = meta.get("title", "")
    meta_desc = meta.get("description", "")
    print(f"  → Pobrano {len(markdown)} znaków markdown, {len(html)} znaków HTML")

    print(f"\n[2/4] Sprawdzanie czynników technicznych...")
    robots = check_robots_txt(base_url)
    sitemap = check_sitemap(base_url)
    html_checks = analyze_html(html, url)
    tech_scores = build_tech_scores(robots, sitemap, html_checks)
    print(f"  → robots.txt: {'OK' if robots.get('accessible') else 'BRAK'} | sitemap: {'OK' if sitemap.get('exists') else 'BRAK'}")

    print(f"\n[3/4] Analiza treści przez Gemini ({GEMINI_MODEL})...")
    gemini = analyze_with_gemini(url, markdown, title, meta_desc)
    print(f"  → Analiza zakończona")

    print(f"\n[4/4] Generowanie raportu...")
    eeat = category_score(gemini.get("eeat", {}))
    topical = category_score(gemini.get("topical_authority", {}))
    rag = category_score(gemini.get("rag_extractability", {}))
    tech = tech_score_pct(tech_scores)
    overall = round((eeat + topical + rag + tech) / 4)

    html_report = generate_html_report(url, robots, sitemap, html_checks, tech_scores, gemini, timestamp)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_report)

    if args.json:
        json_path = args.output.replace(".html", ".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"url": url, "timestamp": timestamp, "scores": {
                "overall": overall, "eeat": eeat, "topical_authority": topical,
                "rag_extractability": rag, "technical": tech
            }, "gemini_analysis": gemini, "tech_checks": tech_scores,
            "robots": {k: v for k, v in robots.items() if k != "raw"}}, f, indent=2, ensure_ascii=False)
        print(f"  → JSON: {json_path}")

    print_results(url, overall, eeat, topical, rag, tech)
    print(f"\n  Raport HTML: {args.output}\n")


if __name__ == "__main__":
    main()
