#!/usr/bin/env python3
"""AI SEO Audit — page-type-aware per-URL factor sets."""

import base64
import gzip
import json
import logging
import os
import re
import smtplib
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.mime.text import MIMEText
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Load a local .env if present (keeps secrets out of the codebase). Optional dependency:
# the app still works when python-dotenv isn't installed and env vars are set directly.
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

FIRECRAWL_KEY = os.environ["FIRECRAWL_KEY"]
GEMINI_KEY = os.environ["GEMINI_KEY"]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
FIRECRAWL_SCRAPE = "https://api.firecrawl.dev/v1/scrape"
FIRECRAWL_MAP = "https://api.firecrawl.dev/v1/map"
PAGESPEED_KEY = os.getenv("PAGESPEED_KEY", "")
PERPLEXITY_KEY = os.getenv("PERPLEXITY_KEY", "")
GPT_KEY = os.getenv("GPT_KEY", "")
LEADS_TOKEN = os.getenv("LEADS_TOKEN", "")
FIRESTORE_PROJECT = os.getenv("FIRESTORE_PROJECT", "")
LEADS_EMAIL = os.getenv("LEADS_EMAIL", "")      # odbiorca powiadomień, np. marcin@...
SMTP_USER   = os.getenv("SMTP_USER", "")        # adres nadawcy / login SMTP
SMTP_PASS   = os.getenv("SMTP_PASS", "")        # hasło lub App Password
SMTP_HOST   = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587"))
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
_PSI_TOKEN_CACHE: dict = {"token": None, "exp": 0}

# In-memory lead storage (resets on restart; backed by Firestore/stdout for persistence)
_LEADS_MEMORY: list[dict] = []
_LEADS_LOCK = threading.Lock()

AI_BOTS = ["GPTBot", "PerplexityBot", "OAI-SearchBot", "ClaudeBot", "anthropic-ai", "Google-Extended"]
MAX_AUDIT_PAGES = 5
SITEMAP_CAP = 300
MAX_CONTENT_CHARS = 7000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATENT_FACTORS_PATH = os.path.join(BASE_DIR, "google-patent-seo-skill", "references", "factors.jsonl")
PATENT_SCORING_CONFIDENCE = {"high", "medium"}
# Opcjonalny cache danych "Widoczność w AI Overviews" (Senuto). Aplikacja nie woła
# konektora Senuto (MCP); operator zasila SENUTO_AIO_DIR/<domena>.json danymi z MCP,
# a audyt dołącza je do wyniku jako blok `senuto_aio` (jeśli plik istnieje).
SENUTO_AIO_DIR = os.getenv("SENUTO_AIO_DIR", os.path.join(BASE_DIR, "senuto_aio"))
# Senuto REST API (live AIO metrics). Credentials are supplied by the operator via
# environment variables — never hardcoded. Prefer a ready SENUTO_BEARER_TOKEN; otherwise
# the app logs in with SENUTO_EMAIL + SENUTO_PASSWORD and caches the 30-day token.
SENUTO_API_BASE = os.getenv("SENUTO_API_BASE", "https://api.senuto.com/api")
SENUTO_BEARER_TOKEN = os.getenv("SENUTO_BEARER_TOKEN", "")
SENUTO_EMAIL = os.getenv("SENUTO_EMAIL", "")
SENUTO_PASSWORD = os.getenv("SENUTO_PASSWORD", "")
SENUTO_COUNTRY_ID = os.getenv("SENUTO_COUNTRY_ID", "200")  # 200 = PL (Base 2.0)
SENUTO_LANG = os.getenv("SENUTO_LANG", "pl-PL")
SENUTO_COMPETITORS = int(os.getenv("SENUTO_COMPETITORS", "3"))  # ile konkurentów porównać w AIO (0 = wyłącz)
_SENUTO_TOKEN_CACHE: dict = {"token": None, "exp": 0.0}
_senuto_session = requests.Session()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("kopernik")


def _load_patent_factors() -> dict[str, dict]:
    """Load patent-derived SEO factors. Missing KB should not break the app."""
    factors: dict[str, dict] = {}
    if not os.path.exists(PATENT_FACTORS_PATH):
        return factors
    try:
        with open(PATENT_FACTORS_PATH, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                record = json.loads(line)
                factor_id = record.get("factor_id")
                if factor_id:
                    factors[factor_id] = record
    except Exception:
        return {}
    return factors


PATENT_FACTORS = _load_patent_factors()


def _senuto_host(url: str) -> str:
    """Normalize an audit URL to a bare hostname (no scheme, no www)."""
    try:
        host = urlparse(url if "//" in url else "https://" + url).hostname or ""
    except Exception:
        return ""
    return re.sub(r"^www\.", "", host.lower())


def load_senuto_aio_cache(url: str) -> dict | None:
    """Read an optional per-domain AIO cache file populated from MCP data.

    Operator writes SENUTO_AIO_DIR/<domain>.json (e.g. with TOP3/10/50 distribution).
    Returns None when the file is absent or invalid.
    """
    host = _senuto_host(url)
    if not host:
        return None
    path = os.path.join(SENUTO_AIO_DIR, host + ".json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _senuto_token() -> str | None:
    """Return a valid Senuto bearer token.

    Prefers SENUTO_BEARER_TOKEN. Otherwise logs in with SENUTO_EMAIL + SENUTO_PASSWORD
    via POST /users/token and caches the token (~30 days). Returns None if no
    credentials are configured or login fails.
    """
    if SENUTO_BEARER_TOKEN:
        return SENUTO_BEARER_TOKEN
    now = time.time()
    if _SENUTO_TOKEN_CACHE["token"] and _SENUTO_TOKEN_CACHE["exp"] > now:
        return _SENUTO_TOKEN_CACHE["token"]
    if not (SENUTO_EMAIL and SENUTO_PASSWORD):
        return None
    try:
        r = _senuto_session.post(
            f"{SENUTO_API_BASE}/users/token",
            headers={"Content-Type": "application/json", "Lang": SENUTO_LANG},
            json={"email": SENUTO_EMAIL, "password": SENUTO_PASSWORD},
            timeout=20,
        )
        r.raise_for_status()
        token = ((r.json() or {}).get("data") or {}).get("token")
        if token:
            _SENUTO_TOKEN_CACHE.update({"token": token, "exp": now + 29 * 86400})
            return token
        logger.warning("Senuto login: no token in response")
    except Exception as e:
        logger.warning("Senuto login failed: %s", e)
    return None


def _senuto_domain_stats(host: str, token: str) -> dict | None:
    """GET getDomainStatistics → {domain, aio_keywords, aio_visible_keywords, visibility} or None."""
    try:
        r = _senuto_session.get(
            f"{SENUTO_API_BASE}/visibility_analysis/reports/dashboard/getDomainStatistics",
            headers={"Authorization": f"Bearer {token}", "Lang": SENUTO_LANG},
            params={"domain": host, "fetch_mode": "topLevelDomain", "country_id": SENUTO_COUNTRY_ID},
            timeout=30,
        )
        r.raise_for_status()
        stats = ((r.json() or {}).get("data") or {}).get("statistics") or {}

        def _val(key):
            v = stats.get(key)
            return v.get("recent_value") if isinstance(v, dict) else v

        aio_kw = _val("aio_keywords")
        aio_vis = _val("aio_visible_keywords")
        if aio_kw is None and aio_vis is None:
            return None
        return {
            "domain": host,
            "aio_keywords": aio_kw,
            "aio_visible_keywords": aio_vis,
            "visibility": _val("visibility"),
        }
    except Exception as e:
        logger.warning("Senuto getDomainStatistics failed for %s: %s", host, e)
        return None


def _senuto_competitor_domains(host: str, token: str, limit: int) -> list:
    """POST competitors/getData → up to `limit` competitor domains (excl. main), by common_keywords."""
    if limit <= 0:
        return []
    try:
        r = _senuto_session.post(
            f"{SENUTO_API_BASE}/visibility_analysis/reports/competitors/getData",
            headers={"Authorization": f"Bearer {token}", "Lang": SENUTO_LANG},
            data={"domain": host, "fetch_mode": "topLevelDomain", "country_id": SENUTO_COUNTRY_ID, "limit": 20},
            timeout=30,
        )
        r.raise_for_status()
        rows = (r.json() or {}).get("data") or []
        comps = []
        for row in rows:
            if not isinstance(row, dict) or row.get("is_main_domain"):
                continue
            dom = row.get("domain")
            if dom:
                comps.append((dom, row.get("common_keywords") or 0))
        comps.sort(key=lambda x: x[1], reverse=True)
        return [dom for dom, _ in comps[:limit]]
    except Exception as e:
        logger.warning("Senuto competitors failed for %s: %s", host, e)
        return []


def _senuto_fetch_aio(host: str) -> dict | None:
    """Live AIO metrics for a domain (Senuto), enriched with top competitors' AIO."""
    token = _senuto_token()
    if not token:
        return None
    main = _senuto_domain_stats(host, token)
    if not main:
        return None
    block = {
        "source": "senuto-api",
        "country": f"country_id={SENUTO_COUNTRY_ID}",
        "fetched_at": datetime.now().strftime("%Y-%m-%d"),
        "domain": host,
        "aio_keywords": main["aio_keywords"],
        "aio_visible_keywords": main["aio_visible_keywords"],
        "visibility": main.get("visibility"),
    }
    try:
        comp_domains = _senuto_competitor_domains(host, token, SENUTO_COMPETITORS)
        if comp_domains:
            with ThreadPoolExecutor(max_workers=min(len(comp_domains), 4)) as ex:
                stats = list(ex.map(lambda d: _senuto_domain_stats(d, token), comp_domains))
            comps = [s for s in stats if s]
            if comps:
                block["competitors"] = comps
    except Exception as e:
        logger.warning("Senuto competitor AIO enrich failed for %s: %s", host, e)
    return block


def load_senuto_aio(url: str) -> dict | None:
    """Resolve the 'AI Overviews visibility' block for the audited domain.

    Uses the live Senuto REST API when credentials are configured (fresh counts),
    and merges in any cached file (e.g. TOP3/10/50 distribution, avg_position).
    Falls back to the cache file alone if the API is unavailable. Returns None
    when neither source yields data — the audit then omits the section.
    """
    host = _senuto_host(url)
    if not host:
        return None
    live = _senuto_fetch_aio(host)
    cached = load_senuto_aio_cache(url)
    if live and cached:
        merged = dict(cached)
        merged.update({k: v for k, v in live.items() if v is not None})
        return merged
    return live or cached


app = FastAPI(title="AI SEO Audit")
app.mount("/static", StaticFiles(directory="static"), name="static")


class AuditRequest(BaseModel):
    url: str


# --- PAGE TYPE CLASSIFICATION ---

PAGE_TYPE_PATTERNS = {
    "contact": ["/kontakt", "/contact"],
    "about": [
        "/o-nas", "/o-firmie", "/o_nas", "/about", "/about-us", "/aboutus",
        "/zespol", "/zespół", "/team", "/our-team", "/nasz-zespol",
        "/kim-jestesmy", "/poznaj-nas", "/historia", "/nasza-historia",
        "/misja", "/wartosci", "/wartości", "/ludzie", "/eksperci",
        "/firma", "/company", "/agencja", "/o-agencji",
    ],
    "article": [
        "/blog/", "/artykul/", "/artykuly/", "/article/", "/articles/",
        "/post/", "/posts/", "/news/", "/aktualnosci/", "/aktualności/",
        "/poradnik/", "/poradniki/", "/wiedza/", "/insights/",
        "/case-study/", "/case-studies/", "/baza-wiedzy/", "/blog-post/",
    ],
    "service": [
        "/uslugi/", "/usługi/", "/usluga/", "/usługa/", "/oferta/", "/services/",
        "/service/", "/produkt/", "/product/", "/produkty/", "/products/",
        "/cennik", "/pricing", "/rozwiazania/", "/solutions/", "/co-robimy/",
    ],
    "category": ["/kategoria/", "/category/", "/tag/", "/tags/", "/archive/", "/archiwum/"],
}

# Sub-paths that are listing/index pages, NOT individual articles. We avoid
# classifying these as 'article' even though they live under e.g. /blog.
ARTICLE_LISTING_PATHS = {
    "/blog", "/aktualnosci", "/aktualności", "/news", "/artykuly", "/artykuły",
    "/poradniki", "/poradnik", "/wiedza", "/insights", "/baza-wiedzy",
    "/case-studies", "/case-study", "/posts", "/post", "/articles", "/article",
}


def _is_article_listing(path: str) -> bool:
    p = path.lower().rstrip("/")
    return p in ARTICLE_LISTING_PATHS


def classify_page_type_heuristic(url: str, base_url: str) -> str | None:
    pu = urlparse(url)
    path = pu.path.lower().rstrip("/")
    base_p = urlparse(base_url).path.rstrip("/")
    if path == base_p or path == "":
        return "homepage"
    if _is_article_listing(path):
        return "category"
    # Article requires a slug AFTER the listing prefix (e.g. /blog/some-title).
    for prefix in PAGE_TYPE_PATTERNS["article"]:
        if prefix in (path + "/"):
            remainder = (path + "/").split(prefix, 1)[1]
            if remainder.strip("/"):
                return "article"
    for type_name in ("about", "contact", "service", "category"):
        if any(p.rstrip("/") in path for p in PAGE_TYPE_PATTERNS[type_name]):
            return type_name
    return None


def normalize_input_url(raw: str) -> str:
    """Accept bare domains ('strategiczni.pl'), with or without scheme/www/trailing slash."""
    v = (raw or "").strip()
    if not v:
        return ""
    v = v.strip().strip("/")
    if not re.match(r"^https?://", v, re.I):
        v = "https://" + v.lstrip("/")
    try:
        pu = urlparse(v)
        if not pu.netloc or "." not in pu.netloc:
            return ""
        return f"{pu.scheme}://{pu.netloc}{pu.path or ''}".rstrip("/") or f"{pu.scheme}://{pu.netloc}"
    except Exception:
        return ""


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


PATENT_PAGE_TYPE_FACTORS = {
    "homepage": [
        "headline-summary-fit",
        "query-intent-classification-alignment",
        "content-entity-alignment",
        "entity-disambiguation-strength",
        "entity-salience",
        "verified-entity-status",
        "brand-mentions-authority-proxy",
        "content-data-alignment-score",
    ],
    "service": [
        "headline-summary-fit",
        "query-intent-classification-alignment",
        "semantic-coherence-score",
        "content-entity-alignment",
        "entity-salience",
        "citable-fragment-density",
        "citation-quality-source-verifiability",
        "content-data-alignment-score",
        "human-likeness-score",
    ],
    "article": [
        "cross-document-factual-consistency",
        "headline-summary-fit",
        "how-to-step-consensus",
        "opinion-subjectivity-detection",
        "query-intent-classification-alignment",
        "semantic-coherence-score",
        "entity-coverage-depth",
        "citable-fragment-density",
        "non-syntheticity-index",
        "citation-quality-source-verifiability",
        "multi-source-consensus",
        "source-confidence-score",
        "content-data-alignment-score",
    ],
    "about": [
        "headline-summary-fit",
        "content-entity-alignment",
        "entity-disambiguation-strength",
        "entity-salience",
        "verified-entity-status",
        "brand-mentions-authority-proxy",
    ],
    "contact": [
        "headline-summary-fit",
        "content-entity-alignment",
        "entity-disambiguation-strength",
        "verified-entity-status",
        "content-data-alignment-score",
    ],
    "category": [
        "headline-summary-fit",
        "query-intent-classification-alignment",
        "semantic-coherence-score",
        "content-entity-alignment",
        "entity-coverage-depth",
        "query-embedding-source-match",
        "content-data-alignment-score",
    ],
    "other": [
        "headline-summary-fit",
        "query-intent-classification-alignment",
        "semantic-coherence-score",
        "content-entity-alignment",
        "citable-fragment-density",
        "non-syntheticity-index",
        "citation-quality-source-verifiability",
        "content-data-alignment-score",
    ],
}


def patent_factor_ids_for_page_type(page_type: str) -> list[str]:
    """Only score factors that have local evidence and are auditable from page content/HTML."""
    requested = PATENT_PAGE_TYPE_FACTORS.get(page_type, PATENT_PAGE_TYPE_FACTORS["other"])
    return [
        factor_id
        for factor_id in requested
        if factor_id in PATENT_FACTORS
        and PATENT_FACTORS[factor_id].get("confidence") in PATENT_SCORING_CONFIDENCE
    ]


def scored_patent_factor_ids() -> list[str]:
    factor_ids = set()
    for page_type in PATENT_PAGE_TYPE_FACTORS:
        factor_ids.update(patent_factor_ids_for_page_type(page_type))
    return sorted(factor_ids)


def _build_patent_factor_prompt(page_type: str) -> str:
    lines: list[str] = []
    for factor_id in patent_factor_ids_for_page_type(page_type):
        factor = PATENT_FACTORS[factor_id]
        checks = "; ".join(factor.get("audit_checks_pl", [])[:3])
        evidence = ", ".join(factor.get("evidence_ids", [])[:3]) or "brak lokalnego evidence_id"
        lines.append(
            f'- "{factor_id}" ({factor.get("name_pl", factor_id)}, '
            f'confidence={factor.get("confidence")}, inference={factor.get("seo_inference_level")}): '
            f'{factor.get("definition_pl", "")} '
            f'Sprawdź: {checks}. Evidence: {evidence}.'
        )
    return "\n".join(lines)


def _build_html_prompt_summary(html_checks: dict | None) -> str:
    if not html_checks:
        return "(brak danych HTML/schema)"
    schema = html_checks.get("schema", {})
    meta = html_checks.get("meta", {})
    summary = {
        "title": meta.get("title"),
        "meta_description_present": bool(meta.get("description")),
        "canonical": meta.get("canonical"),
        "lang": meta.get("lang"),
        "headings": html_checks.get("headings", {}),
        "schema": {
            "types": schema.get("types", []),
            "has_author": schema.get("has_author"),
            "has_datepublished": schema.get("has_datepublished"),
            "has_datemodified": schema.get("has_datemodified"),
            "samples": schema.get("samples", []),
        },
        "links": html_checks.get("links", {}),
        "content": html_checks.get("content", {}),
        "contact_signals": html_checks.get("contact_signals", {}),
        "rag_signals": html_checks.get("rag_signals", {}),
        "images": html_checks.get("images", {}),
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)


def _build_patent_factor_meta() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for factor_id, factor in PATENT_FACTORS.items():
        if factor.get("confidence") not in PATENT_SCORING_CONFIDENCE:
            continue
        out[factor_id] = {
            "label": factor.get("name_pl", factor_id),
            "category": "patent",
            "source": "google_patent",
            "patent_category": factor.get("category_label_pl", factor.get("category", "")),
            "confidence": factor.get("confidence", ""),
            "seo_inference_level": factor.get("seo_inference_level", ""),
            "description": factor.get("definition_pl", ""),
            "evidence_ids": factor.get("evidence_ids", []),
        }
    return out


def _build_patent_client_explanations() -> dict[str, str]:
    out: dict[str, str] = {}
    for factor_id, factor in PATENT_FACTORS.items():
        if factor.get("confidence") not in PATENT_SCORING_CONFIDENCE:
            continue
        action = factor.get("how_to_satisfy_pl") or factor.get("definition_pl") or factor_id
        out[factor_id] = f"Sprawdzamy patentową hipotezę SEO: {action}"
    return out


# --- FACTOR META: Polish labels + meta-categories for tabs ---

CATEGORY_LABELS = {
    "eeat": "E-E-A-T / Wiarygodność",
    "topical": "Topical Authority",
    "geo": "GEO / RAG Extractability",
    "patent": "Patenty Google",
    "accessibility": "Techniczne / Dostępność",
}

FACTOR_META = {
    # HOMEPAGE — 4 eeat + 3 topical + 3 geo = 10
    "clear_value_proposition_above_fold": {"label": "Jasna propozycja wartości w pierwszym ekranie", "category": "geo"},
    "primary_cta_visible": {"label": "Widoczne główne wezwanie do działania", "category": "geo"},
    "navigation_to_key_sections_clear": {"label": "Czytelna nawigacja do kluczowych sekcji", "category": "topical"},
    "trust_signals_logos_reviews_numbers": {"label": "Sygnały zaufania (logotypy klientów, opinie, liczby)", "category": "eeat"},
    "organization_entity_clearly_stated": {"label": "Jasno określona tożsamość firmy", "category": "eeat"},
    "contact_info_accessible_from_home": {"label": "Dostęp do kontaktu ze strony głównej", "category": "geo"},
    "brand_identity_consistent_and_unique": {"label": "Spójna i unikalna tożsamość marki", "category": "topical"},
    "no_generic_marketing_fluff": {"label": "Brak ogólnikowej marketingowej waty", "category": "eeat"},
    "internal_links_to_services_or_products": {"label": "Linki wewnętrzne do usług/produktów", "category": "topical"},
    "external_proof_social_press_awards": {"label": "Dowody zewnętrzne (social media, prasa, nagrody)", "category": "eeat"},
    # SERVICE — 4 eeat + 3 topical + 3 geo = 10
    "clear_offer_or_service_definition": {"label": "Jasna definicja oferty/usługi", "category": "topical"},
    "benefits_stated_explicitly_not_just_features": {"label": "Wyrażone wprost korzyści (nie tylko cechy)", "category": "topical"},
    "pricing_or_price_range_indication": {"label": "Cena lub przedział cenowy", "category": "geo"},
    "use_cases_or_target_customer_defined": {"label": "Scenariusze użycia / profil klienta", "category": "topical"},
    "social_proof_testimonials_clients_case_studies": {"label": "Dowody społeczne (opinie, klienci, case study)", "category": "eeat"},
    "faq_section_addressing_objections": {"label": "Sekcja FAQ odpowiadająca na obiekcje", "category": "geo"},
    "clear_primary_cta_to_contact_or_buy": {"label": "Wyraźne CTA do kontaktu/zakupu", "category": "geo"},
    "differentiation_vs_competition": {"label": "Wyróżnienie się od konkurencji", "category": "eeat"},
    "content_substance_over_fluff": {"label": "Konkret zamiast waty", "category": "eeat"},
    "risk_reversal_guarantee_trial_or_process_clarity": {"label": "Ograniczenie ryzyka (gwarancja/test/przejrzysty proces)", "category": "eeat"},
    # ARTICLE — 4 eeat + 3 topical + 3 geo = 10
    "author_bio_with_name_and_credentials": {"label": "Bio autora z imieniem i kwalifikacjami", "category": "eeat"},
    "publication_date_visible_inline": {"label": "Widoczna data publikacji", "category": "geo"},
    "last_updated_date_visible": {"label": "Widoczna data ostatniej aktualizacji", "category": "topical"},
    "external_authoritative_citations_with_links": {"label": "Cytowania autorytatywnych źródeł z linkami", "category": "eeat"},
    "firsthand_experience_or_original_data": {"label": "Doświadczenie z pierwszej ręki lub własne dane", "category": "eeat"},
    "direct_answer_near_content_start": {"label": "Bezpośrednia odpowiedź na początku treści", "category": "geo"},
    "scannable_structure_headings_lists_tables": {"label": "Skanowalna struktura (nagłówki, listy, tabele)", "category": "geo"},
    "unique_pov_not_generic_rehash": {"label": "Unikalny punkt widzenia (nie powielanie cudzego)", "category": "eeat"},
    "depth_comprehensive_treatment_of_topic": {"label": "Głębia i kompleksowość ujęcia tematu", "category": "topical"},
    "internal_links_to_related_content": {"label": "Linki wewnętrzne do powiązanych treści", "category": "topical"},
    # ABOUT — 4 eeat + 3 topical + 3 geo = 10
    "founder_or_team_profiles_with_names": {"label": "Profile założycieli/zespołu z imionami", "category": "eeat"},
    "credentials_certifications_or_qualifications": {"label": "Certyfikaty i kwalifikacje", "category": "eeat"},
    "company_history_mission_or_founding_story": {"label": "Historia firmy / misja / historia powstania", "category": "topical"},
    "external_validation_awards_partners_media": {"label": "Walidacja zewnętrzna (nagrody, partnerzy, media)", "category": "eeat"},
    "office_location_or_physical_presence": {"label": "Lokalizacja biura / fizyczna obecność", "category": "eeat"},
    "values_or_real_differentiators": {"label": "Wartości i realne wyróżniki", "category": "topical"},
    "links_to_linkedin_or_professional_profiles": {"label": "Linki do LinkedIn / profili zawodowych", "category": "geo"},
    "real_photos_not_stock_implied": {"label": "Prawdziwe zdjęcia (nie stockowe)", "category": "geo"},
    "clients_or_projects_showcased": {"label": "Prezentacja klientów / projektów", "category": "topical"},
    "contact_pathway_from_about": {"label": "Ścieżka do kontaktu z O nas", "category": "geo"},
    # CONTACT — 4 eeat + 3 topical + 3 geo = 10
    "nap_name_address_phone_complete_and_visible": {"label": "Pełny i widoczny NAP (nazwa/adres/telefon)", "category": "eeat"},
    "contact_form_present_and_clear": {"label": "Czytelny formularz kontaktowy", "category": "topical"},
    "opening_hours_visible": {"label": "Widoczne godziny otwarcia", "category": "topical"},
    "phone_clickable_tel_link": {"label": "Klikalny numer telefonu", "category": "geo"},
    "email_clickable_mailto": {"label": "Klikalny e-mail", "category": "geo"},
    "map_or_embedded_location": {"label": "Mapa lub osadzona lokalizacja", "category": "geo"},
    "multiple_contact_channels": {"label": "Wiele kanałów kontaktu", "category": "eeat"},
    "department_or_role_specific_contacts": {"label": "Kontakty per dział / rola", "category": "topical"},
    "response_time_expectation": {"label": "Informacja o czasie odpowiedzi", "category": "eeat"},
    "physical_office_photo_or_proof": {"label": "Zdjęcie biura / dowód fizycznej obecności", "category": "eeat"},
    # CATEGORY — 3 eeat + 4 topical + 3 geo = 10
    "meaningful_category_intro_copy_not_thin": {"label": "Sensowny wstęp kategorii (nie thin content)", "category": "eeat"},
    "unique_category_h1_and_title": {"label": "Unikalny H1 i title kategorii", "category": "eeat"},
    "category_specific_meta_description": {"label": "Meta description dedykowana kategorii", "category": "geo"},
    "internal_links_to_items_with_context": {"label": "Linki wewnętrzne do elementów z kontekstem", "category": "topical"},
    "filters_or_facets_if_applicable": {"label": "Filtry / fasety (jeśli zasadne)", "category": "topical"},
    "pagination_or_load_more_sensible": {"label": "Paginacja lub 'załaduj więcej' z sensem", "category": "geo"},
    "subcategory_links_exposed": {"label": "Widoczne linki do podkategorii", "category": "topical"},
    "no_boilerplate_content_duplicated": {"label": "Brak powielanego szablonowego contentu", "category": "eeat"},
    "visual_hierarchy_for_scannability": {"label": "Wizualna hierarchia ułatwiająca skanowanie", "category": "geo"},
    "related_categories_linked": {"label": "Powiązane kategorie z linkami", "category": "topical"},
    # OTHER — 3 eeat + 3 topical + 4 geo = 10
    "clear_page_purpose_stated": {"label": "Jasno określony cel strony", "category": "topical"},
    "value_for_user_evident": {"label": "Widoczna wartość dla użytkownika", "category": "eeat"},
    "heading_hierarchy_correct": {"label": "Poprawna hierarchia nagłówków", "category": "topical"},
    "meta_description_descriptive_and_unique": {"label": "Opisowa i unikalna meta description", "category": "geo"},
    "scannable_structure_lists_or_subheadings": {"label": "Skanowalna struktura (listy / podtytuły)", "category": "geo"},
    "appropriate_schema_for_content_type": {"label": "Odpowiedni schema dla typu treści", "category": "geo"},
    "internal_links_to_contextual_content": {"label": "Linki wewnętrzne do kontekstowych treści", "category": "topical"},
    "no_generic_ai_generated_content": {"label": "Brak generycznego AI contentu", "category": "eeat"},
    "external_sources_or_proof_where_relevant": {"label": "Zewnętrzne źródła / dowody gdzie zasadne", "category": "eeat"},
    "clear_next_step_or_cta": {"label": "Jasny następny krok / CTA", "category": "geo"},
}

FACTOR_META.update(_build_patent_factor_meta())

TECH_FACTOR_META = {
    "meta_title_present": {"label": "Obecny tag <title>", "category": "tech"},
    "meta_description": {"label": "Obecna meta description", "category": "tech"},
    "canonical_tag": {"label": "Tag canonical", "category": "tech"},
    "h1_single": {"label": "Pojedynczy nagłówek H1", "category": "tech"},
    "heading_hierarchy": {"label": "Poprawna hierarchia nagłówków", "category": "tech"},
    "og_tags": {"label": "Tagi Open Graph", "category": "tech"},
    "viewport_meta": {"label": "Meta viewport (mobile)", "category": "tech"},
    "lang_attribute": {"label": "Atrybut lang w <html>", "category": "tech"},
    "image_alt_coverage": {"label": "Pokrycie obrazów atrybutem alt", "category": "tech"},
    "semantic_html5_tags": {"label": "Semantyczne tagi HTML5", "category": "tech"},
    "response_size_ok": {"label": "Rozmiar HTML w normie", "category": "tech"},
    "organization_schema": {"label": "Schema Organization", "category": "tech"},
    "website_schema": {"label": "Schema WebSite", "category": "tech"},
    "any_schema": {"label": "Dowolne schema.org", "category": "tech"},
    "product_or_service_schema": {"label": "Schema Product lub Service", "category": "tech"},
    "faq_schema_bonus": {"label": "Schema FAQPage (bonus)", "category": "tech"},
    "breadcrumb_schema": {"label": "Schema BreadcrumbList", "category": "tech"},
    "article_schema": {"label": "Schema Article / BlogPosting", "category": "tech"},
    "schema_author_field": {"label": "Pole 'author' w schema", "category": "tech"},
    "schema_dates": {"label": "Daty w schema (published / modified)", "category": "tech"},
    "person_schema_team": {"label": "Schema Person (zespół)", "category": "tech"},
    "localbusiness_or_organization_schema": {"label": "Schema LocalBusiness / Organization", "category": "tech"},
    "tel_link_present": {"label": "Klikalny numer telefonu (tel:)", "category": "tech"},
    "mailto_link_present": {"label": "Klikalny e-mail (mailto:)", "category": "tech"},
    "contact_form_present": {"label": "Formularz kontaktowy (<form>)", "category": "tech"},
    "itemlist_schema": {"label": "Schema ItemList", "category": "tech"},
}


CLIENT_FACTOR_EXPLANATIONS = {
    "clear_value_proposition_above_fold": "Od razu tłumaczymy klientom i AI, czym zajmuje się firma i co oferuje.",
    "primary_cta_visible": "Wskazujemy jasny następny krok, by ułatwić kontakt lub zakup.",
    "navigation_to_key_sections_clear": "Ułatwiamy odnalezienie najważniejszych informacji na stronie.",
    "trust_signals_logos_reviews_numbers": "Budujemy zaufanie dzięki opiniom i doświadczeniu.",
    "organization_entity_clearly_stated": "Sygnalizujemy AI kim dokładnie jesteśmy jako firma (budowa marki).",
    "contact_info_accessible_from_home": "Umożliwiamy szybki i prosty kontakt bezpośrednio po wejściu na stronę.",
    "brand_identity_consistent_and_unique": "Wyróżniamy się na tle innych i zapadamy w pamięć.",
    "no_generic_marketing_fluff": "Unikamy pustych haseł reklamowych, które AI i użytkownicy ignorują.",
    "internal_links_to_services_or_products": "Łączymy stronę główną z najważniejszymi elementami oferty.",
    "external_proof_social_press_awards": "Potwierdzamy nasz profesjonalizm linkami do nagród i mediów.",
    "clear_offer_or_service_definition": "Precyzyjnie opisujemy, co klient u nas kupuje.",
    "benefits_stated_explicitly_not_just_features": "Mówimy językiem korzyści dla klienta, a nie tylko wypisujemy cechy produktu.",
    "pricing_or_price_range_indication": "Pokazujemy choćby przedziały cenowe, bo tego szukają klienci i AI.",
    "use_cases_or_target_customer_defined": "Wskazujemy dokładnie komu nasza oferta najbardziej pomoże.",
    "social_proof_testimonials_clients_case_studies": "Dowodzimy skuteczności na podstawie historii zadowolonych klientów.",
    "faq_section_addressing_objections": "Odpowiadamy z góry na obawy i częste pytania kupujących.",
    "clear_primary_cta_to_contact_or_buy": "Wyraźnie prosimy o kontakt lub dodanie do koszyka.",
    "differentiation_vs_competition": "Wyjaśniamy dlaczego warto wybrać nas, a nie konkurencję.",
    "content_substance_over_fluff": "Dajemy same konkrety w tekście, by nikt nie tracił czasu.",
    "risk_reversal_guarantee_trial_or_process_clarity": "Zdejmujemy z klienta obawy o ryzyko, np. pokazując łatwe zwroty.",
    "author_bio_with_name_and_credentials": "Pokazujemy AI i czytelnikom, że tekst pisał prawdziwy ekspert.",
    "publication_date_visible_inline": "Udowadniamy, że informacja jest świeża.",
    "last_updated_date_visible": "Sygnalizujemy, że artykuł jest na bieżąco aktualizowany.",
    "external_authoritative_citations_with_links": "Powołujemy się na mocne i wiarygodne źródła zewnętrzne.",
    "firsthand_experience_or_original_data": "Prezentujemy własne doświadczenia, a nie tylko kopiujemy wiedzę z innych miejsc.",
    "direct_answer_near_content_start": "Dajemy odpowiedź na początku, bo AI lubi streszczenia i konkrety.",
    "scannable_structure_headings_lists_tables": "Formatujemy tekst tak, by czytało się go łatwo i skanowało wzrokiem.",
    "unique_pov_not_generic_rehash": "Dodajemy unikalną wartość, zamiast pisać to co wszyscy inni.",
    "depth_comprehensive_treatment_of_topic": "Wyczerpujemy temat, stając się najlepszym źródłem w sieci.",
    "internal_links_to_related_content": "Prowadzimy czytelnika do innych, wartościowych stron na naszym portalu.",
    "founder_or_team_profiles_with_names": "Budujemy ludzką twarz firmy pokazując ludzi za nią stojących.",
    "credentials_certifications_or_qualifications": "Potwierdzamy nasze umiejętności certyfikatami.",
    "company_history_mission_or_founding_story": "Opowiadamy autentyczną historię firmy.",
    "external_validation_awards_partners_media": "Uwiarygadniamy naszą pozycję partnerami i nagrodami.",
    "office_location_or_physical_presence": "Udowadniamy, że istniejemy w realnym świecie.",
    "values_or_real_differentiators": "Pokazujemy wartości jakimi kieruje się nasz zespół.",
    "links_to_linkedin_or_professional_profiles": "Łączymy naszą stronę z zawodowymi profilami z sieci.",
    "real_photos_not_stock_implied": "Wzbudzamy większe zaufanie dzięki prawdziwym zdjęciom.",
    "clients_or_projects_showcased": "Pokazujemy komu do tej pory pomogliśmy.",
    "contact_pathway_from_about": "Umożliwiamy płynne przejście z poznania zespołu do wysłania wiadomości.",
    "nap_name_address_phone_complete_and_visible": "Podajemy komplet danych rejestrowych - nazwa, adres, telefon.",
    "contact_form_present_and_clear": "Dajemy łatwy formularz dla tych, którzy nie chcą pisać maili.",
    "opening_hours_visible": "Informujemy, kiedy można się z nami kontaktować.",
    "phone_clickable_tel_link": "Pozwalamy na szybkie wykonanie połączenia z telefonu klikając w numer.",
    "email_clickable_mailto": "Umożliwiamy otwarcie programu pocztowego przez kliknięcie w maila.",
    "map_or_embedded_location": "Ułatwiamy znalezienie naszego biura dodając mapę.",
    "multiple_contact_channels": "Dajemy klientowi wybór preferowanej formy kontaktu.",
    "department_or_role_specific_contacts": "Ułatwiamy dotarcie do odpowiednich specjalistów w większej firmie.",
    "response_time_expectation": "Tłumaczymy, jak długo czeka się na odpowiedź od nas.",
    "physical_office_photo_or_proof": "Uwiarygadniamy lokalne wyniki w Google poprzez zdjęcia biura.",
    "meaningful_category_intro_copy_not_thin": "Dodajemy opis ułatwiający AI zrozumienie tematyki tej kategorii.",
    "unique_category_h1_and_title": "Jasno zatytułowana sekcja, niepowtarzalna z innymi.",
    "category_specific_meta_description": "Posiadamy opis podstrony używany przez wyszukiwarki do jej streszczenia.",
    "internal_links_to_items_with_context": "Ułatwiamy AI przechodzenie do poszczególnych produktów z kategorii.",
    "filters_or_facets_if_applicable": "Dajemy klientom narzędzia, żeby szybko znaleźli to, czego chcą.",
    "pagination_or_load_more_sensible": "Ułatwiamy przełączanie między kolejnymi stronami długich kategorii.",
    "subcategory_links_exposed": "Tworzymy sensowną hierarchię grupującą mniejsze tematy.",
    "no_boilerplate_content_duplicated": "Nie mamy automatycznie generowanych i powtarzalnych zapychaczy.",
    "visual_hierarchy_for_scannability": "Prezentujemy zawartość tej sekcji w przyjemny dla oka sposób.",
    "related_categories_linked": "Sugerujemy podobne tematy, zatrzymując użytkownika na dłużej.",
    "clear_page_purpose_stated": "Szybko informujemy jaki jest cel tej podstrony.",
    "value_for_user_evident": "Pokazujemy korzyść za wejście na ten link.",
    "heading_hierarchy_correct": "Tytuły i podtytuły (H1, H2) są ułożone logicznie jak w dobrej książce.",
    "meta_description_descriptive_and_unique": "Unikalne streszczenie witryny pod roboty wyszukiwarek.",
    "scannable_structure_lists_or_subheadings": "Rozdzielamy długie bloki tekstu listami i wypunktowaniami.",
    "appropriate_schema_for_content_type": "Posiadamy techniczne znaczniki w kodzie potrzebne dla AI.",
    "internal_links_to_contextual_content": "Linkujemy do innych artykułów, dając wiedzę na szerszy temat.",
    "no_generic_ai_generated_content": "Nie wrzucamy byle jakiego materiału od AI (dbamy o jakość).",
    "external_sources_or_proof_where_relevant": "Dodajemy odnośniki potwierdzające prawdziwość informacji.",
    "clear_next_step_or_cta": "Podpowiadamy co zrobić po przeczytaniu tego materiału.",
    "meta_title_present": "Mamy prawidłowo wpisany tytuł ułatwiający Google i AI zrozumienie strony.",
    "meta_description": "Mamy zwięzły opis dla Google podsumowujący o czym jest podstrona.",
    "canonical_tag": "Mamy sygnał dla Google unikający problemów ze sklonowanymi podstronami.",
    "h1_single": "Jest tylko jeden główny tytuł, co ułatwia kategoryzowanie strony.",
    "heading_hierarchy": "Podtytuły na stronie zachowują logiczną hierarchię jak w podręczniku.",
    "og_tags": "Strona poprawnie wyświetla się podczas udostępniania w social mediach.",
    "viewport_meta": "Strona poprawnie skaluje się do telefonów i tabletów.",
    "lang_attribute": "Mówimy botom, w jakim języku pomyślana jest strona.",
    "image_alt_coverage": "Posiadamy opisy graficzne ułatwiające AI 'zobaczenie' zdjęć.",
    "semantic_html5_tags": "Kod strony jest nowoczesny i łatwo czytelny przez dzisiejsze boty internetowe.",
    "response_size_ok": "Rozmiar wagowy witryny jest w granicach normy.",
    "organization_schema": "Prowadzimy AI za rękę poprzez kod informujący wprost, że jesteśmy Organizacją.",
    "website_schema": "Mamy odpowiednie znaczniki kodowe świadczące o posiadaniu poprawnej struktury serwisu.",
    "any_schema": "Używamy specjalnych, ukrytych w kodzie opisów (Schema), na których polega sztuczna inteligencja.",
    "product_or_service_schema": "Posiadamy ukryty w kodzie znacznik wspierający widoczność Oferty w Google i AI.",
    "faq_schema_bonus": "Posiadamy w kodzie znacznik wyróżniający często zadawane pytania (FAQ).",
    "breadcrumb_schema": "Struktura nawigacyjna tzw. Okruszki ma wsparcie techniczne.",
    "article_schema": "Kod naszej strony wskazuje wprost do AI, że dany tekst to autorski Artykuł.",
    "schema_author_field": "W ukrytym kodzie precyzujemy, kto napisał dany tekst.",
    "schema_dates": "W kodzie dla AI wysyłamy poprawną datę pierwszej i ostatniej wersji artykułu.",
    "person_schema_team": "W kodzie znajduje się opis techniczny poszczególnych osób z zespołu.",
    "localbusiness_or_organization_schema": "Zdefiniowaliśmy w kodzie dla AI naszą obecność lokalną lub jako firma.",
    "tel_link_present": "Kliknięcie w telefon automatycznie go wybiera w urządzeniu.",
    "mailto_link_present": "Kliknięcie w maila automatycznie otwiera aplikację pocztową.",
    "contact_form_present": "Klient może napisać wiadomość od razu bez przechodzenia na swoją pocztę.",
    "itemlist_schema": "Kategoria używa odpowiednich danych strukturalnych do opisania listy przedmiotów.",
    "robots_txt_accessible": "Sygnalizujemy robotom wyszukiwarek plik z instrukcjami, jak czytać całą domenę.",
    "gptbot_not_blocked": "Pozwalamy robotowi ChatGPT czytać naszą domenę.",
    "perplexitybot_not_blocked": "Pozwalamy robotowi Perplexity uczyć się z naszych tekstów.",
    "claudebot_not_blocked": "Zezwalamy botowi sztucznej inteligencji od Claude (Anthropic) na wejścia.",
    "google_extended_not_blocked": "Nie blokujemy bota Google tworzącego odpowiedzi z udziałem AI.",
    "crawl_delay_ok": "Boty badające stronę nie są spowalniane dziwnymi limitami czasowymi.",
    "sitemap_present": "Wskazujemy robotom mapę całej strony z wypisanymi adresami.",
    "sitemap_in_robots": "Linkujemy od razu do mapy witryny we wspomnianym wyżej pliku 'robots.txt'.",
    "llms_txt_present": "Tworzymy specjalny plik tekstowy, ułatwiający botom AI zebranie wiedzy z całej naszej witryny.",
    "https_enabled": "Posiadamy bezpieczny certyfikat zabezpieczający całą domenę - kłódeczkę w przeglądarce.",
    "hreflang_used": "Podajemy w kodzie do AI i Google języki, w których dostępna jest nasza firma.",
    "hsts_enabled": "Przeglądarka zawsze używa bezpiecznego połączenia – chroni użytkowników i wzmacnia zaufanie.",
    "compression_enabled": "Pliki strony są kompresowane przed wysłaniem – szybsze ładowanie, mniejsze zużycie danych.",
    "performance_score_mobile": "Strona szybko ładuje się na telefonach – kluczowe, bo większość użytkowników przegląda na mobile.",
    "lcp_mobile_ok": "Pierwsza duża grafika/nagłówek pojawia się szybko – kluczowe Core Web Vital.",
    "cls_mobile_ok": "Treść nie skacze w trakcie ładowania – stabilność wizualna podnosi zaufanie i UX.",
    "tbt_mobile_ok": "Strona reaguje natychmiast na dotyk – brak zacięć przy interakcji.",
    "fcp_mobile_ok": "Pierwsza treść pojawia się szybko – użytkownik widzi, że coś się dzieje.",
}

CLIENT_FACTOR_EXPLANATIONS.update(_build_patent_client_explanations())

DOMAIN_TECH_META = {
    "robots_txt_accessible": {"label": "Dostępny plik robots.txt", "category": "tech"},
    "gptbot_not_blocked": {"label": "GPTBot (OpenAI) niezablokowany", "category": "tech"},
    "perplexitybot_not_blocked": {"label": "PerplexityBot niezablokowany", "category": "tech"},
    "claudebot_not_blocked": {"label": "ClaudeBot (Anthropic) niezablokowany", "category": "tech"},
    "google_extended_not_blocked": {"label": "Google-Extended niezablokowany (AI Overviews)", "category": "tech"},
    "crawl_delay_ok": {"label": "Crawl-delay w normie", "category": "tech"},
    "sitemap_present": {"label": "Sitemap XML obecna", "category": "tech"},
    "sitemap_in_robots": {"label": "Link do sitemap w robots.txt", "category": "tech"},
    "llms_txt_present": {"label": "Plik llms.txt obecny", "category": "tech"},
    "https_enabled": {"label": "HTTPS włączone", "category": "tech"},
    "hreflang_used": {"label": "Tagi hreflang", "category": "tech"},
    "hsts_enabled": {"label": "HSTS (Strict-Transport-Security)", "category": "tech"},
    "compression_enabled": {"label": "Kompresja odpowiedzi (gzip/brotli)", "category": "tech"},
}

# Domain tech factor weights. Normalized dynamically so sum doesn't need to equal 100.
DOMAIN_TECH_WEIGHTS = {
    "llms_txt_present": 20,
    "gptbot_not_blocked": 15,
    "claudebot_not_blocked": 12,
    "perplexitybot_not_blocked": 12,
    "google_extended_not_blocked": 8,
    "robots_txt_accessible": 10,
    "sitemap_present": 8,
    "https_enabled": 7,
    "hsts_enabled": 3,
    "compression_enabled": 3,
    "crawl_delay_ok": 4,
    "sitemap_in_robots": 3,
    "hreflang_used": 1,
}

# Content category weights for per-page factor scoring. EEAT matters most.
CONTENT_CATEGORY_WEIGHTS = {
    "eeat": 3.0,
    "rag": 2.0,
    "conversion": 1.5,
    "trust": 1.5,
    "patent": 2.0,
    "topical": 1.0,
    "tech": 1.0,
}

# Per-factor point penalties applied to overall score when critical factors are absent.
CRITICAL_FACTOR_PENALTIES = {
    "llms_txt_present": 8,
    "gptbot_not_blocked": 7,
    "claudebot_not_blocked": 6,
    "perplexitybot_not_blocked": 6,
    "google_extended_not_blocked": 4,
    "robots_txt_accessible": 6,
    "https_enabled": 12,
}

UI_GROUP_ORDER = ["technical", "performance", "onpage", "eeat", "patents", "ai_aeo"]
UI_GROUP_LABELS = {
    "technical": "Techniczne SEO",
    "performance": "Wydajność",
    "onpage": "On-page",
    "eeat": "E-E-A-T",
    "patents": "Patenty Google",
    "ai_aeo": "AI / AEO",
}
# Weights chosen for AI SEO priorities: authority + AI extractability > foundations > perf.
UI_GROUP_WEIGHTS = {
    "technical": 20,
    "performance": 10,
    "onpage": 10,
    "eeat": 25,
    "patents": 15,
    "ai_aeo": 20,
}

# Czynniki o najsłabszym impacie: higiena techniczna/UX, którą zdaje niemal każdy
# nowoczesny szablon/CMS i która słabo koreluje z cytowalnością przez LLM. Domyślny
# impact czynnika to 2 (grupowo-krytyczny 3); tu celowo schodzimy PONIŻEJ floora=1,
# aby te "zawsze na plus" sygnały przestały zawyżać wynik ogólny. Wartość = absolutna
# waga (impact) nadpisująca wynik z _impact_effort_for_factor. Łatwe do strojenia.
LOW_IMPACT_FACTORS = {
    # Trywialne, niemal 100% pass-rate, znikomy wpływ na AI -> waga 0.25
    "image_alt_coverage": 0.25,      # atrybuty alt (przykład podany przez użytkownika)
    "viewport_meta": 0.25,           # każdy responsywny szablon to ma
    "lang_attribute": 0.25,          # near-universal
    "semantic_html5_tags": 0.25,     # standard w nowoczesnych templa­tkach
    "response_size_ok": 0.25,        # słaby sygnał jakości
    "og_tags": 0.25,                 # meta social, nie napędza cytowań AI
    "meta_title_present": 0.25,      # sprawdzana sama OBECNOŚĆ -> ~100% pass
    # Drobna higiena kontaktu/UX, niski wpływ na ekstrakcję przez LLM -> waga 0.5
    "tel_link_present": 0.5,
    "mailto_link_present": 0.5,
    "contact_form_present": 0.5,
    "phone_clickable_tel_link": 0.5,
    "email_clickable_mailto": 0.5,
    "opening_hours_visible": 0.5,
    "map_or_embedded_location": 0.5,
    "pagination_or_load_more_sensible": 0.5,
    "filters_or_facets_if_applicable": 0.5,
}

PERFORMANCE_FACTOR_META = {
    "performance_score_mobile": {"label": "Lighthouse Performance (mobile)", "category": "performance"},
    "lcp_mobile_ok": {"label": "LCP – Largest Contentful Paint (mobile)", "category": "performance"},
    "cls_mobile_ok": {"label": "CLS – Cumulative Layout Shift (mobile)", "category": "performance"},
    "tbt_mobile_ok": {"label": "TBT – Total Blocking Time (mobile)", "category": "performance"},
    "fcp_mobile_ok": {"label": "FCP – First Contentful Paint (mobile)", "category": "performance"},
}

# --- FAIL LABELS: alternatywna nazwa czynnika gdy audyt NIE jest zaliczony (status "missing") ---
# Każdy klucz to factor_id. `label` (w meta wyżej) = nazwa gdy PASS (brzmi jak zaliczone);
# FAIL_LABELS[key] = nazwa gdy FAIL (problem wskazany wprost w nazwie, np. "Brak pliku llms.txt").
FAIL_LABELS = {
    # HOMEPAGE
    "clear_value_proposition_above_fold": "Brak jasnej propozycji wartości w pierwszym ekranie",
    "primary_cta_visible": "Brak widocznego wezwania do działania",
    "navigation_to_key_sections_clear": "Nieczytelna nawigacja do kluczowych sekcji",
    "trust_signals_logos_reviews_numbers": "Brak sygnałów zaufania (logotypy, opinie, liczby)",
    "organization_entity_clearly_stated": "Niejasna tożsamość firmy",
    "contact_info_accessible_from_home": "Brak dostępu do kontaktu ze strony głównej",
    "brand_identity_consistent_and_unique": "Niespójna lub generyczna tożsamość marki",
    "no_generic_marketing_fluff": "Ogólnikowa marketingowa wata w treści",
    "internal_links_to_services_or_products": "Brak linków wewnętrznych do usług/produktów",
    "external_proof_social_press_awards": "Brak dowodów zewnętrznych (social media, prasa, nagrody)",
    # SERVICE
    "clear_offer_or_service_definition": "Niejasna definicja oferty/usługi",
    "benefits_stated_explicitly_not_just_features": "Brak wyrażonych wprost korzyści (tylko cechy)",
    "pricing_or_price_range_indication": "Brak ceny lub przedziału cenowego",
    "use_cases_or_target_customer_defined": "Brak scenariuszy użycia / profilu klienta",
    "social_proof_testimonials_clients_case_studies": "Brak dowodów społecznych (opinie, klienci, case study)",
    "faq_section_addressing_objections": "Brak sekcji FAQ odpowiadającej na obiekcje",
    "clear_primary_cta_to_contact_or_buy": "Brak wyraźnego CTA do kontaktu/zakupu",
    "differentiation_vs_competition": "Brak wyróżnienia się od konkurencji",
    "content_substance_over_fluff": "Treść bez konkretów (wata zamiast wartości)",
    "risk_reversal_guarantee_trial_or_process_clarity": "Brak ograniczenia ryzyka (gwarancja/test/przejrzysty proces)",
    # ARTICLE
    "author_bio_with_name_and_credentials": "Brak bio autora z imieniem i kwalifikacjami",
    "publication_date_visible_inline": "Brak widocznej daty publikacji",
    "last_updated_date_visible": "Brak widocznej daty ostatniej aktualizacji",
    "external_authoritative_citations_with_links": "Brak cytowań autorytatywnych źródeł z linkami",
    "firsthand_experience_or_original_data": "Brak doświadczenia z pierwszej ręki / własnych danych",
    "direct_answer_near_content_start": "Brak bezpośredniej odpowiedzi na początku treści",
    "scannable_structure_headings_lists_tables": "Słabo skanowalna struktura (brak nagłówków/list/tabel)",
    "unique_pov_not_generic_rehash": "Brak unikalnego punktu widzenia (powielanie cudzego)",
    "depth_comprehensive_treatment_of_topic": "Powierzchowne ujęcie tematu (brak głębi)",
    "internal_links_to_related_content": "Brak linków wewnętrznych do powiązanych treści",
    # ABOUT
    "founder_or_team_profiles_with_names": "Brak profili założycieli/zespołu z imionami",
    "credentials_certifications_or_qualifications": "Brak certyfikatów i kwalifikacji",
    "company_history_mission_or_founding_story": "Brak historii firmy / misji",
    "external_validation_awards_partners_media": "Brak walidacji zewnętrznej (nagrody, partnerzy, media)",
    "office_location_or_physical_presence": "Brak lokalizacji biura / fizycznej obecności",
    "values_or_real_differentiators": "Brak wartości i realnych wyróżników",
    "links_to_linkedin_or_professional_profiles": "Brak linków do LinkedIn / profili zawodowych",
    "real_photos_not_stock_implied": "Brak prawdziwych zdjęć (zdjęcia stockowe)",
    "clients_or_projects_showcased": "Brak prezentacji klientów / projektów",
    "contact_pathway_from_about": "Brak ścieżki do kontaktu z 'O nas'",
    # CONTACT
    "nap_name_address_phone_complete_and_visible": "Niepełne lub ukryte dane NAP (nazwa/adres/telefon)",
    "contact_form_present_and_clear": "Brak czytelnego formularza kontaktowego",
    "opening_hours_visible": "Brak widocznych godzin otwarcia",
    "phone_clickable_tel_link": "Numer telefonu nie jest klikalny",
    "email_clickable_mailto": "E-mail nie jest klikalny",
    "map_or_embedded_location": "Brak mapy / osadzonej lokalizacji",
    "multiple_contact_channels": "Brak wielu kanałów kontaktu",
    "department_or_role_specific_contacts": "Brak kontaktów per dział / rola",
    "response_time_expectation": "Brak informacji o czasie odpowiedzi",
    "physical_office_photo_or_proof": "Brak zdjęcia biura / dowodu fizycznej obecności",
    # CATEGORY
    "meaningful_category_intro_copy_not_thin": "Brak sensownego wstępu kategorii (thin content)",
    "unique_category_h1_and_title": "Brak unikalnego H1 i title kategorii",
    "category_specific_meta_description": "Brak meta description dedykowanej kategorii",
    "internal_links_to_items_with_context": "Brak linków wewnętrznych do elementów z kontekstem",
    "filters_or_facets_if_applicable": "Brak filtrów / fasetów (gdy zasadne)",
    "pagination_or_load_more_sensible": "Brak sensownej paginacji / 'załaduj więcej'",
    "subcategory_links_exposed": "Brak widocznych linków do podkategorii",
    "no_boilerplate_content_duplicated": "Powielany szablonowy content (boilerplate)",
    "visual_hierarchy_for_scannability": "Brak wizualnej hierarchii ułatwiającej skanowanie",
    "related_categories_linked": "Brak powiązanych kategorii z linkami",
    # OTHER
    "clear_page_purpose_stated": "Niejasny cel strony",
    "value_for_user_evident": "Brak widocznej wartości dla użytkownika",
    "heading_hierarchy_correct": "Błędna hierarchia nagłówków",
    "meta_description_descriptive_and_unique": "Brak opisowej i unikalnej meta description",
    "scannable_structure_lists_or_subheadings": "Słabo skanowalna struktura (brak list/podtytułów)",
    "appropriate_schema_for_content_type": "Brak odpowiedniego schema dla typu treści",
    "internal_links_to_contextual_content": "Brak linków wewnętrznych do kontekstowych treści",
    "no_generic_ai_generated_content": "Generyczny AI-content (niska jakość)",
    "external_sources_or_proof_where_relevant": "Brak zewnętrznych źródeł / dowodów",
    "clear_next_step_or_cta": "Brak jasnego następnego kroku / CTA",
    # TECH (per-page HTML)
    "meta_title_present": "Brak tagu <title>",
    "meta_description": "Brak meta description",
    "canonical_tag": "Brak tagu canonical",
    "h1_single": "Brak pojedynczego H1 (zero lub wiele)",
    "heading_hierarchy": "Błędna hierarchia nagłówków",
    "og_tags": "Brak tagów Open Graph",
    "viewport_meta": "Brak meta viewport (mobile)",
    "lang_attribute": "Brak atrybutu lang w <html>",
    "image_alt_coverage": "Braki w atrybutach alt obrazów",
    "semantic_html5_tags": "Brak semantycznych tagów HTML5",
    "response_size_ok": "Rozmiar HTML poza normą",
    "organization_schema": "Brak schema Organization",
    "website_schema": "Brak schema WebSite",
    "any_schema": "Brak jakiegokolwiek schema.org",
    "product_or_service_schema": "Brak schema Product/Service",
    "faq_schema_bonus": "Brak schema FAQPage",
    "breadcrumb_schema": "Brak schema BreadcrumbList",
    "article_schema": "Brak schema Article/BlogPosting",
    "schema_author_field": "Brak pola 'author' w schema",
    "schema_dates": "Brak dat w schema (published/modified)",
    "person_schema_team": "Brak schema Person (zespół)",
    "localbusiness_or_organization_schema": "Brak schema LocalBusiness/Organization",
    "tel_link_present": "Brak klikalnego numeru telefonu (tel:)",
    "mailto_link_present": "Brak klikalnego e-maila (mailto:)",
    "contact_form_present": "Brak formularza kontaktowego (<form>)",
    "itemlist_schema": "Brak schema ItemList",
    # DOMAIN TECH
    "robots_txt_accessible": "Brak dostępnego pliku robots.txt",
    "gptbot_not_blocked": "GPTBot (OpenAI) zablokowany",
    "perplexitybot_not_blocked": "PerplexityBot zablokowany",
    "claudebot_not_blocked": "ClaudeBot (Anthropic) zablokowany",
    "google_extended_not_blocked": "Google-Extended zablokowany (AI Overviews)",
    "crawl_delay_ok": "Zbyt wysoki crawl-delay dla botów",
    "sitemap_present": "Brak sitemap XML",
    "sitemap_in_robots": "Brak linku do sitemap w robots.txt",
    "llms_txt_present": "Brak pliku llms.txt",
    "https_enabled": "Brak HTTPS",
    "hreflang_used": "Brak tagów hreflang",
    "hsts_enabled": "Brak HSTS (Strict-Transport-Security)",
    "compression_enabled": "Brak kompresji odpowiedzi (gzip/brotli)",
    # PERFORMANCE
    "performance_score_mobile": "Niski wynik Lighthouse Performance (mobile)",
    "lcp_mobile_ok": "LCP zbyt wolne (mobile)",
    "cls_mobile_ok": "Zbyt wysoki CLS (mobile)",
    "tbt_mobile_ok": "Zbyt wysoki TBT (mobile)",
    "fcp_mobile_ok": "FCP zbyt wolne (mobile)",
}


def _inject_fail_labels() -> None:
    """Wstrzykuje `label_fail` do wszystkich słowników meta (single source of truth: FAIL_LABELS)."""
    for meta_dict in (FACTOR_META, TECH_FACTOR_META, DOMAIN_TECH_META, PERFORMANCE_FACTOR_META):
        for key, meta in meta_dict.items():
            if key in FAIL_LABELS:
                meta["label_fail"] = FAIL_LABELS[key]


_inject_fail_labels()

# Stable per-factor descriptions (PRO/tech tone). Used by _generic_detail() before falling back to group templates.
FACTOR_DETAILS = {
    # --- FACTOR_META: HOMEPAGE ---
    "clear_value_proposition_above_fold": {
        "what": "Sprawdza, czy w pierwszym ekranie (above the fold) znajduje się jednoznaczne zdanie określające czym firma się zajmuje i dla kogo — wykrywane przez analizę pierwszych H1/H2 oraz hero copy.",
        "why": "LLM-y i crawlery używają pierwszego widocznego bloku tekstu jako głównego sygnału klasyfikacji entity; brak VP w hero powoduje, że RAG ekstraktuje przypadkowy fragment lub boilerplate jako definicję firmy.",
        "how_to_fix": "Umieść w hero jedno zdanie w formacie 'X dla Y dające Z' jako H1 lub leadowy <p>, bez ogólnych sloganów."
    },
    "primary_cta_visible": {
        "what": "Wykrywa obecność wyraźnie wyróżnionego przycisku/linku akcji (kontrastowy <a>/<button>) w pierwszym widoku strony, prowadzącego do konwersji.",
        "why": "Sygnał intencji strony dla klasyfikatorów (transactional vs informational) — pomaga AI poprawnie przypisać typ strony do query intent.",
        "how_to_fix": "Dodaj jeden dominujący CTA w hero z czasownikiem akcji ('Umów konsultację', 'Pobierz ofertę') prowadzący do dedykowanego URL."
    },
    "navigation_to_key_sections_clear": {
        "what": "Analizuje strukturę <nav> i top-bar — czy istnieją bezpośrednie linki do kluczowych sekcji (oferta, o nas, kontakt, blog) z czytelnymi anchor textami.",
        "why": "Crawler buduje site graph z menu głównego; semantyczne anchory są ważnym sygnałem topical hierarchy dla Google i kontekstu dla LLM.",
        "how_to_fix": "Spłaszcz nawigację do 5-7 pozycji z opisowymi anchorami zamiast ogólnych ('Usługi' → 'Audyt SEO', 'Pozycjonowanie B2B')."
    },
    "trust_signals_logos_reviews_numbers": {
        "what": "Skanuje stronę pod kątem konkretnych liczb (lata działalności, klienci, projekty), logotypów klientów oraz cytowanych opinii z atrybucją.",
        "why": "Silny sygnał E-E-A-T (Experience, Trustworthiness) dla Google; LLM-y wykorzystują konkretne metryki jako evidence przy generowaniu odpowiedzi typu 'czy X jest godne zaufania'.",
        "how_to_fix": "Dodaj sekcję z 3-4 metrykami (np. '120 klientów B2B', '8 lat na rynku') oraz pasem logotypów klientów z prawdziwą atrybucją."
    },
    "organization_entity_clearly_stated": {
        "what": "Sprawdza czy nazwa firmy, forma prawna i opis tożsamości są jednoznacznie obecne w treści i wzmocnione schema Organization.",
        "why": "Entity disambiguation w Knowledge Graph i bazach wiedzy LLM wymaga jasnego sygnału — bez tego brand nie zostaje rozpoznany jako encja, tylko jako string.",
        "how_to_fix": "Umieść pełną nazwę i krótki opis firmy w footerze + JSON-LD Organization z legalName, foundingDate i sameAs."
    },
    "contact_info_accessible_from_home": {
        "what": "Wykrywa czy ze strony głównej (header/footer) widoczny jest telefon, e-mail lub link do strony kontaktu w max. jednym kliknięciu.",
        "why": "Sygnał lokalności i wiarygodności dla Google LocalBusiness; AI cytując firmę często szuka kontaktu w surowym HTML jako proof-of-existence.",
        "how_to_fix": "Umieść telefon/e-mail w headerze oraz pełen NAP w footerze z klikalnymi linkami tel:/mailto:."
    },
    "brand_identity_consistent_and_unique": {
        "what": "Ocenia spójność elementów wizualnych i językowych (logo, kolory, tone of voice, naming) oraz unikalność względem szablonowych template'ów.",
        "why": "LLM-y i Google wykorzystują sygnały unikalności brand voice do oceny czy strona reprezentuje realną organizację czy churn-content site.",
        "how_to_fix": "Wprowadź własny brand voice w copy, unikaj stockowych template'ów, zachowaj spójną typografię i naming sekcji w całej domenie."
    },
    "no_generic_marketing_fluff": {
        "what": "Wykrywa nadmiar pustych fraz marketingowych ('innowacyjne rozwiązania', 'najwyższa jakość', 'profesjonalny zespół') bez konkretów.",
        "why": "Google QRG i klasyfikatory LLM penalizują 'thin content' z niską gęstością informacji; AI ekstraktor pomija strony bez konkretnej propozycji.",
        "how_to_fix": "Zamień ogólniki na konkretne dane (technologia, metodologia, liczby, branże) — usuń puste przymiotniki."
    },
    "internal_links_to_services_or_products": {
        "what": "Sprawdza obecność kontekstowych linków wewnętrznych z homepage do podstron usługowych/produktowych z opisowym anchor textem.",
        "why": "Strona główna ma najwyższy PageRank w domenie — linki stąd przekazują autorytet do hubów tematycznych i pomagają crawlerom mapować site structure.",
        "how_to_fix": "Z hero/sekcji oferty linkuj do każdej głównej usługi opisowym anchorem (nie 'czytaj więcej')."
    },
    "external_proof_social_press_awards": {
        "what": "Wykrywa wzmianki o publikacjach prasowych, nagrodach, certyfikatach lub linkach do profili social z atrybucją.",
        "why": "Off-domain validation jest silnym sygnałem E-E-A-T; LLM-y traktują wzmianki w prasie jako weryfikowalne źródła autorytetu marki.",
        "how_to_fix": "Dodaj sekcję 'Jak o nas mówią' z logotypami mediów + outbound linki do oryginalnych publikacji i sameAs w schema Organization."
    },
    # --- FACTOR_META: SERVICE ---
    "clear_offer_or_service_definition": {
        "what": "Sprawdza czy strona usługi zawiera jednoznaczną definicję — co dokładnie zawiera usługa, zakres prac, deliverables.",
        "why": "Klasyfikatory intencji wymagają precyzyjnego mapowania query → service offering; bez definicji RAG nie wybierze strony jako odpowiedzi.",
        "how_to_fix": "Dodaj sekcję 'Co zawiera usługa' z bulletlistą konkretnych komponentów i zakresu prac."
    },
    "benefits_stated_explicitly_not_just_features": {
        "what": "Wykrywa czy strona przekłada cechy techniczne na biznesowe efekty (np. 'audyt 200 URL' → 'zidentyfikujemy luki indeksacji').",
        "why": "LLM-y generując odpowiedzi 'po co X' wymagają jawnie sformułowanych benefitów; same features są ekstraktowane jako spec, nie value-prop.",
        "how_to_fix": "Pod każdą cechą dodaj zdanie 'co to oznacza dla klienta' — przełóż technikalia na rezultat."
    },
    "pricing_or_price_range_indication": {
        "what": "Sprawdza obecność jakiejkolwiek informacji o cenie — kwota, widełki, model rozliczenia (od/do, godzinowo, projektowo).",
        "why": "Cena jest jednym z najczęściej fan-outowanych podzapytań w AI search; brak danych = strona pominięta na rzecz konkurencji z widełkami.",
        "how_to_fix": "Podaj minimum 'od X PLN' lub model rozliczenia oraz JSON-LD Offer z priceRange."
    },
    "use_cases_or_target_customer_defined": {
        "what": "Wykrywa konkretne opisy scenariuszy użycia oraz profilu idealnego klienta (branża, wielkość firmy, problem).",
        "why": "AI personalizując odpowiedzi dopasowuje stronę do profilu pytającego; bez ICP strona nie matchuje do 'X dla mojej branży'.",
        "how_to_fix": "Dodaj sekcję 'Dla kogo' z 3-5 ICP oraz 'Kiedy nas wybrać' z konkretnymi sytuacjami biznesowymi."
    },
    "social_proof_testimonials_clients_case_studies": {
        "what": "Skanuje pod kątem opinii z atrybucją (imię, firma, zdjęcie), logotypów klientów oraz linkowanych case studies z metrykami.",
        "why": "Najsilniejszy sygnał Experience w E-E-A-T; LLM-y używają cytatów klientów jako evidence przy ocenie wiarygodności.",
        "how_to_fix": "Dodaj 3+ opinie z pełną atrybucją oraz linki do case studies z konkretnymi wynikami i Review schema."
    },
    "faq_section_addressing_objections": {
        "what": "Wykrywa sekcję FAQ adresującą realne obiekcje zakupowe, nie generyczne pytania ('Czym się zajmujecie?').",
        "why": "FAQ to najczęściej cytowana sekcja przez AI Overviews i Perplexity (passage-level retrieval); pytania są bezpośrednim matchem dla long-tail queries.",
        "how_to_fix": "Dodaj 5-8 pytań adresujących obiekcje (cena, czas, ryzyko, alternatywy) + FAQPage JSON-LD."
    },
    "clear_primary_cta_to_contact_or_buy": {
        "what": "Sprawdza obecność wyraźnego CTA prowadzącego do konwersji (formularz, zakup, kalendarz) — minimum jeden powyżej i poniżej treści.",
        "why": "Brak CTA klasyfikuje stronę jako informational; intent-mismatch względem transactional queries powoduje obniżenie pozycji.",
        "how_to_fix": "Dodaj jeden dominujący CTA w hero i powtórz na końcu sekcji opisowej oraz sticky bar mobile."
    },
    "differentiation_vs_competition": {
        "what": "Wykrywa jawnie sformułowane wyróżniki — co odróżnia firmę od konkurencji (metodologia, technologia, gwarancje).",
        "why": "LLM-y porównując dostawców szukają unique selling points jako evidence; bez wyróżników strona jest jedną z wielu w komoditowej kategorii.",
        "how_to_fix": "Dodaj sekcję 'Czym się różnimy' z 3-5 konkretnymi punktami (nie 'lepsza jakość' — np. 'własny crawler', 'gwarancja TOP10')."
    },
    "content_substance_over_fluff": {
        "what": "Ocenia gęstość informacyjną treści — stosunek konkretów (dane, metodologia, liczby) do ogólników.",
        "why": "Klasyfikatory thin content i helpful content Google obniżają strony o niskiej density; LLM ekstrakcja pomija strony bez actionable info.",
        "how_to_fix": "Wytnij 30% ogólników i zastąp danymi, procesem, listą deliverables i konkretnymi narzędziami."
    },
    "risk_reversal_guarantee_trial_or_process_clarity": {
        "what": "Wykrywa elementy redukujące ryzyko zakupu — gwarancje, okresy próbne, jasno opisany proces współpracy z etapami.",
        "why": "Sygnał Trustworthiness E-E-A-T; AI cytując ofertę często wyciąga sekcję 'jak wygląda współpraca' jako evidence wiarygodności.",
        "how_to_fix": "Dodaj proces krok-po-kroku (4-6 etapów z czasem) oraz politykę zwrotu/gwarancji lub model billing-as-you-go."
    },
    # --- FACTOR_META: ARTICLE ---
    "author_bio_with_name_and_credentials": {
        "what": "Sprawdza obecność widocznego boxa autora z imieniem, fotografią i credentials (stanowisko, doświadczenie, linki do profili).",
        "why": "Krytyczny sygnał E-E-A-T zwłaszcza dla YMYL; LLM-y przypisują autorytet treści na podstawie atrybucji autora-encji w Knowledge Graph.",
        "how_to_fix": "Dodaj box autora pod H1 z imieniem, jobTitle, linkiem do strony autora oraz JSON-LD author w Article schema."
    },
    "publication_date_visible_inline": {
        "what": "Wykrywa widoczną w treści (nie tylko w meta) datę publikacji w formacie zrozumiałym dla użytkownika i parserów.",
        "why": "Freshness signal dla Google i AI; Perplexity/ChatGPT priorytetyzują źródła z jawnymi datami przy queries time-sensitive.",
        "how_to_fix": "Wstaw datę publikacji pod tytułem ('Opublikowano: 15.03.2026') oraz datePublished w schema Article."
    },
    "last_updated_date_visible": {
        "what": "Sprawdza obecność daty ostatniej aktualizacji odrębnej od publikacji, sygnalizującej maintenance treści.",
        "why": "AI search engines wyraźnie preferują 'updated' content nad 'published'; dateModified jest jednym z silniejszych freshness signals.",
        "how_to_fix": "Dodaj 'Zaktualizowano: DD.MM.RRRR' obok daty publikacji oraz dateModified w Article schema."
    },
    "external_authoritative_citations_with_links": {
        "what": "Wykrywa outbound linki do autorytatywnych źródeł (badania, dokumentacja, oficjalne publikacje) z kontekstowym anchor textem.",
        "why": "Co-citation z autorytatywnymi domenami wzmacnia topical authority; LLM-y traktują strony cytujące źródła jako bardziej trustworthy.",
        "how_to_fix": "Dodaj 3-5 outbound linków do oficjalnych źródeł (Google, dokumentacja, akademickie) z opisowym anchorem."
    },
    "firsthand_experience_or_original_data": {
        "what": "Wykrywa elementy świadczące o własnym doświadczeniu — własne badania, screenshoty, dane z projektów, autorski POV.",
        "why": "Pierwsza litera E w E-E-A-T (Experience) — Google jawnie premiuje content z first-hand evidence nad agregowanym; AI rozróżnia 'experience' od 'rehash'.",
        "how_to_fix": "Dodaj własne dane (wykresy, screeny narzędzi, własne case study) oraz wzmianki o realnych projektach."
    },
    "direct_answer_near_content_start": {
        "what": "Sprawdza czy w pierwszych 100-200 słowach znajduje się bezpośrednia odpowiedź na pytanie z tytułu (TL;DR/lead).",
        "why": "Featured snippets, AI Overviews i Perplexity ekstraktują 40-60 słów z początku treści jako passage; bez direct answer cytowany jest losowy fragment lub konkurent.",
        "how_to_fix": "Dodaj pod H1 lead w formie 'TL;DR' z 2-3 zdaniową odpowiedzią na pytanie z tytułu."
    },
    "scannable_structure_headings_lists_tables": {
        "what": "Analizuje strukturę dokumentu pod kątem podziału na sekcje H2/H3, list, tabel i krótkich akapitów (<150 słów).",
        "why": "Passage indexing Google i chunking w RAG działają na poziomie sekcji; dobrze posegmentowana treść jest częściej cytowana per-passage przez LLM.",
        "how_to_fix": "Podziel długie akapity na sekcje H2 co 200-400 słów, zamień bloki tekstu na listy/tabele tam gdzie zasadne."
    },
    "unique_pov_not_generic_rehash": {
        "what": "Ocenia oryginalność perspektywy — czy treść wnosi nową tezę/argument zamiast powielać top10 SERP.",
        "why": "Google Helpful Content i klasyfikatory LLM penalizują rehash; AI preferuje strony wnoszące unikalny argument do query.",
        "how_to_fix": "Zawrzyj jawną tezę/opinię autora oraz kontr-argument wobec mainstreamowego ujęcia tematu."
    },
    "depth_comprehensive_treatment_of_topic": {
        "what": "Mierzy kompleksowość pokrycia tematu — czy artykuł adresuje główne podpytania i powiązane subtopiki (topical coverage).",
        "why": "Topical authority i query deserves diversity faworyzują głębokie ujęcia; LLM fan-out generuje wiele podzapytań — pełne pokrycie maksymalizuje cytowalność.",
        "how_to_fix": "Zmapuj 'people also ask' i fan-out queries; dodaj sekcje H2 pokrywające każdy podtemat."
    },
    "internal_links_to_related_content": {
        "what": "Sprawdza obecność 3+ kontekstowych linków wewnętrznych do powiązanych artykułów/zasobów z opisowym anchor textem.",
        "why": "Internal linking buduje topical clusters i pillar structure; crawler i LLM mapują relacje tematyczne na podstawie linkowania kontekstowego.",
        "how_to_fix": "Wpleć 3-5 linków wewnętrznych w body do powiązanych artykułów (nie w 'related posts' boxie)."
    },
    # --- FACTOR_META: ABOUT ---
    "founder_or_team_profiles_with_names": {
        "what": "Wykrywa profile członków zespołu z imionami, stanowiskami, zdjęciami i biografiami.",
        "why": "Atrybucja personalna jest silnym sygnałem Authoritativeness; LLM-y budują encje 'osoba w organizacji' z tych danych do later citation.",
        "how_to_fix": "Dodaj sekcję 'Zespół' z imieniem, jobTitle, foto i bio dla każdej kluczowej osoby + Person schema."
    },
    "credentials_certifications_or_qualifications": {
        "what": "Sprawdza wzmianki o certyfikatach (Google, branżowe), wykształceniu, latach doświadczenia członków zespołu.",
        "why": "Authoritativeness w E-E-A-T; weryfikowalne credentials są ekstraktowane przez AI jako evidence kompetencji.",
        "how_to_fix": "Dodaj listę certyfikatów (Google Ads, ISO, branżowych) z logotypami i datami pod profilami osób."
    },
    "company_history_mission_or_founding_story": {
        "what": "Wykrywa narrację o historii powstania firmy, misji lub kluczowych kamieniach milowych.",
        "why": "Buduje encję organizacji w bazach wiedzy LLM (foundingDate, founder); historia jest cytowana przy queries 'kim jest X'.",
        "how_to_fix": "Dodaj sekcję 'Nasza historia' z konkretnym rokiem założenia, motywacją founderów i 3-5 milestone'ami."
    },
    "external_validation_awards_partners_media": {
        "what": "Skanuje pod kątem wzmianek o nagrodach branżowych, partnerstwach technologicznych, wystąpieniach w mediach.",
        "why": "Trzecio-stronna walidacja jest najsilniejszym sygnałem autorytetu; sameAs w schema Organization linkuje encję do oficjalnych źródeł.",
        "how_to_fix": "Dodaj logo partnerów i nagród z linkami do oficjalnych stron + sameAs w JSON-LD Organization."
    },
    "office_location_or_physical_presence": {
        "what": "Sprawdza obecność informacji o fizycznej lokalizacji — adres biura, zdjęcia, mapa, regiony działania.",
        "why": "Sygnał realnej organizacji vs shell company; krytyczne dla LocalBusiness i lokalnych queries w AI.",
        "how_to_fix": "Podaj pełny adres + osadzoną mapę + zdjęcia biura + LocalBusiness JSON-LD z geo coordinates."
    },
    "values_or_real_differentiators": {
        "what": "Wykrywa autentyczne wartości firmowe lub differentiatory (nie generyczne 'zaangażowanie' i 'pasja').",
        "why": "Sygnał unikalności brand identity; LLM-y rozróżniają strony z autentycznym voice od template'owych dzięki konkretnym wartościom.",
        "how_to_fix": "Zamień ogólne wartości na konkretne ('open-sourceujemy nasze narzędzia', 'gwarantujemy SLA 4h') z dowodem realizacji."
    },
    "links_to_linkedin_or_professional_profiles": {
        "what": "Wykrywa outbound linki z profili członków zespołu do LinkedIn lub profili zawodowych.",
        "why": "Cross-platform identity verification dla Google Knowledge Graph; LinkedIn jest jednym z głównych źródeł weryfikacji encji-osób.",
        "how_to_fix": "Dodaj ikonę/link LinkedIn pod każdym profilem zespołu oraz sameAs w Person JSON-LD."
    },
    "real_photos_not_stock_implied": {
        "what": "Ocenia czy zdjęcia zespołu/biura wyglądają na autentyczne — sygnatury stockowe (Unsplash, Shutterstock) są degradujące.",
        "why": "Trustworthiness signal; AI image classifiers i ręczni reviewerzy Google rozpoznają stock photography jako sygnał thin/template site.",
        "how_to_fix": "Zrób sesję zdjęciową zespołu i biura; usuń wszystkie stockowe ilustracje z About."
    },
    "clients_or_projects_showcased": {
        "what": "Sprawdza prezentację konkretnych klientów lub projektów na stronie About — logotypy z atrybucją, linki do case studies.",
        "why": "Experience signal — realne projekty są weryfikowalnym dowodem doświadczenia; AI cytuje portfolio przy queries 'kto robił X'.",
        "how_to_fix": "Dodaj pas logotypów klientów + 3-5 linków do szczegółowych case studies z About."
    },
    "contact_pathway_from_about": {
        "what": "Wykrywa CTA lub link na końcu About prowadzący do kontaktu/oferty, zamykający conversion path.",
        "why": "Page-level conversion intent signal; About bez ścieżki dalej klasyfikowany jako dead-end informational.",
        "how_to_fix": "Dodaj na końcu About wyraźny CTA 'Porozmawiajmy o projekcie' linkujący do kontaktu lub kalendarza."
    },
    # --- FACTOR_META: CONTACT ---
    "nap_name_address_phone_complete_and_visible": {
        "what": "Sprawdza obecność i widoczność pełnego NAP — nazwy firmy, adresu pocztowego i telefonu — w identycznym formacie jak w GBP/rejestrach.",
        "why": "NAP consistency jest fundamentem local SEO i entity resolution; AI verifikuje istnienie firmy crawlując NAP po wielu źródłach.",
        "how_to_fix": "Umieść identyczny NAP na stronie kontakt + footer + LocalBusiness schema; format zgodny z GBP."
    },
    "contact_form_present_and_clear": {
        "what": "Wykrywa obecność funkcjonalnego formularza kontaktowego z polami imię/e-mail/wiadomość i mechanizmem anty-spam.",
        "why": "Conversion enabler; AI klasyfikuje stronę kontakt z formularzem jako fully functional vs link-only.",
        "how_to_fix": "Dodaj <form> z 3-5 polami, reCAPTCHA i stroną dziękującą do trackowania konwersji."
    },
    "opening_hours_visible": {
        "what": "Sprawdza widoczność godzin otwarcia w czytelnym formacie oraz openingHours w JSON-LD.",
        "why": "Krytyczne dla local pack i 'open now' queries w Google/AI; brak danych = exclusion z lokalnych wyników czasowo-zależnych.",
        "how_to_fix": "Podaj godziny w formacie 'Pn-Pt: 9-17' oraz openingHours w LocalBusiness schema."
    },
    "phone_clickable_tel_link": {
        "what": "Wykrywa czy numer telefonu jest opakowany w <a href=\"tel:\"> umożliwiający bezpośrednie połączenie z mobile.",
        "why": "Mobile UX signal i conversion enabler; brakujący tel: zmniejsza mobile call rate (sygnał behawioralny dla Google).",
        "how_to_fix": "Zamień wszystkie numery na <a href=\"tel:+48...\">+48 ...</a>."
    },
    "email_clickable_mailto": {
        "what": "Sprawdza czy adres e-mail jest klikalny przez mailto: link otwierający klienta pocztowego.",
        "why": "UX i sygnał funkcjonalnej strony kontakt; AI parsery preferują klikalne kontakty przy ekstrakcji.",
        "how_to_fix": "Owiń e-mail w <a href=\"mailto:...\">."
    },
    "map_or_embedded_location": {
        "what": "Wykrywa osadzoną mapę Google/OSM lub statyczny obraz lokalizacji z geocoordinates.",
        "why": "Visual proof of location dla local SEO; geo signal wzmacnia LocalBusiness entity w Knowledge Graph.",
        "how_to_fix": "Osadź Google Maps iframe lub statyczny snapshot oraz geo (lat/long) w LocalBusiness JSON-LD."
    },
    "multiple_contact_channels": {
        "what": "Sprawdza obecność więcej niż jednego kanału kontaktu (telefon + e-mail + formularz + chat/messenger).",
        "why": "Accessibility i trustworthiness signal; brak alternatyw klasyfikuje firmę jako trudną do osiągnięcia.",
        "how_to_fix": "Dodaj minimum 3 kanały: tel, e-mail, formularz; opcjonalnie chat lub messenger."
    },
    "department_or_role_specific_contacts": {
        "what": "Wykrywa rozdzielenie kontaktów per dział/rola (sprzedaż, support, prasa) zamiast jednego ogólnego adresu.",
        "why": "Sygnał skali organizacji i profesjonalnej struktury; pomaga AI routować zapytania do właściwego punktu kontaktu.",
        "how_to_fix": "Wydziel sekcje 'Sprzedaż', 'Wsparcie', 'Prasa' z dedykowanymi adresami i osobami kontaktowymi."
    },
    "response_time_expectation": {
        "what": "Sprawdza obecność jawnej informacji o spodziewanym czasie odpowiedzi (np. 'odpowiadamy w 24h').",
        "why": "Expectation setting jako sygnał profesjonalizmu; AI w odpowiedziach typu 'jak szybko skontaktować się z X' wykorzystuje tę daną.",
        "how_to_fix": "Dodaj zdanie 'Odpowiadamy w ciągu X godzin roboczych' przy formularzu i adresach e-mail."
    },
    "physical_office_photo_or_proof": {
        "what": "Wykrywa zdjęcie biura, wnętrza lub fasady budynku jako wizualny dowód fizycznej obecności.",
        "why": "Local proof signal — odróżnia faktyczną lokalizację od adresu wirtualnego; istotne dla LocalBusiness ranking.",
        "how_to_fix": "Dodaj 1-3 prawdziwe zdjęcia biura (zewnętrze lub recepcja) na stronie kontakt."
    },
    # --- FACTOR_META: CATEGORY ---
    "meaningful_category_intro_copy_not_thin": {
        "what": "Sprawdza obecność opisowego wstępu kategorii (200-500 słów) definiującego zakres tematyczny, nie samej listy produktów.",
        "why": "Bez intro kategoria jest klasyfikowana jako thin content; LLM-y nie mają contextu do mapowania query → category.",
        "how_to_fix": "Dodaj 200-400 słów intro nad listingiem z definicją kategorii, kluczowymi cechami i przewodnikiem zakupowym."
    },
    "unique_category_h1_and_title": {
        "what": "Wykrywa czy H1 i <title> kategorii są unikalne w obrębie domeny i opisowe (nie 'Produkty' lub 'Kategoria 1').",
        "why": "Title/H1 to najsilniejszy on-page signal; duplikaty powodują keyword cannibalization i obniżenie rankingu wszystkich kanibalizujących URL.",
        "how_to_fix": "Ustaw unikalny H1 z nazwą kategorii i modyfikatorem ('Buty trekkingowe damskie') oraz spójny title."
    },
    "category_specific_meta_description": {
        "what": "Sprawdza obecność unikalnej meta description dla danej kategorii, opisującej zakres oferty i CTA.",
        "why": "Wpływa na CTR z SERP; AI Overviews wykorzystują meta description jako jeden z preview signals.",
        "how_to_fix": "Napisz dedykowaną meta description 120-160 znaków z liczbą produktów, kluczowymi markami i value-prop."
    },
    "internal_links_to_items_with_context": {
        "what": "Wykrywa czy linki do produktów/elementów listy mają opisowy anchor i kontekstowe modyfikatory, nie tylko miniaturkę.",
        "why": "Anchor text jest sygnałem topical relevance dla docelowego URL; crawler dystrybuuje autorytet kategorii do produktów przez kontekstowe linkowanie.",
        "how_to_fix": "Upewnij się że każdy element listy ma tekstowy anchor z pełną nazwą produktu, nie tylko klikalne foto."
    },
    "filters_or_facets_if_applicable": {
        "what": "Sprawdza obecność funkcjonalnych filtrów/fasetów na listingu kategorii — cena, atrybuty, marka.",
        "why": "UX i dłuższe sesje (sygnał behawioralny); jednocześnie wymaga kontroli indeksacji aby nie generować thin/duplicate URL.",
        "how_to_fix": "Dodaj filtry per atrybut produktu + canonical/noindex dla kombinacji fasetowych aby uniknąć duplicate content."
    },
    "pagination_or_load_more_sensible": {
        "what": "Wykrywa obecność mechanizmu paginacji (rel=next/prev lub crawlowalne URL z ?page=N) dla długich list.",
        "why": "Crawler musi dotrzeć do produktów na dalszych stronach; infinite scroll bez fallbackowego URL ukrywa głębsze produkty przed botami.",
        "how_to_fix": "Wprowadź crawlowalne URL paginacji (/?page=2) z linkami w HTML lub progressive enhancement przy infinite scroll."
    },
    "subcategory_links_exposed": {
        "what": "Sprawdza widoczność linków do podkategorii z poziomu kategorii nadrzędnej — bezpośrednio w HTML, nie tylko w menu.",
        "why": "Hierarchia kategoryjna buduje topical clusters i przekazuje PageRank w głąb taksonomii.",
        "how_to_fix": "Dodaj sekcję 'Podkategorie' pod intro z linkami do każdej podkategorii i krótkim opisem."
    },
    "no_boilerplate_content_duplicated": {
        "what": "Wykrywa powtarzające się szablonowe bloki tekstu identyczne na wielu kategoriach (boilerplate).",
        "why": "Boilerplate jest filtrowany przez Google jako near-duplicate content i obniża wartość unikalnej treści na stronie.",
        "how_to_fix": "Zamień powtarzalne bloki na unikalny content per kategoria lub wytnij je ze strony."
    },
    "visual_hierarchy_for_scannability": {
        "what": "Ocenia czytelność listingu — siatka produktów, jasna typografia, kontrast, separacja sekcji.",
        "why": "UX/dwell time signal dla Google; LLM-y nie analizują wizualnie, ale dobra struktura HTML koreluje z accessibility.",
        "how_to_fix": "Zastosuj spójny grid produktów, czytelną typografię z hierarchią i wyraźną separację sekcji."
    },
    "related_categories_linked": {
        "what": "Wykrywa linki do powiązanych kategorii na końcu lub w sidebarze listingu.",
        "why": "Buduje horyzontalne powiązania w taxonomy graph; pomaga crawlerowi i LLM zrozumieć relacje siblings w katalogu.",
        "how_to_fix": "Dodaj sekcję 'Powiązane kategorie' z 4-6 linkami do siblings i komplementarnych kategorii."
    },
    # --- FACTOR_META: OTHER ---
    "clear_page_purpose_stated": {
        "what": "Sprawdza czy strona w pierwszej sekcji jednoznacznie komunikuje swój cel i odbiorcę.",
        "why": "Klasyfikatory intencji wymagają jasnego sygnału; ambivalent purpose powoduje misclassification i niski match score do query.",
        "how_to_fix": "W pierwszym akapicie pod H1 określ jednym zdaniem 'po co' istnieje ta podstrona i 'dla kogo'."
    },
    "value_for_user_evident": {
        "what": "Ocenia czy user value (informacja, narzędzie, rozwiązanie) jest widoczna od razu, bez konieczności scrollowania.",
        "why": "Helpful Content System Google penalizuje strony bez jasnej wartości; AI ekstraktor odrzuca strony bez evidentnego value-prop.",
        "how_to_fix": "Umieść główną wartość strony (insight, tool, answer) above the fold."
    },
    "heading_hierarchy_correct": {
        "what": "Sprawdza poprawną hierarchię nagłówków — jeden H1, sekwencyjnie zagnieżdżone H2/H3 bez skoków poziomów.",
        "why": "Hierarchia nagłówków odpowiada za document outline używany przez accessibility, passage indexing i RAG chunking.",
        "how_to_fix": "Zostaw jeden H1, ułóż H2 jako sekcje główne, H3 jako podpunkty; eliminuj skoki H1→H4."
    },
    "meta_description_descriptive_and_unique": {
        "what": "Wykrywa obecność unikalnej dla URL meta description w przedziale 120-160 znaków, opisującej treść.",
        "why": "Wpływa na CTR z SERP; chociaż nie jest ranking factorem direct, brak/duplikaty obniżają widoczność w wynikach.",
        "how_to_fix": "Napisz unikalną meta description 120-160 znaków z value-prop i nieoficjalnym CTA."
    },
    "scannable_structure_lists_or_subheadings": {
        "what": "Sprawdza obecność list, podtytułów i krótkich akapitów dzielących treść na łatwo skanowalne sekcje.",
        "why": "Passage retrieval i RAG chunking preferują dobrze posegmentowane dokumenty; AI cytuje sekcje per H2/list.",
        "how_to_fix": "Podziel długie bloki na listy bullet/numerowane oraz sekcje H2 co 200-400 słów."
    },
    "appropriate_schema_for_content_type": {
        "what": "Sprawdza czy typ schema.org (Article, Product, Service, FAQPage) odpowiada faktycznemu typowi treści strony.",
        "why": "Schema disambiguuje typ contentu dla crawlera i pozwala uzyskać rich results; mismatch może być filtrowany jako spam.",
        "how_to_fix": "Dobierz schema do typu strony: Article→blog, Service→oferty, Product→produkty, FAQPage→FAQ."
    },
    "internal_links_to_contextual_content": {
        "what": "Wykrywa linki wewnętrzne do tematycznie powiązanych treści z kontekstowym anchor textem (nie 'kliknij tutaj').",
        "why": "Buduje topical clusters i przekazuje autorytet; pomaga AI mapować relacje semantyczne w domenie.",
        "how_to_fix": "Wpleć 3-5 kontekstowych linków wewnętrznych w body z opisowymi anchorami."
    },
    "no_generic_ai_generated_content": {
        "what": "Wykrywa cechy generycznego AI-generated contentu — szablonowe frazy, brak konkretnych przykładów, jednakowa struktura sekcji.",
        "why": "Google Spam Update i SpamBrain klasyfikują scaled content abuse; LLM-y rozpoznają własne template'y i obniżają cytowalność.",
        "how_to_fix": "Dodaj osobiste przykłady, własne dane, autentyczny POV; przepisz template'owe sekcje na unikalny voice."
    },
    "external_sources_or_proof_where_relevant": {
        "what": "Sprawdza obecność outbound linków do źródeł lub dowodów potwierdzających fakty i twierdzenia w treści.",
        "why": "Trustworthiness signal i co-citation z autorytetami; AI woli cytować strony które same cytują weryfikowalne źródła.",
        "how_to_fix": "Linkuj do oficjalnych źródeł (dokumentacja, badania, statystyki) przy każdym kluczowym twierdzeniu."
    },
    "clear_next_step_or_cta": {
        "what": "Wykrywa obecność jasnej akcji do podjęcia na końcu strony — CTA, link do powiązanego zasobu, formularz.",
        "why": "Conversion path completion; brak next step generuje dead-end signal i niski dwell time.",
        "how_to_fix": "Dodaj na końcu strony jednoznaczny CTA lub link do logicznie następnego kroku w user journey."
    },
    # --- TECH_FACTOR_META ---
    "meta_title_present": {
        "what": "Weryfikuje obecność tagu <title> w <head> z niepustą zawartością w przedziale 30-65 znaków.",
        "why": "Title jest najsilniejszym on-page rankingiem; jego brak powoduje że Google sam generuje fallback (zwykle gorszy) z H1 lub treści.",
        "how_to_fix": "Dodaj <title>Główne słowo kluczowe — Marka</title> w <head>, 50-60 znaków."
    },
    "meta_description": {
        "what": "Sprawdza obecność meta description w <head> w długości 120-160 znaków.",
        "why": "Mimo że nie jest direct ranking factor, wpływa na CTR z SERP i jest używana przez AI search engines jako preview.",
        "how_to_fix": "Dodaj <meta name=\"description\" content=\"…\"> z value-prop, 120-160 znaków."
    },
    "canonical_tag": {
        "what": "Wykrywa obecność <link rel=\"canonical\"> wskazującego preferowany URL kanoniczny strony.",
        "why": "Eliminuje duplicate content z parametrów, trailing slash, http/https; konsoliduje sygnały rankingowe do jednego URL.",
        "how_to_fix": "Dodaj <link rel=\"canonical\" href=\"https://domena.pl/url/\"> w <head> z pełnym absolute URL."
    },
    "h1_single": {
        "what": "Sprawdza czy strona zawiera dokładnie jeden tag H1 z głównym tematem.",
        "why": "H1 jest głównym sygnałem o czym jest strona; multiple H1 rozmywają fokus i utrudniają klasyfikację.",
        "how_to_fix": "Pozostaw jeden H1 z głównym tematem, pozostałe semantyczne nagłówki zamień na H2/H3."
    },
    "heading_hierarchy": {
        "what": "Analizuje sekwencję nagłówków H1-H6 pod kątem braku skoków poziomów (np. H1→H4) i poprawnego zagnieżdżenia.",
        "why": "Hierarchia nagłówków odpowiada za document outline używany przez accessibility tools, passage indexing i chunking w RAG.",
        "how_to_fix": "Ułóż nagłówki sekwencyjnie H1→H2→H3 bez skoków poziomów."
    },
    "og_tags": {
        "what": "Wykrywa obecność tagów Open Graph (og:title, og:description, og:image, og:url) w <head>.",
        "why": "Steruje wyglądem strony przy udostępnianiu w social media i messengerach; brak generuje fallback z często niewłaściwym preview.",
        "how_to_fix": "Dodaj og:title, og:description, og:image (min 1200x630), og:url w <head>."
    },
    "viewport_meta": {
        "what": "Sprawdza obecność <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">.",
        "why": "Krytyczne dla mobile-friendliness; brak powoduje że strona jest renderowana w trybie desktop na mobile (skalowana), co psuje UX i mobile ranking.",
        "how_to_fix": "Dodaj <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> w <head>."
    },
    "lang_attribute": {
        "what": "Wykrywa atrybut lang w tagu <html> określający język strony (np. lang=\"pl\").",
        "why": "Sygnał dla crawlerów, screen readerów i tłumaczy o języku contentu; wpływa na geo-targeting i accessibility.",
        "how_to_fix": "Ustaw <html lang=\"pl\"> (lub odpowiedni ISO code)."
    },
    "image_alt_coverage": {
        "what": "Mierzy procent obrazów <img> z wypełnionym atrybutem alt — opisującym treść lub pustym dla dekoracyjnych.",
        "why": "Accessibility (screen readers), image search ranking, oraz semantyczny kontekst obrazu dla AI multimodalnych modeli.",
        "how_to_fix": "Dodaj alt do każdego <img>: opisowy dla content images (5-12 słów), alt=\"\" dla dekoracji."
    },
    "semantic_html5_tags": {
        "what": "Wykrywa użycie semantycznych tagów HTML5 (<main>, <article>, <section>, <nav>, <header>, <footer>) zamiast <div>.",
        "why": "Daje crawlerowi i parserom AI eksplicytny document structure ułatwiający rozpoznanie funkcji bloków (boilerplate vs main content).",
        "how_to_fix": "Zamień strukturalne <div> na semantyczne tagi <main>, <article>, <section>, <nav>, <header>, <footer>."
    },
    "response_size_ok": {
        "what": "Sprawdza czy rozmiar HTML response mieści się w rozsądnych granicach (zwykle ≤200-300 KB).",
        "why": "Duży HTML spowalnia parsing, render i crawl budget; bloated markup często sygnalizuje słabej jakości template.",
        "how_to_fix": "Usuń inline CSS/JS, komentarze i nieużywany markup; włącz minifikację i kompresję."
    },
    "organization_schema": {
        "what": "Wykrywa JSON-LD typu Organization z polami name, url, logo, sameAs.",
        "why": "Definiuje encję organizacji dla Knowledge Graph; sameAs łączy z oficjalnymi profilami social/LinkedIn do entity verification.",
        "how_to_fix": "Dodaj JSON-LD Organization z name, url, logo, sameAs (LinkedIn, GBP, social)."
    },
    "website_schema": {
        "what": "Wykrywa JSON-LD typu WebSite z polami name, url i opcjonalnie potentialAction (SearchAction).",
        "why": "Pomaga AI rozpoznać markę i włączyć sitelinks search box w SERP.",
        "how_to_fix": "Dodaj JSON-LD WebSite z name, url i potentialAction SearchAction."
    },
    "any_schema": {
        "what": "Sprawdza obecność jakiegokolwiek poprawnego schema.org JSON-LD na stronie.",
        "why": "Schema disambiguuje typ contentu i encje dla AI; strony bez schemy są klasyfikowane wyłącznie heurystycznie z HTML.",
        "how_to_fix": "Dodaj minimum jeden JSON-LD pasujący do typu strony (Organization, Article, Service, Product)."
    },
    "product_or_service_schema": {
        "what": "Wykrywa JSON-LD typu Product lub Service z polami name, description, offers/provider.",
        "why": "Umożliwia rich results dla ofert (cena, dostępność) i jednoznacznie klasyfikuje stronę jako transactional.",
        "how_to_fix": "Dodaj Service/Product JSON-LD z name, description, provider, areaServed, offers (price/priceCurrency)."
    },
    "faq_schema_bonus": {
        "what": "Wykrywa JSON-LD FAQPage z parami pytanie-odpowiedź odpowiadającymi widocznej treści.",
        "why": "Bardzo często ekstraktowane przez AI Overviews i Perplexity jako bezpośrednie odpowiedzi na long-tail queries.",
        "how_to_fix": "Dodaj FAQPage JSON-LD odzwierciedlający widoczne FAQ (nie ukryte — to violation guidelines)."
    },
    "breadcrumb_schema": {
        "what": "Sprawdza obecność JSON-LD BreadcrumbList z sekwencją position/name/item od korzenia do bieżącej strony.",
        "why": "Wzbogaca SERP o breadcrumb path zamiast URL, podnosząc CTR; pomaga crawlerowi mapować taxonomy.",
        "how_to_fix": "Dodaj BreadcrumbList JSON-LD odzwierciedlający faktyczną hierarchię nawigacyjną."
    },
    "article_schema": {
        "what": "Wykrywa JSON-LD typu Article lub BlogPosting z headline, author, datePublished, dateModified.",
        "why": "Umożliwia Top Stories, Discover i article rich results; krytyczne dla atrybucji autora-encji.",
        "how_to_fix": "Dodaj Article/BlogPosting JSON-LD z headline, author, datePublished, dateModified, image, publisher."
    },
    "schema_author_field": {
        "what": "Sprawdza obecność pola author w Article schema z typem Person i wypełnionym name/url.",
        "why": "Najsilniejszy sygnał atrybucji autora dla E-E-A-T; bez tego pola Article schema jest niekompletna.",
        "how_to_fix": "Uzupełnij \"author\": {\"@type\":\"Person\",\"name\":\"…\",\"url\":\"…\"} w Article JSON-LD."
    },
    "schema_dates": {
        "what": "Wykrywa obecność datePublished i dateModified w Article schema w formacie ISO 8601.",
        "why": "Freshness signal jawnie deklarowany; AI search engines wyraźnie premiują content z poprawnymi datami.",
        "how_to_fix": "Dodaj datePublished i dateModified w formacie ISO 8601 (YYYY-MM-DD) do Article JSON-LD."
    },
    "person_schema_team": {
        "what": "Wykrywa JSON-LD typu Person dla członków zespołu z name, jobTitle, sameAs.",
        "why": "Buduje encje-osoby w Knowledge Graph powiązane z Organization; krytyczne dla atrybucji eksperckiej w E-E-A-T.",
        "how_to_fix": "Dla każdej osoby zespołu dodaj Person JSON-LD z name, jobTitle, sameAs (LinkedIn), worksFor."
    },
    "localbusiness_or_organization_schema": {
        "what": "Sprawdza obecność JSON-LD LocalBusiness lub Organization z address, telephone, openingHours, geo.",
        "why": "Krytyczne dla local pack ranking i lokalnych queries w AI; geo coordinates łączą encję z mapą Google.",
        "how_to_fix": "Dodaj LocalBusiness JSON-LD z PostalAddress, telephone, openingHours, geo (latitude/longitude)."
    },
    "tel_link_present": {
        "what": "Wykrywa minimum jeden link <a href=\"tel:\"> umożliwiający bezpośrednie wybranie numeru na urządzeniu mobilnym.",
        "why": "Mobile UX i conversion enabler; krytyczne dla local businesses zależnych od call tracking.",
        "how_to_fix": "Zamień telefony w treści na <a href=\"tel:+48123456789\">."
    },
    "mailto_link_present": {
        "what": "Wykrywa minimum jeden link <a href=\"mailto:\"> otwierający klienta pocztowego.",
        "why": "UX signal i sygnał funkcjonalnej strony kontaktowej.",
        "how_to_fix": "Zamień e-maile w treści na <a href=\"mailto:...\">."
    },
    "contact_form_present": {
        "what": "Wykrywa obecność tagu <form> z polami input służącego do kontaktu.",
        "why": "Zwiększa konwersję i sygnalizuje funkcjonalność strony; AI rozpoznaje funkcjonalność strony kontakt na podstawie obecności form.",
        "how_to_fix": "Dodaj <form> z imię, e-mail, wiadomość + reCAPTCHA i stroną dziękującą."
    },
    "itemlist_schema": {
        "what": "Wykrywa JSON-LD typu ItemList z position/url/name dla elementów listy (produkty, kategoria, zespół).",
        "why": "Pozwala AI rozpoznać typ strony listingowej i prawidłowo paginować/cytować pojedyncze elementy.",
        "how_to_fix": "Dodaj ItemList JSON-LD z position/url/name każdego elementu na listingu."
    },
    # --- DOMAIN_TECH_META ---
    "robots_txt_accessible": {
        "what": "Sprawdza czy /robots.txt zwraca HTTP 200 z poprawnym text/plain i parsowalnymi dyrektywami.",
        "why": "Pierwszy plik odwiedzany przez crawlery; brak/błąd 500 może blokować całą domenę lub powodować nieoptymalny crawl.",
        "how_to_fix": "Udostępnij /robots.txt zwracający HTTP 200 z minimalną zawartością User-agent + Sitemap."
    },
    "gptbot_not_blocked": {
        "what": "Weryfikuje czy User-agent: GPTBot nie jest zablokowany przez Disallow: / w robots.txt.",
        "why": "GPTBot odpowiada za crawl dla ChatGPT i indeksu OpenAI; jego zablokowanie wyłącza domenę z citation pool ChatGPT.",
        "how_to_fix": "Usuń z robots.txt User-agent: GPTBot z Disallow: / lub zmień na Allow."
    },
    "perplexitybot_not_blocked": {
        "what": "Sprawdza czy User-agent: PerplexityBot nie jest blokowany w robots.txt.",
        "why": "PerplexityBot indeksuje dla Perplexity.ai — jednego z największych AI search engines; blokada eliminuje cytowanie.",
        "how_to_fix": "Usuń z robots.txt User-agent: PerplexityBot z Disallow: /."
    },
    "claudebot_not_blocked": {
        "what": "Weryfikuje czy ClaudeBot (oraz anthropic-ai) nie są zablokowane w robots.txt.",
        "why": "ClaudeBot odpowiada za crawl dla Claude i Anthropic; blokada wyłącza domenę z bazy wiedzy Claude.",
        "how_to_fix": "Usuń z robots.txt User-agent: ClaudeBot oraz anthropic-ai z Disallow: /."
    },
    "google_extended_not_blocked": {
        "what": "Sprawdza czy User-agent: Google-Extended nie jest blokowany w robots.txt.",
        "why": "Google-Extended steruje wykorzystaniem treści w Gemini i AI Overviews — blokada wyłącza z AI features Google przy zachowaniu klasycznego rankingu.",
        "how_to_fix": "Usuń z robots.txt User-agent: Google-Extended z Disallow: / aby pojawiać się w AI Overviews."
    },
    "crawl_delay_ok": {
        "what": "Sprawdza wartość dyrektywy Crawl-delay w robots.txt — czy nie jest absurdalnie wysoka (>10s).",
        "why": "Wysoki crawl-delay ogranicza crawl rate i sygnalizuje słabą infrastrukturę; Googlebot ignoruje, ale inne boty respektują.",
        "how_to_fix": "Usuń lub obniż Crawl-delay do ≤10 w robots.txt."
    },
    "sitemap_present": {
        "what": "Wykrywa obecność pliku sitemap.xml (na /sitemap.xml lub zgłoszonego w robots/GSC) z poprawną strukturą XML.",
        "why": "Sitemap przyspiesza discovery i indeksację — krytyczne dla dużych serwisów i nowych URL.",
        "how_to_fix": "Wygeneruj sitemap.xml (Yoast/RankMath/Screaming Frog), opublikuj pod /sitemap.xml i zgłoś w GSC."
    },
    "sitemap_in_robots": {
        "what": "Sprawdza czy robots.txt zawiera dyrektywę Sitemap: wskazującą lokalizację sitemap.xml.",
        "why": "Pozwala crawlerom (zwłaszcza tym, które nie używają GSC) automatycznie wykryć sitemap.",
        "how_to_fix": "Dodaj na końcu robots.txt: Sitemap: https://domena.pl/sitemap.xml."
    },
    "llms_txt_present": {
        "what": "Wykrywa plik /llms.txt zgodny ze standardem llmstxt.org — markdown z TOC kluczowych URL, opisem firmy, licencją.",
        "why": "Standard pozwala LLM-om efektywnie pobrać curated knowledge o firmie bez crawlowania całej domeny; jeden z najsilniejszych aktualnie AI SEO sygnałów.",
        "how_to_fix": "Stwórz /llms.txt z markdown TOC najważniejszych URL, opisem firmy i licencją zgodnie z llmstxt.org."
    },
    "https_enabled": {
        "what": "Sprawdza czy domena obsługuje HTTPS z ważnym certyfikatem SSL/TLS i przekierowuje HTTP→HTTPS.",
        "why": "Confirmed ranking factor Google; brak HTTPS powoduje 'Not Secure' w przeglądarce i drastyczne obniżenie zaufania.",
        "how_to_fix": "Zainstaluj certyfikat (np. Let's Encrypt) i wymusz redirect 301 HTTP→HTTPS na serwerze."
    },
    "hreflang_used": {
        "what": "Wykrywa tagi <link rel=\"alternate\" hreflang=\"…\"> dla wersji językowych/regionalnych strony.",
        "why": "Kluczowe dla geo-targetingu i unikania duplicate content między wersjami językowymi; bez hreflang Google sam wybiera 'kanoniczną' wersję.",
        "how_to_fix": "Dodaj <link rel=\"alternate\" hreflang=\"…\" href=\"…\"> per język + hreflang=\"x-default\" — tylko jeśli masz wersje językowe."
    },
    "hsts_enabled": {
        "what": "Sprawdza obecność nagłówka serwera Strict-Transport-Security z max-age.",
        "why": "Wymusza HTTPS w przeglądarce nawet przy pierwszym wejściu (po preload); chroni przed SSL stripping i wzmacnia security signal.",
        "how_to_fix": "Dodaj header Strict-Transport-Security: max-age=31536000; includeSubDomains."
    },
    "compression_enabled": {
        "what": "Weryfikuje czy serwer kompresuje odpowiedzi (Content-Encoding: gzip lub brotli).",
        "why": "Kompresja redukuje TTFB i payload o 60-80%, bezpośrednio wpływając na Core Web Vitals i crawl efficiency.",
        "how_to_fix": "Włącz gzip lub brotli na serwerze (nginx: gzip on / brotli on; Apache: mod_deflate)."
    },
    # --- PERFORMANCE_FACTOR_META ---
    "performance_score_mobile": {
        "what": "Łączna ocena Lighthouse Performance (0-100) dla wersji mobilnej, agregująca LCP, CLS, TBT, FCP, SI, TTI.",
        "why": "Mobile-first indexing Google używa wydajności mobilnej jako rankingowego signalu; niski score koreluje z wysokim bounce rate.",
        "how_to_fix": "Optymalizuj wszystkie Core Web Vitals jednocześnie — kompresja obrazów (WebP/AVIF), lazy-load, deferred JS, krytyczny CSS inline."
    },
    "lcp_mobile_ok": {
        "what": "Mierzy Largest Contentful Paint — czas wczytania największego widocznego elementu (zwykle hero image/heading); cel <2.5s.",
        "why": "Core Web Vital — confirmed ranking factor; reprezentuje perceived load speed kluczową dla mobile UX.",
        "how_to_fix": "Preload hero image, użyj WebP/AVIF, dodaj fetchpriority=\"high\", optymalizuj TTFB serwera, eliminuj render-blocking resources."
    },
    "cls_mobile_ok": {
        "what": "Mierzy Cumulative Layout Shift — sumę nieoczekiwanych przesunięć layoutu w czasie sesji; cel <0.1.",
        "why": "Core Web Vital — wysokie CLS powoduje misclick'i i frustrację, jest karane w mobile ranking.",
        "how_to_fix": "Ustaw width/height na <img>/<video>, rezerwuj miejsce dla embedów i reklam, unikaj wstrzykiwania DOM nad fold."
    },
    "tbt_mobile_ok": {
        "what": "Mierzy Total Blocking Time — sumę czasu blokowania głównego wątku przez długie zadania JS między FCP a TTI; cel <200ms.",
        "why": "Proxy dla INP (Interaction to Next Paint) — Core Web Vital decydujący o reaktywności strony.",
        "how_to_fix": "Code-splitting, defer/async non-critical JS, web workers do ciężkich obliczeń, eliminuj third-party scripts."
    },
    "fcp_mobile_ok": {
        "what": "Mierzy First Contentful Paint — czas do renderu pierwszego elementu treści (tekst, obraz); cel <1.8s.",
        "why": "Pierwsze wrażenie performance; wpływa na bounce rate i sygnalizuje czy strona w ogóle 'się ładuje'.",
        "how_to_fix": "Inline critical CSS, preconnect do third-party origins, optymalizuj TTFB, eliminuj render-blocking resources."
    },
}


PERFORMANCE_FACTOR_WHY = {
    "performance_score_mobile": "Łączna ocena Lighthouse — wpływa na ranking mobilny i UX.",
    "lcp_mobile_ok": "Czas wczytania największego elementu. Core Web Vitals.",
    "cls_mobile_ok": "Stabilność layoutu — skoki treści w trakcie ładowania psują UX.",
    "tbt_mobile_ok": "Czas blokowania głównego wątku przez JS. Wpływa na interaktywność.",
    "fcp_mobile_ok": "Czas do pierwszego renderu — pierwsze wrażenie użytkownika.",
}
SCORE_VALUE_MAP = {0: 0.0, 1: 0.35, 2: 1.0}

SCHEMA_FACTOR_IDS = {
    "appropriate_schema_for_content_type",
    "organization_schema",
    "website_schema",
    "any_schema",
    "product_or_service_schema",
    "faq_schema_bonus",
    "breadcrumb_schema",
    "article_schema",
    "schema_author_field",
    "schema_dates",
    "person_schema_team",
    "localbusiness_or_organization_schema",
    "itemlist_schema",
}

PAGE_TECH_APPLIES_TO = {
    "meta_title_present": ["homepage", "service", "article", "about", "contact", "category", "other"],
    "meta_description": ["homepage", "service", "article", "about", "contact", "category", "other"],
    "canonical_tag": ["homepage", "service", "article", "about", "contact", "category", "other"],
    "h1_single": ["homepage", "service", "article", "about", "contact", "category", "other"],
    "heading_hierarchy": ["homepage", "service", "article", "about", "contact", "category", "other"],
    "og_tags": ["homepage", "service", "article", "about", "contact", "category", "other"],
    "viewport_meta": ["homepage", "service", "article", "about", "contact", "category", "other"],
    "lang_attribute": ["homepage", "service", "article", "about", "contact", "category", "other"],
    "image_alt_coverage": ["homepage", "service", "article", "about", "contact", "category", "other"],
    "semantic_html5_tags": ["homepage", "service", "article", "about", "contact", "category", "other"],
    "response_size_ok": ["homepage", "service", "article", "about", "contact", "category", "other"],
    "organization_schema": ["homepage", "about"],
    "website_schema": ["homepage"],
    "any_schema": ["homepage", "category", "other"],
    "product_or_service_schema": ["service"],
    "faq_schema_bonus": ["service", "article"],
    "breadcrumb_schema": ["service", "article", "about", "category", "other"],
    "article_schema": ["article"],
    "schema_author_field": ["article"],
    "schema_dates": ["article"],
    "person_schema_team": ["about"],
    "localbusiness_or_organization_schema": ["contact"],
    "tel_link_present": ["contact"],
    "mailto_link_present": ["contact"],
    "contact_form_present": ["contact"],
    "itemlist_schema": ["category"],
}


def _clamp_score(value: int, low: int = 1, high: int = 3) -> int:
    return max(low, min(high, value))


def score_value(score: int | float) -> float:
    return SCORE_VALUE_MAP.get(int(score or 0), 0.0)


def _content_applies_to(factor_id: str) -> list[str]:
    page_types = [
        page_type
        for page_type, spec in PAGE_TYPE_FACTORS.items()
        if factor_id in spec.get("factors", [])
    ]
    if page_types:
        return page_types
    patent_types = [
        page_type
        for page_type, factor_ids in PATENT_PAGE_TYPE_FACTORS.items()
        if factor_id in factor_ids
    ]
    return patent_types or ["homepage", "service", "article", "about", "contact", "category", "other"]


def _ui_group_for_factor(factor_id: str, meta: dict | None = None, *, is_tech: bool = False, is_domain: bool = False, is_performance: bool = False) -> str:
    meta = meta or {}
    if is_performance or meta.get("category") == "performance":
        return "performance"
    if meta.get("source") == "google_patent":
        return "patents"
    if factor_id in SCHEMA_FACTOR_IDS or "schema" in factor_id:
        return "technical"
    if is_tech or is_domain:
        return "technical"

    category = meta.get("category", "")
    if category == "eeat":
        return "eeat"
    if category in {"geo", "rag"}:
        return "ai_aeo"
    if category == "patent":
        return "patents"
    return "onpage"


def _impact_effort_for_factor(factor_id: str, group: str, meta: dict | None = None, *, is_tech: bool = False, is_domain: bool = False) -> tuple[int, int]:
    meta = meta or {}
    impact = 2
    effort = 2

    if group == "patents":
        impact = 3 if meta.get("confidence") == "high" else 2
        effort = 2 if meta.get("seo_inference_level") == "direct" else 3
    elif group == "eeat":
        impact = 3
        effort = 2
    elif group == "ai_aeo":
        impact = 3 if factor_id in {"direct_answer_near_content_start", "citable-fragment-density"} else 2
        effort = 2
    elif group == "technical":
        if factor_id in SCHEMA_FACTOR_IDS or "schema" in factor_id:
            impact = 3
            effort = 1
        else:
            impact = 3 if factor_id in CRITICAL_FACTOR_PENALTIES or is_domain else 2
            effort = 1
    elif group == "performance":
        impact = 3 if factor_id in {"performance_score_mobile", "lcp_mobile_ok", "cls_mobile_ok"} else 2
        effort = 3
    else:
        impact = 2
        effort = 2

    high_effort_tokens = ("depth", "original", "external", "citations", "case", "comprehensive", "differentiation")
    low_effort_tokens = ("date", "cta", "title", "h1", "mailto", "tel", "viewport", "lang", "canonical")
    if any(token in factor_id for token in high_effort_tokens):
        effort = max(effort, 3)
    if any(token in factor_id for token in low_effort_tokens):
        effort = min(effort, 1)

    impact_final, effort_final = _clamp_score(impact), _clamp_score(effort)
    # Nadpisanie dla czynników o najsłabszym impacie — celowo poniżej floora clampu (1),
    # żeby trywialne, zawsze-zdawane sygnały nie zawyżały wyniku ogólnego.
    if factor_id in LOW_IMPACT_FACTORS:
        impact_final = LOW_IMPACT_FACTORS[factor_id]
    return impact_final, effort_final


def _schema_code_example(factor_id: str) -> str | None:
    if factor_id in {"article_schema", "schema_author_field", "schema_dates"}:
        return """<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "Tytuł artykułu",
  "author": {"@type": "Person", "name": "Imię i nazwisko"},
  "datePublished": "2026-05-15",
  "dateModified": "2026-05-15"
}
</script>"""
    if factor_id in {"organization_schema", "website_schema", "localbusiness_or_organization_schema"}:
        return """<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Organization",
  "name": "Nazwa firmy",
  "url": "https://example.com",
  "sameAs": ["https://www.linkedin.com/company/example"]
}
</script>"""
    if factor_id == "product_or_service_schema":
        return """<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Service",
  "name": "Nazwa usługi",
  "provider": {"@type": "Organization", "name": "Nazwa firmy"},
  "areaServed": "PL"
}
</script>"""
    if factor_id == "breadcrumb_schema":
        return """<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://example.com"}
  ]
}
</script>"""
    return None


def _generic_detail(factor_id: str, label: str, group: str, meta: dict | None = None, *, is_tech: bool = False, is_domain: bool = False) -> dict:
    meta = meta or {}
    # Stable per-factor descriptions take precedence over group templates (patent factors keep their own metadata).
    stable = FACTOR_DETAILS.get(factor_id)
    if stable and meta.get("source") != "google_patent":
        return {
            "what": stable["what"],
            "why": stable["why"],
            "how_to_fix": stable["how_to_fix"],
            "code_example": _schema_code_example(factor_id) if (factor_id in SCHEMA_FACTOR_IDS or "schema" in factor_id) else None,
        }
    if meta.get("source") == "google_patent" and factor_id in PATENT_FACTORS:
        patent = PATENT_FACTORS[factor_id]
        patents = [p.get("patent_id", "") for p in patent.get("source_patents", []) if p.get("patent_id")]
        return {
            "what": patent.get("definition_pl", f"Czynnik patentowy: {label}."),
            "why": (
                f"{patent.get('evidence_summary_pl', '')} "
                "Traktuj to jako sprawdzalną hipotezę audytową, nie deklarację aktualnego czynnika rankingowego Google."
            ).strip(),
            "how_to_fix": patent.get("how_to_satisfy_pl", "Popraw treść zgodnie z opisem mechanizmu i zweryfikuj zmianę na konkretnych URL-ach."),
            "code_example": None,
            "patent_ref": ", ".join(patents),
        }

    is_schema = factor_id in SCHEMA_FACTOR_IDS or "schema" in factor_id
    if is_schema:
        return {
            "what": f"{label} to techniczny opis treści w danych strukturalnych, który pomaga wyszukiwarkom i systemom AI rozpoznać typ strony.",
            "why": "Schema zmniejsza niejednoznaczność: artykuł, organizacja, oferta, breadcrumb lub osoba są opisane jawnie, a nie tylko wywnioskowane z HTML.",
            "how_to_fix": "Dodaj lub popraw JSON-LD zgodny z realną treścią strony. Nie oznaczaj elementów, których użytkownik nie widzi w treści.",
            "code_example": _schema_code_example(factor_id),
        }
    if group == "technical":
        return {
            "what": f"{label} to warunek techniczny, który wpływa na możliwość poprawnego pobrania, zinterpretowania lub zaufania do strony.",
            "why": "Problemy techniczne ograniczają wartość nawet dobrej treści, bo boty i wyszukiwarki mogą jej nie pobrać, nie zrozumieć albo źle skonsolidować.",
            "how_to_fix": "Napraw element w kodzie strony, konfiguracji serwera albo plikach domenowych, a potem sprawdź wynik ponownym audytem.",
            "code_example": None,
        }
    if group == "eeat":
        return {
            "what": f"{label} pokazuje, czy strona daje użytkownikowi i AI wystarczające sygnały doświadczenia, eksperckości i wiarygodności.",
            "why": "Przy tematach eksperckich anonimowa lub generyczna treść jest trudniejsza do zaufania i cytowania.",
            "how_to_fix": "Dodaj konkret: autora, kwalifikacje, źródła, dane własne, case study, zewnętrzne potwierdzenia albo inne dowody adekwatne do typu strony.",
            "code_example": "Przykład treści: „Autor: Anna Nowak, senior SEO consultant od 2016 r.; w tekście wykorzystano dane z GSC z marca 2026 oraz dokumentację Google Search Central.”",
        }
    if group == "ai_aeo":
        return {
            "what": f"{label} wpływa na to, czy system AI może łatwo wyciągnąć z podstrony jednoznaczną odpowiedź.",
            "why": "Asystenci AI preferują krótkie, samodzielne fragmenty z jasną odpowiedzią, źródłem i kontekstem.",
            "how_to_fix": "Dodaj odpowiedź już na początku sekcji, rozbij długie akapity, użyj list/tabel i dopisz brakujące pytania klientów.",
            "code_example": "Przykład: „Crawl budget to limit zasobów, które Googlebot przeznacza na pobieranie URL-i z domeny. Najczęściej problem dotyczy dużych sklepów i serwisów z filtrami.”",
        }
    return {
        "what": f"{label} określa, czy podstrona jasno komunikuje swój temat, cel i wartość dla użytkownika.",
        "why": "Czytelny on-page zmniejsza chaos informacyjny: użytkownik, Google i AI szybciej rozumieją, co jest najważniejsze.",
        "how_to_fix": "Doprecyzuj tytuł, H1, pierwszą sekcję, linkowanie wewnętrzne i układ treści tak, żeby wspierały jedną główną intencję.",
        "code_example": "Przykład: zamiast ogólnego H1 „Rozwiązania dla firm” użyj „Audyt SEO dla sklepów internetowych po migracji platformy”.",
    }


def _enrich_factor_metadata() -> None:
    for factor_id, meta in FACTOR_META.items():
        group = _ui_group_for_factor(factor_id, meta)
        impact, effort = _impact_effort_for_factor(factor_id, group, meta)
        meta.setdefault("group", group)
        meta.setdefault("group_label", UI_GROUP_LABELS[group])
        meta.setdefault("applies_to", _content_applies_to(factor_id))
        meta.setdefault("impact", impact)
        meta.setdefault("effort", effort)
        meta.setdefault("detail", _generic_detail(factor_id, meta.get("label", factor_id), group, meta))
        if meta.get("source") == "google_patent" and factor_id in PATENT_FACTORS:
            patents = [p.get("patent_id", "") for p in PATENT_FACTORS[factor_id].get("source_patents", []) if p.get("patent_id")]
            meta.setdefault("patent_ref", ", ".join(patents))

    for factor_id, meta in TECH_FACTOR_META.items():
        group = _ui_group_for_factor(factor_id, meta, is_tech=True)
        impact, effort = _impact_effort_for_factor(factor_id, group, meta, is_tech=True)
        meta.setdefault("group", group)
        meta.setdefault("group_label", UI_GROUP_LABELS[group])
        meta.setdefault("applies_to", PAGE_TECH_APPLIES_TO.get(factor_id, ["homepage", "service", "article", "about", "contact", "category", "other"]))
        meta.setdefault("impact", impact)
        meta.setdefault("effort", effort)
        detail = _generic_detail(factor_id, meta.get("label", factor_id), group, meta, is_tech=True)
        if factor_id in TECH_FIX_HOW:
            detail["how_to_fix"] = TECH_FIX_HOW[factor_id]
        meta.setdefault("detail", detail)

    for factor_id, meta in DOMAIN_TECH_META.items():
        group = _ui_group_for_factor(factor_id, meta, is_domain=True)
        impact, effort = _impact_effort_for_factor(factor_id, group, meta, is_domain=True)
        meta.setdefault("group", group)
        meta.setdefault("group_label", UI_GROUP_LABELS[group])
        meta.setdefault("applies_to", ["domain"])
        meta.setdefault("impact", impact)
        meta.setdefault("effort", effort)
        detail = _generic_detail(factor_id, meta.get("label", factor_id), group, meta, is_domain=True)
        if factor_id in DOMAIN_FIX_HOW:
            detail["how_to_fix"] = DOMAIN_FIX_HOW[factor_id]
        meta.setdefault("detail", detail)

    for factor_id, meta in PERFORMANCE_FACTOR_META.items():
        group = _ui_group_for_factor(factor_id, meta, is_performance=True)
        impact, effort = _impact_effort_for_factor(factor_id, group, meta)
        meta.setdefault("group", group)
        meta.setdefault("group_label", UI_GROUP_LABELS[group])
        meta.setdefault("applies_to", ["homepage", "service", "article", "about", "contact", "category", "other"])
        meta.setdefault("impact", impact)
        meta.setdefault("effort", effort)
        meta.setdefault("detail", {
            "what": meta["label"],
            "why": PERFORMANCE_FACTOR_WHY.get(factor_id, "Wpływa na Core Web Vitals i ranking mobilny."),
            "how_to_fix": "Zoptymalizuj zasoby (obrazy WebP/AVIF, lazy-load, defer JS), CDN, kompresję, cache. Sprawdź raport PageSpeed Insights dla detali.",
            "code_example": None,
        })


def _score_status(score: float) -> str:
    if score >= 1.75:
        return "ok"
    if score >= 0.75:
        return "partial"
    return "missing"


def _status_label(status: str) -> str:
    return {"ok": "OK", "partial": "Częściowo", "missing": "Brak"}.get(status, status)


def _tech_auto_note(score: int, *, domain: bool = False) -> str:
    target = "konfiguracji domeny" if domain else "kodzie HTML strony"
    if score >= 2:
        return f"Element wykryty poprawnie w {target}."
    if score == 1:
        return f"Element wykryty częściowo lub niekompletnie w {target}."
    return f"Element nie został wykryty w {target}."


def _trunc(text: str, limit: int = 80) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def _tech_specific_note(key: str, hc: dict, score: int) -> str:
    """Concrete observation per tech factor — actual values + delta vs threshold."""
    if not hc:
        return _tech_auto_note(score)
    meta = hc.get("meta", {}) or {}
    heads = hc.get("headings", {}) or {}
    schema = hc.get("schema", {}) or {}
    imgs = hc.get("images", {}) or {}
    sem = hc.get("semantic_html5", {}) or {}
    contact_sig = hc.get("contact_signals", {}) or {}
    size_kb = hc.get("html_size_kb", 0)

    if key == "meta_title_present":
        title = meta.get("title")
        if title:
            return f"Title obecny ({len(title)} zn.): „{_trunc(title, 90)}”."
        return "Brak tagu <title> w <head>."
    if key == "meta_description":
        desc = meta.get("description")
        if desc:
            ln = len(desc)
            note = f"Meta description: {ln} zn. „{_trunc(desc, 90)}”."
            if ln < 70:
                note += " Za krótko (zalecane 120–160 zn.)."
            elif ln > 170:
                note += " Za długo (zalecane 120–160 zn., snippet ucinany)."
            return note
        return "Brak meta description w <head>."
    if key == "canonical_tag":
        c = meta.get("canonical")
        return f"Canonical: {c}" if c else "Brak <link rel=\"canonical\">."
    if key == "h1_single":
        n = heads.get("h1_count", 0)
        if n == 1:
            return "Dokładnie 1 H1 — OK."
        if n == 0:
            return "Brak H1 na stronie."
        return f"{n} elementów H1 — powinien być dokładnie 1."
    if key == "heading_hierarchy":
        if heads.get("hierarchy_ok"):
            return f"Hierarchia poprawna (H2: {heads.get('h2_count',0)}, H3: {heads.get('h3_count',0)})."
        return f"Skoki poziomów lub brak H2 (H1: {heads.get('h1_count',0)}, H2: {heads.get('h2_count',0)}, H3: {heads.get('h3_count',0)})."
    if key == "og_tags":
        ogt, ogd = meta.get("og_title"), meta.get("og_description")
        if ogt and ogd:
            return "og:title + og:description obecne."
        if ogt:
            return "og:title obecne, brak og:description."
        return "Brak tagów Open Graph (og:title, og:description)."
    if key == "viewport_meta":
        v = meta.get("viewport")
        return f"Viewport: „{_trunc(v, 80)}”." if v else "Brak <meta name=\"viewport\"> — strona nie skaluje się na mobile."
    if key == "lang_attribute":
        lang = meta.get("lang")
        return f"<html lang=\"{lang}\"> — OK." if lang else "Brak atrybutu lang w <html>."
    if key == "image_alt_coverage":
        pct = imgs.get("alt_coverage_pct", 0)
        total = imgs.get("total", 0)
        with_alt = imgs.get("with_alt", 0)
        return f"Pokrycie alt: {pct}% ({with_alt}/{total} obrazów). Próg dobry: ≥90%, słaby: <50%."
    if key == "semantic_html5_tags":
        present = [k for k in ("article", "main", "section", "nav", "header", "footer") if sem.get(k)]
        if present:
            return f"Tagi semantyczne: {', '.join(present)}."
        return "Brak tagów semantycznych (<article>, <main>, <section>) — surowy <div>-soup."
    if key == "response_size_ok":
        if size_kb > 500:
            return f"HTML {size_kb} KB — przekracza próg 500 KB o {round(size_kb - 500, 1)} KB. Boty mogą ucinać/wolniej parsować."
        if size_kb > 200:
            return f"HTML {size_kb} KB — w strefie ostrzegawczej (próg dobry: ≤200 KB)."
        return f"HTML {size_kb} KB — w normie."
    if key in ("organization_schema",):
        return "Schema Organization obecny." if schema.get("organization") else "Brak schema typu Organization w JSON-LD."
    if key == "website_schema":
        return "Schema WebSite obecny." if schema.get("website") else "Brak schema typu WebSite w JSON-LD."
    if key == "any_schema":
        types = schema.get("types", []) or []
        if types:
            return f"Obecne typy schema: {', '.join(types[:6])}{' …' if len(types) > 6 else ''}."
        return "Brak jakiegokolwiek JSON-LD na stronie."
    if key == "product_or_service_schema":
        if schema.get("product") or schema.get("service"):
            return "Schema Product/Service obecny."
        return "Brak schema Product ani Service na stronie usługowej/produktowej."
    if key == "faq_schema_bonus":
        return "Schema FAQPage obecny." if schema.get("faq") else "Brak FAQPage (bonus — pomaga w AI Overviews i rich snippets)."
    if key == "breadcrumb_schema":
        return "BreadcrumbList obecny." if schema.get("breadcrumb") else "Brak schema BreadcrumbList — okruszki nawigacyjne bez wsparcia w kodzie."
    if key == "article_schema":
        return "Schema Article/BlogPosting obecny." if schema.get("article") else "Brak schema Article/BlogPosting na podstronie artykułowej."
    if key == "schema_author_field":
        return "Pole author w schema obecne." if schema.get("has_author") else "Brak pola \"author\" w JSON-LD artykułu."
    if key == "schema_dates":
        dp = schema.get("has_datepublished")
        dm = schema.get("has_datemodified")
        if dp and dm:
            return "datePublished + dateModified obecne w schema."
        if dp:
            return "datePublished obecne, brak dateModified — AI nie wie, że artykuł jest aktualizowany."
        return "Brak datePublished/dateModified w schema."
    if key == "person_schema_team":
        return "Schema Person obecny." if schema.get("person") else "Brak schema Person — zespół niewidoczny dla AI jako encje."
    if key == "localbusiness_or_organization_schema":
        if schema.get("localbusiness") or schema.get("organization"):
            return "Schema LocalBusiness/Organization obecny."
        return "Brak LocalBusiness ani Organization w JSON-LD."
    if key == "tel_link_present":
        return "Klikalny <a href=\"tel:\"> obecny." if contact_sig.get("tel") else "Brak klikalnego numeru telefonu (<a href=\"tel:…\">)."
    if key == "mailto_link_present":
        return "Klikalny <a href=\"mailto:\"> obecny." if contact_sig.get("mailto") else "Brak klikalnego e-maila (<a href=\"mailto:…\">)."
    if key == "contact_form_present":
        n = contact_sig.get("forms", 0)
        return f"Formularz kontaktowy: {n} <form> na stronie." if n else "Brak <form> na podstronie kontaktowej."
    if key == "itemlist_schema":
        return "Schema ItemList obecny." if schema.get("itemlist") else "Brak schema ItemList — lista produktów/wpisów bez wsparcia w kodzie."
    return _tech_auto_note(score)


def _domain_tech_specific_note(key: str, raw: dict, score: int) -> str:
    """Concrete observation per domain tech factor."""
    if not raw:
        return _tech_auto_note(score, domain=True)
    robots = raw.get("robots", {}) or {}
    sitemap = raw.get("sitemap", {}) or {}
    llms = raw.get("llms", {}) or {}
    headers = raw.get("http_headers", {}) or {}
    hc = raw.get("homepage_hc", {}) or {}
    bots = robots.get("bots", {}) or {}

    if key == "robots_txt_accessible":
        if robots.get("accessible"):
            return "robots.txt dostępny (HTTP 200)."
        err = robots.get("error")
        return f"robots.txt niedostępny ({err})." if err else "robots.txt niedostępny lub brak pliku."
    if key in ("gptbot_not_blocked", "perplexitybot_not_blocked", "claudebot_not_blocked", "google_extended_not_blocked"):
        bot_name = {
            "gptbot_not_blocked": "GPTBot",
            "perplexitybot_not_blocked": "PerplexityBot",
            "claudebot_not_blocked": "ClaudeBot",
            "google_extended_not_blocked": "Google-Extended",
        }[key]
        b = bots.get(bot_name, {}) or {}
        if b.get("allowed", True):
            return f"{bot_name} dozwolony{' (wymieniony jawnie w robots.txt)' if b.get('mentioned') else ' (brak reguły Disallow)'}."
        return f"{bot_name} zablokowany w robots.txt (Disallow: /) — bot AI nie pobiera Twoich treści."
    if key == "crawl_delay_ok":
        d = robots.get("crawl_delay")
        if d is None:
            return "Brak Crawl-delay — OK."
        if d < 10:
            return f"Crawl-delay: {d}s — w normie."
        if d < 30:
            return f"Crawl-delay: {d}s — wysoki, spowalnia indeksację."
        return f"Crawl-delay: {d}s — bardzo wysoki, blokuje crawl."
    if key == "sitemap_present":
        if sitemap.get("exists"):
            return f"sitemap.xml obecna pod {sitemap.get('url','')} ({sitemap.get('size_kb','?')} KB)."
        return "Brak sitemap.xml — boty muszą same szukać URL-i."
    if key == "sitemap_in_robots":
        return "Link do sitemap w robots.txt — OK." if robots.get("sitemap_in_robots") else "Brak linii Sitemap: w robots.txt."
    if key == "llms_txt_present":
        if llms.get("exists"):
            return f"{llms.get('path','/llms.txt')} obecny ({llms.get('size_kb','?')} KB)."
        return "Brak /llms.txt — pliku ułatwiającego AI poznanie struktury i kluczowych zasobów domeny."
    if key == "https_enabled":
        return "HTTPS aktywne (kłódka w przeglądarce)." if hc.get("https") else "HTTPS wyłączone — strona ładowana po HTTP (brak szyfrowania)."
    if key == "hreflang_used":
        n = (hc.get("meta", {}) or {}).get("hreflang_count", 0)
        if n > 0:
            return f"{n} tag(ów) hreflang w <head>."
        return "Brak hreflang — strona jednojęzyczna lub język niezadeklarowany."
    if key == "hsts_enabled":
        if headers.get("hsts"):
            return "HSTS aktywne (Strict-Transport-Security obecne w odpowiedzi serwera)."
        return "Brak nagłówka Strict-Transport-Security."
    if key == "compression_enabled":
        if headers.get("compression"):
            return f"Kompresja aktywna ({headers.get('compression')})."
        return "Brak kompresji gzip/brotli — strona transferowana jako tekst."
    return _tech_auto_note(score, domain=True)


# Per-factor concrete fix instructions (override generic detail.how_to_fix).
TECH_FIX_HOW = {
    "meta_title_present": "Dodaj <title> w <head> — 50–60 zn., zawierający główne słowo kluczowe i markę: <title>Audyt akustyczny mieszkań — Nyquista</title>.",
    "meta_description": "Dodaj/uzupełnij meta description (120–160 zn.), zawierającą propozycję wartości i CTA. <meta name=\"description\" content=\"…\">.",
    "canonical_tag": "Dodaj <link rel=\"canonical\" href=\"https://twojadomena.pl/strona/\"> w <head>, wskazując pełny URL bez parametrów śledzących.",
    "h1_single": "Pozostaw dokładnie jeden <h1> na stronie z głównym tematem podstrony. Pozostałe nagłówki sekcji zamień na <h2>/<h3>.",
    "heading_hierarchy": "Ułóż nagłówki kolejno: H1 → H2 → H3, bez skoków (np. H1 → H4). Każda sekcja zaczyna się od H2.",
    "og_tags": "Dodaj w <head>: <meta property=\"og:title\">, <meta property=\"og:description\">, <meta property=\"og:image\">, <meta property=\"og:url\">.",
    "viewport_meta": "Dodaj <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> w <head>.",
    "lang_attribute": "Ustaw <html lang=\"pl\"> (lub odpowiedni kod języka).",
    "image_alt_coverage": "Dodaj atrybut alt do wszystkich <img>. Dla obrazów dekoracyjnych użyj alt=\"\". Dla treściowych — opisz zawartość 5–12 słowami.",
    "semantic_html5_tags": "Zamień opakowujące <div> na <main>, <article>, <section>, <nav>, <header>, <footer> zgodnie z funkcją bloku.",
    "response_size_ok": "Zmniejsz wagę HTML do ≤200 KB: usuń inline CSS/JS (przenieś do plików), wykasuj komentarze i niewykorzystany markup, minifikuj output WordPress/CMS, włącz lazy-load grafik.",
    "organization_schema": "Dodaj JSON-LD Organization z polami: name, url, logo, sameAs (LinkedIn, GBP, social). Umieść w <head> lub stopce.",
    "website_schema": "Dodaj JSON-LD WebSite z polami: name, url, potentialAction (SearchAction) — pomaga AI rozpoznać markę i wyszukiwarkę wewnętrzną.",
    "any_schema": "Wstrzyknij dowolny JSON-LD pasujący do typu strony (Organization na home, Article na blogu, Service na ofertach, BreadcrumbList wszędzie).",
    "product_or_service_schema": "Dodaj JSON-LD Service (lub Product) z polami: name, description, provider, areaServed, offers (price/priceCurrency).",
    "faq_schema_bonus": "Dodaj JSON-LD FAQPage z faktycznymi pytaniami i odpowiedziami widocznymi też w treści (nie ukryte).",
    "breadcrumb_schema": "Dodaj JSON-LD BreadcrumbList — lista pozycji od Home → Kategoria → Bieżąca strona, z polami position/name/item.",
    "article_schema": "Dodaj JSON-LD Article (lub BlogPosting) z polami: headline, author, datePublished, dateModified, image, publisher.",
    "schema_author_field": "Uzupełnij \"author\": {\"@type\":\"Person\",\"name\":\"…\",\"url\":\"…/o-nas\"} w JSON-LD artykułu.",
    "schema_dates": "Dodaj \"datePublished\" i \"dateModified\" w formacie ISO 8601 (\"2026-05-17\") do JSON-LD Article.",
    "person_schema_team": "Dla każdej osoby zespołu dodaj JSON-LD Person z name, jobTitle, sameAs (LinkedIn), worksFor.",
    "localbusiness_or_organization_schema": "Dodaj JSON-LD LocalBusiness z address (PostalAddress), telephone, openingHours, geo (latitude/longitude).",
    "tel_link_present": "Zamień telefony w treści na <a href=\"tel:+48123456789\">+48 123 456 789</a>.",
    "mailto_link_present": "Zamień adresy e-mail w treści na <a href=\"mailto:kontakt@…\">kontakt@…</a>.",
    "contact_form_present": "Dodaj prosty <form> z polami imię, e-mail, wiadomość + reCAPTCHA. Po wysłaniu — strona dziękująca.",
    "itemlist_schema": "Dodaj JSON-LD ItemList z position/url/name każdego elementu listy (produkty, wpisy, członkowie).",
}

DOMAIN_FIX_HOW = {
    "robots_txt_accessible": "Udostępnij plik /robots.txt (HTTP 200, text/plain). Minimalny: \"User-agent: *\\nAllow: /\\nSitemap: https://twojadomena.pl/sitemap.xml\".",
    "gptbot_not_blocked": "Usuń z robots.txt sekcję \"User-agent: GPTBot\\nDisallow: /\" (lub zmień Disallow na Allow).",
    "perplexitybot_not_blocked": "Usuń z robots.txt sekcję \"User-agent: PerplexityBot\\nDisallow: /\".",
    "claudebot_not_blocked": "Usuń z robots.txt sekcję \"User-agent: ClaudeBot\\nDisallow: /\" (oraz anthropic-ai).",
    "google_extended_not_blocked": "Usuń z robots.txt sekcję \"User-agent: Google-Extended\\nDisallow: /\" — inaczej AI Overviews Cię pomija.",
    "crawl_delay_ok": "Usuń lub obniż dyrektywę Crawl-delay w robots.txt (zostaw ≤10 lub nic).",
    "sitemap_present": "Wygeneruj sitemap.xml (Yoast/RankMath/Screaming Frog) i opublikuj pod /sitemap.xml. Zgłoś w Google Search Console.",
    "sitemap_in_robots": "Dodaj na końcu robots.txt linię: \"Sitemap: https://twojadomena.pl/sitemap.xml\".",
    "llms_txt_present": "Stwórz /llms.txt z markdown TOC do najważniejszych URL-i, opisem firmy i licencją treści. Standard: https://llmstxt.org.",
    "https_enabled": "Wymuś HTTPS: zainstaluj certyfikat Let's Encrypt (zwykle darmowo u hostingu), dodaj redirect 301 HTTP → HTTPS.",
    "hreflang_used": "Dodaj <link rel=\"alternate\" hreflang=\"pl\" href=\"…\"> i hreflang=\"x-default\" — istotne tylko jeśli masz wersje językowe.",
    "hsts_enabled": "Dodaj nagłówek serwera: Strict-Transport-Security: max-age=31536000; includeSubDomains.",
    "compression_enabled": "Włącz na serwerze gzip lub brotli (nginx: gzip on, brotli on; Apache: mod_deflate). Sprawdza się przez Content-Encoding header.",
}


def _ensure_factor_record(index: dict[str, dict], key: str, meta: dict, *, is_tech: bool = False, is_domain: bool = False, is_performance: bool = False) -> dict:
    uid = f"perf:{key}" if is_performance else f"domain:{key}" if is_domain else f"tech:{key}" if is_tech else f"factor:{key}"
    if uid not in index:
        group = meta.get("group") or _ui_group_for_factor(key, meta, is_tech=is_tech, is_domain=is_domain, is_performance=is_performance)
        index[uid] = {
            "uid": uid,
            "id": key,
            "label": meta.get("label", key),
            "label_fail": meta.get("label_fail", meta.get("label", key)),
            "group": group,
            "group_label": UI_GROUP_LABELS.get(group, group),
            "applies_to": meta.get("applies_to", []),
            "impact": meta.get("impact", 2),
            "effort": meta.get("effort", 2),
            "detail": meta.get("detail", {}),
            "source": meta.get("source", "technical" if (is_tech or is_domain) else "audit"),
            "patent_ref": meta.get("patent_ref", ""),
            "confidence": meta.get("confidence", ""),
            "seo_inference_level": meta.get("seo_inference_level", ""),
            "evidence_ids": meta.get("evidence_ids", []),
            "is_tech": is_tech,
            "is_domain": is_domain,
            "observations": [],
        }
    return index[uid]


def build_factor_index(page_audits: list[dict], domain_tech_scores: dict, domain_tech_raw: dict | None = None) -> list[dict]:
    index: dict[str, dict] = {}

    for pa in page_audits:
        hc = pa.get("html_checks") or {}
        page_ref = {
            "url": pa.get("url", ""),
            "title": pa.get("title", ""),
            "page_type": pa.get("page_type", "other"),
            "page_type_label": pa.get("page_type_label", PAGE_TYPE_LABELS.get(pa.get("page_type", "other"), pa.get("page_type", "other"))),
            "scope": "page",
        }
        for key, value in (pa.get("factors") or {}).items():
            if not isinstance(value, dict) or "score" not in value:
                continue
            meta = FACTOR_META.get(key, {"label": key, "category": "topical"})
            record = _ensure_factor_record(index, key, meta)
            score = int(value.get("score", 0))
            status = _score_status(score)
            record["observations"].append({
                **page_ref,
                "score": score,
                "score_pct": round(score / 2 * 100),
                "status": status,
                "status_label": _status_label(status),
                "note": value.get("note", ""),
            })

        for key, score_raw in (pa.get("tech_scores") or {}).items():
            meta = TECH_FACTOR_META.get(key, {"label": key, "category": "tech"})
            record = _ensure_factor_record(index, key, meta, is_tech=True)
            score = int(score_raw)
            status = _score_status(score)
            record["observations"].append({
                **page_ref,
                "score": score,
                "score_pct": round(score / 2 * 100),
                "status": status,
                "status_label": _status_label(status),
                "note": _tech_specific_note(key, hc, score),
            })

        for key, perf_data in (pa.get("performance_scores") or {}).items():
            meta = PERFORMANCE_FACTOR_META.get(key, {"label": key, "category": "performance"})
            record = _ensure_factor_record(index, key, meta, is_performance=True)
            if isinstance(perf_data, dict):
                score = int(perf_data.get("score", 0))
                note = perf_data.get("note", "")
            else:
                score = int(perf_data)
                note = ""
            status = _score_status(score)
            record["observations"].append({
                **page_ref,
                "score": score,
                "score_pct": round(score / 2 * 100),
                "status": status,
                "status_label": _status_label(status),
                "note": note or _tech_auto_note(score),
            })

    for key, score_raw in (domain_tech_scores or {}).items():
        meta = DOMAIN_TECH_META.get(key, {"label": key, "category": "tech"})
        record = _ensure_factor_record(index, key, meta, is_domain=True)
        score = int(score_raw)
        status = _score_status(score)
        record["observations"].append({
            "url": "domain",
            "title": "Cała domena",
            "page_type": "domain",
            "page_type_label": "Domena",
            "scope": "domain",
            "score": score,
            "score_pct": round(score / 2 * 100),
            "status": status,
            "status_label": _status_label(status),
            "note": _domain_tech_specific_note(key, domain_tech_raw or {}, score),
        })

    records = []
    for record in index.values():
        observations = record["observations"]
        if not observations:
            continue
        scores = [obs["score"] for obs in observations]
        worst = min(scores)
        avg_score = sum(scores) / len(scores)
        avg_score_value = sum(score_value(score) for score in scores) / len(scores)
        status = _score_status(worst)
        affected = [obs for obs in observations if obs["score"] < 2]
        record["score"] = worst
        record["avg_score"] = round(avg_score, 2)
        record["score_pct"] = round(avg_score_value * 100)
        record["status"] = status
        record["status_label"] = _status_label(status)
        record["affected_count"] = len(affected)
        record["ok_count"] = len(observations) - len(affected)
        record["priority_score"] = round(((2 - worst) * record["impact"] / max(record["effort"], 1)) + (len(affected) * 0.15), 2)
        records.append(record)

    return sorted(records, key=lambda item: (UI_GROUP_ORDER.index(item["group"]) if item["group"] in UI_GROUP_ORDER else 99, -item["priority_score"], item["label"]))


def _scoped_observations(factor: dict, scope_url: str = "all") -> list[dict]:
    observations = factor.get("observations", [])
    if scope_url == "all":
        return observations
    return [
        obs
        for obs in observations
        if obs.get("url") == scope_url or obs.get("scope") == "domain"
    ]


def calculate_scope_scores(factor_index: list[dict], scope_url: str = "all") -> dict:
    totals = {group: {"val": 0.0, "max": 0.0, "count": 0} for group in UI_GROUP_ORDER}
    scoped_observations: list[dict] = []
    scoped_factors: list[dict] = []
    for factor in factor_index:
        group = factor.get("group")
        if group not in totals:
            continue
        observations = _scoped_observations(factor, scope_url)
        if not observations:
            continue
        scoped_factors.append(factor)
        scoped_observations.extend({**obs, "_factor_id": factor.get("id"), "_group": group} for obs in observations)
        impact = factor.get("impact", 2)
        for obs in observations:
            totals[group]["val"] += score_value(obs.get("score", 0)) * impact
            totals[group]["max"] += impact
            totals[group]["count"] += 1

    groups = []
    for group in UI_GROUP_ORDER:
        total = totals[group]
        score = round(total["val"] / total["max"] * 100) if total["max"] else None
        groups.append({
            "id": group,
            "label": UI_GROUP_LABELS[group],
            "score": score,
            "count": total["count"],
        })

    weighted_total = 0.0
    weight_total = 0.0
    for group in groups:
        if group["score"] is None:
            continue
        weight = UI_GROUP_WEIGHTS.get(group["id"], 10)
        weighted_total += group["score"] * weight
        weight_total += weight
    overall = round(weighted_total / weight_total) if weight_total else 0
    return {"overall": overall, "raw_overall": overall, "groups": groups}


def build_top_actions(factor_index: list[dict], scope_url: str = "all", limit: int = 5) -> list[dict]:
    actions = []
    for factor in factor_index:
        observations = [obs for obs in _scoped_observations(factor, scope_url) if obs.get("score", 2) < 2]
        if not observations:
            continue
        worst = min(obs.get("score", 0) for obs in observations)
        severity = 2 - worst
        priority = round((severity * factor.get("impact", 2) / max(factor.get("effort", 1), 1)) + (len(observations) * 0.15), 2)
        first_note = next((obs.get("note", "") for obs in observations if obs.get("note")), "")
        page_refs = [
            {
                "url": obs.get("url", ""),
                "title": obs.get("title", ""),
                "page_type": obs.get("page_type", ""),
                "page_type_label": obs.get("page_type_label", ""),
                "score": obs.get("score", 0),
                "status": obs.get("status", ""),
            }
            for obs in observations[:5]
        ]
        actions.append({
            "factor_uid": factor.get("uid"),
            "factor_id": factor.get("id"),
            "label": factor.get("label"),
            "group": factor.get("group"),
            "group_label": factor.get("group_label"),
            "status": _score_status(worst),
            "status_label": _status_label(_score_status(worst)),
            "impact": factor.get("impact", 2),
            "effort": factor.get("effort", 2),
            "affected_count": len(observations),
            "priority_score": priority,
            "note": first_note,
            "page_refs": page_refs,
            "source": factor.get("source", ""),
        })
    return sorted(actions, key=lambda item: (-item["priority_score"], -item["affected_count"], item["label"]))[:limit]


def build_dashboard(factor_index: list[dict], page_audits: list[dict]) -> dict:
    default_scores = calculate_scope_scores(factor_index, "all")
    url_options = [{
        "id": "all",
        "label": "Wszystkie",
        "url": None,
        "page_type": "all",
        "page_type_label": "Wszystkie",
        "count": len(page_audits),
        "score": default_scores["overall"],
    }]
    for idx, page in enumerate(page_audits, start=1):
        scoped = calculate_scope_scores(factor_index, page.get("url", ""))
        url_options.append({
            "id": page.get("url", f"page-{idx}"),
            "label": page.get("page_type_label") or PAGE_TYPE_LABELS.get(page.get("page_type", "other"), page.get("page_type", "other")),
            "url": page.get("url", ""),
            "title": page.get("title", ""),
            "page_type": page.get("page_type", "other"),
            "page_type_label": page.get("page_type_label", ""),
            "count": 1,
            "score": scoped["overall"],
        })

    return {
        "overall": default_scores["overall"],
        "groups": default_scores["groups"],
        "top_actions": build_top_actions(factor_index, "all", limit=5),
        "url_options": url_options,
    }


_enrich_factor_metadata()


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


def fetch_homepage_nav_links(homepage_url: str, base_url: str) -> list[dict]:
    """GET the homepage and return links from <nav>/<header>/menu-classed elements.

    These are the site's main entrypoints — usually About, Services, Blog, Contact.
    Used to bias Gemini's candidate selection toward what the site itself promotes.
    """
    try:
        r = requests.get(
            homepage_url,
            timeout=15,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; KopernikAudit/1.0)"},
        )
        if r.status_code != 200 or not r.text:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return []

    domain = urlparse(base_url).netloc
    seen: set[str] = set()
    out: list[dict] = []

    containers = soup.find_all(["nav", "header"])
    for el in soup.find_all(attrs={"class": True}):
        cls = " ".join(el.get("class") or []).lower()
        if any(k in cls for k in ("menu", "navigation", "nav-", "navbar", "main-nav", "primary-menu")):
            containers.append(el)

    for cont in containers:
        for a in cont.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            full = urljoin(homepage_url, href)
            pu = urlparse(full)
            if pu.netloc and pu.netloc != domain:
                continue
            if not pu.path or pu.path in ("/", ""):
                continue
            if re.search(r"\.(jpg|jpeg|png|gif|webp|svg|ico|css|js|xml|pdf)(\?|$)", full, re.I):
                continue
            clean = f"{pu.scheme or 'https'}://{pu.netloc or domain}{pu.path.rstrip('/')}"
            if clean in seen:
                continue
            seen.add(clean)
            label = (a.get_text(strip=True) or "")[:80]
            out.append({"url": clean, "label": label})
            if len(out) >= 40:
                return out
    return out


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

    prompt = f"""Jesteś ekspertem SEO. Przeprowadzasz audyt witryny i musisz wybrać {MAX_AUDIT_PAGES - 1} najbardziej wartościowych podstron do audytu.

<cel_zadania>
Wybierz strony, które NAJLEPIEJ reprezentują witrynę pod kątem SEO i biznesowym.
Priorytetem są strony generujące ruch organiczny i konwersje — nie strony techniczne/systemowe.
</cel_zadania>

<priorytety_wyboru>
1. NAJWYŻSZY — strony sprzedażowe/usługowe (oferta, usługi, produkty, cennik, landing page). Wybierz 1–2 jeśli dostępne.
2. WYSOKI — artykuły blogowe/poradniki (wybierz flagowy — dłuższy slug, opisowa nazwa tematu). Wybierz 1–2 jeśli dostępne.
3. ŚREDNI — strona "o nas" / "o firmie" / "zespół" (sygnał E-E-A-T). Wybierz 1 jeśli dostępna.
4. NISKI — kontakt (tylko jeśli brakuje ważniejszych stron).
</priorytety_wyboru>

<obowiązkowe_wykluczenia>
- polityka prywatności, regulamin, RODO, cookies, disclaimer
- paginacja (/page/N, ?page=, /strona/N, /p/N)
- tag pages, archive pages, strony wyników wyszukiwania
- logowanie, rejestracja, koszyk, checkout, konto użytkownika
- URL-e z tokenami sesji, parametrami śledzenia (?utm_*, ?fbclid=, itp.)
- Strony błędów, testowe, staging
</obowiązkowe_wykluczenia>

<wskazówki_doboru>
- Slug URL sugeruje ważność: "/uslugi/copywriting-seo" > "/uslugi"
- Przy wielu artykułach wybierz ten z najbardziej opisową, szczegółową nazwą tematu
- Unikaj stron bardzo podobnych tematycznie do siebie
- Każda strona powinna reprezentować inny segment treści witryny
- Strona główna ({homepage_url}) jest JUŻ WYBRANA — nie wliczaj jej
</wskazówki_doboru>

<typy_stron>
- service: oferta/usługa/produkt/cennik/landing (sprzedażowa)
- article: artykuł blogowy/poradnik/case study/news (edukacyjna)
- about: o nas/zespół/historia/misja (wizerunkowa)
- contact: kontakt/formularz/NAP
- category: kategoria/listing/archiwum
- other: inne istotne (portfolio, FAQ, referencje, itp.)
</typy_stron>

<kandydaci>
{chr(10).join(f"- {u}" for u in candidates)}
</kandydaci>

Zwróć TYLKO JSON (bez markdown, bez komentarzy poza JSON):
{{
  "selected": [
    {{"url": "https://...", "page_type": "service|article|about|contact|category|other", "reason": "krótkie uzasadnienie po polsku dlaczego ta strona jest wartościowa do audytu"}}
  ]
}}

Dokładnie {MAX_AUDIT_PAGES - 1} pozycji. Każda z innego segmentu witryny."""

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
        picked = picked[: MAX_AUDIT_PAGES - 1]
        picked = _ensure_about_page(picked, candidates, base_url)
        return picked
    except Exception:
        return _heuristic_pick_and_classify(candidates, base_url)


def _ensure_about_page(picked: list[dict], candidates: list[str], base_url: str) -> list[dict]:
    """Guarantee at least one about/team page is in the selection for E-E-A-T coverage."""
    if any(p["page_type"] == "about" for p in picked):
        return picked
    # Find first about-type candidate not already picked
    picked_urls = {p["url"] for p in picked}
    about_url = next(
        (u for u in candidates if u not in picked_urls and classify_page_type_heuristic(u, base_url) == "about"),
        None,
    )
    if not about_url:
        return picked
    # Replace lowest-priority slot: prefer contact > other > category > article > service
    replace_order = ["contact", "other", "category", "article", "service"]
    for rtype in replace_order:
        for i, p in enumerate(picked):
            if p["page_type"] == rtype:
                picked[i] = {"url": about_url, "page_type": "about", "reason": "wymuszona strona O nas / Zespół (sygnał E-E-A-T)"}
                return picked
    # All slots used by higher-priority types — replace last slot
    if picked:
        picked[-1] = {"url": about_url, "page_type": "about", "reason": "wymuszona strona O nas / Zespół (sygnał E-E-A-T)"}
    return picked


def propose_page_candidates(all_urls: list[str], homepage_url: str, base_url: str, per_type: int = 4, nav_links: list[dict] | None = None) -> dict:
    """Gemini groups sitemap URLs into buckets (service/article/about/other). User confirms picks."""
    domain = urlparse(base_url).netloc
    clean: list[str] = []
    seen = set()
    nav_url_set = {n["url"] for n in (nav_links or [])}

    def _clean_add(u: str) -> None:
        pu = urlparse(u)
        if pu.netloc and pu.netloc != domain:
            return
        if re.search(r"\.(jpg|jpeg|png|gif|webp|svg|ico|css|js|xml|woff2?|ttf|mp4|zip|pdf)(\?|$)", u, re.I):
            return
        key = (pu.path.rstrip("/"), pu.query)
        if key in seen or pu.path in ("", "/"):
            return
        if _is_article_listing(pu.path):
            return
        seen.add(key)
        clean.append(u)

    # Surface nav-menu URLs first — these are the site's own "important pages" signal.
    for nav in (nav_links or []):
        _clean_add(nav["url"])
    for u in all_urls:
        _clean_add(u)

    empty = {"service": [], "article": [], "about": [], "other": []}
    if not clean:
        return empty
    candidates = clean[:100]

    nav_block = ""
    if nav_links:
        nav_block = "\n<menu_glowne_strony>\nLinki z nawigacji strony głównej (najwyższy priorytet — to są strony, które witryna sama promuje):\n" + "\n".join(
            f"- {n['url']}" + (f"  [label: {n['label']}]" if n.get("label") else "")
            for n in nav_links[:30]
        ) + "\n</menu_glowne_strony>\n"

    prompt = f"""Jesteś ekspertem SEO. Klasyfikujesz podstrony witryny i sugerujesz NAJLEPSZYCH kandydatów do audytu.

<cel>
Pogrupuj kandydatów w 4 kubełki: service, article, about, other. Dla każdego wybierz do {per_type} najbardziej reprezentatywnych URL-i.
Strona główna {homepage_url} jest JUŻ wybrana — nie wliczaj jej.
</cel>

<definicje_kubelkow>
- service: konkretna strona oferty/usługi/produktu/cennika/landingu (np. /uslugi/seo, /oferta/google-ads, /produkt/abc, /cennik). NIE listingi typu /uslugi.
- article: KONKRETNY wpis blogowy/poradnik/case study — URL z pełnym slugiem tytułowym (np. /blog/jak-zoptymalizowac-strone, /poradniki/audyt-seo-krok-po-kroku). NIGDY sam /blog, /aktualnosci, /poradniki, /news — to listingi, nie artykuły.
- about: strona "o nas / o firmie / zespół / nasza historia / misja / wartości / poznaj-nas / ludzie / eksperci / agencja / o-agencji". Szukaj też nieoczywistych wariantów slugów.
- other: portfolio, FAQ, referencje, case studies (jeśli nie wpadły do article), partnerzy.
</definicje_kubelkow>

<bezwzgledne_wykluczenia>
- polityka prywatności, regulamin, RODO, cookies, disclaimer
- paginacja (/page/N, ?page=, /strona/N)
- tagi, kategorie, archiwa, wyniki wyszukiwania
- logowanie, rejestracja, koszyk, checkout, konto
- URL-e z trackingiem (?utm_, ?fbclid=, ?gclid=)
- LISTINGI bez slugu artykułu (np. sam /blog, /blog/, /aktualnosci, /news, /poradniki, /case-studies, /artykuly) — NIGDY nie wrzucaj ich do 'article'
- strony błędów, staging, testowe
</bezwzgledne_wykluczenia>

<heurystyki_wyboru>
- Strony z menu głównego (sekcja <menu_glowne_strony>) zwykle reprezentują najważniejsze sekcje — preferuj je przy doborze.
- Slug głębszy + opisowy = bardziej konkretna treść. Preferuj "/uslugi/seo-techniczny" nad "/uslugi", "/blog/audyt-seo-2024" nad "/blog/2024".
- Dla 'article': URL MUSI mieć segment slugu po prefiksie (/blog/COS-TU-JEST, nie samo /blog).
- Dla 'about': jeśli nie ma oczywistego /o-nas, sprawdź alternatywy: /o-firmie, /zespol, /poznaj-nas, /historia, /misja, /ludzie, /eksperci, /agencja.
- W każdym kubełku unikaj duplikatów tematycznych.
- Pusty kubełek = zwróć [].
</heurystyki_wyboru>
{nav_block}
<kandydaci>
{chr(10).join(f"- {u}" for u in candidates)}
</kandydaci>

Zwróć TYLKO JSON (bez markdown, bez komentarzy):
{{
  "service": [{{"url":"https://...","reason":"krótkie uzasadnienie po polsku"}}, ...],
  "article": [...],
  "about": [...],
  "other": [...]
}}"""

    try:
        parsed = _extract_json(_gemini_call(prompt, temperature=0.2, max_tokens=2048))
    except Exception:
        return _heuristic_propose_candidates(candidates, base_url, per_type)

    cand_set = set(candidates)
    out = {"service": [], "article": [], "about": [], "other": []}
    for bucket in out:
        for item in (parsed.get(bucket) or [])[:per_type]:
            u = item.get("url") if isinstance(item, dict) else None
            if u and u in cand_set and u not in {p["url"] for p in out[bucket]}:
                out[bucket].append({"url": u, "reason": (item.get("reason") if isinstance(item, dict) else "") or ""})
    # Fallback per empty bucket using heuristics
    heur = _heuristic_propose_candidates(candidates, base_url, per_type)
    for bucket, items in out.items():
        if not items:
            out[bucket] = heur[bucket]
    return out


def _heuristic_propose_candidates(urls: list[str], base_url: str, per_type: int) -> dict:
    buckets: dict[str, list[dict]] = {"service": [], "article": [], "about": [], "other": []}
    for u in urls:
        pt = classify_page_type_heuristic(u, base_url)
        if pt in buckets:
            bucket = pt
        elif pt in (None, "category"):
            bucket = "other"
        else:
            continue
        if len(buckets[bucket]) < per_type:
            buckets[bucket].append({"url": u, "reason": "heurystyka URL"})
    return buckets


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


def check_http_headers(base_url: str) -> dict:
    result: dict = {"hsts": False, "compression": None, "cache_control": None, "x_robots_tag": None}
    try:
        r = requests.head(base_url, timeout=10, allow_redirects=True)
        headers = {k.lower(): v for k, v in r.headers.items()}
        result["hsts"] = "strict-transport-security" in headers
        enc = headers.get("content-encoding", "")
        result["compression"] = enc if enc else None
        result["cache_control"] = headers.get("cache-control")
        result["x_robots_tag"] = headers.get("x-robots-tag")
    except Exception as e:
        result["error"] = str(e)
    return result


def _psi_access_token() -> str | None:
    """Build OAuth bearer token from service account JSON. Cached ~50min."""
    import time as _t
    if not GOOGLE_APPLICATION_CREDENTIALS or not os.path.exists(GOOGLE_APPLICATION_CREDENTIALS):
        return None
    now = _t.time()
    if _PSI_TOKEN_CACHE["token"] and _PSI_TOKEN_CACHE["exp"] > now + 60:
        return _PSI_TOKEN_CACHE["token"]
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests as _gar
        creds = service_account.Credentials.from_service_account_file(
            GOOGLE_APPLICATION_CREDENTIALS,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        creds.refresh(_gar.Request())
        _PSI_TOKEN_CACHE["token"] = creds.token
        _PSI_TOKEN_CACHE["exp"] = creds.expiry.timestamp() if creds.expiry else now + 3000
        return creds.token
    except Exception:
        return None


def check_pagespeed(url: str) -> dict:
    """Call PageSpeed Insights API. Prefers API key, falls back to service account OAuth."""
    params = {"url": url, "strategy": "mobile"}
    headers = {}
    if PAGESPEED_KEY:
        params["key"] = PAGESPEED_KEY
    else:
        token = _psi_access_token()
        if not token:
            return {"available": False, "error": "no PAGESPEED_KEY and no service account"}
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
            params=params, headers=headers, timeout=90,
        )
        r.raise_for_status()
        data = r.json()
        cats = data.get("lighthouseResult", {}).get("categories", {})
        perf_score = round((cats.get("performance", {}).get("score") or 0) * 100)
        audits = data.get("lighthouseResult", {}).get("audits", {})

        def _ms(aid: str):
            v = audits.get(aid, {}).get("numericValue")
            return round(v) if v is not None else None

        cls_raw = audits.get("cumulative-layout-shift", {}).get("numericValue")
        return {
            "available": True,
            "performance_score": perf_score,
            "lcp_ms": _ms("largest-contentful-paint"),
            "fcp_ms": _ms("first-contentful-paint"),
            "tbt_ms": _ms("total-blocking-time"),
            "cls": round(cls_raw, 3) if cls_raw is not None else None,
            "strategy": "mobile",
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


# Per-URL performance scoring thresholds (mobile, Lighthouse v10 norms).
def perf_to_scores(ps: dict) -> dict:
    """Convert PSI response to dict of {factor_id: {score, note}}."""
    if not ps or not ps.get("available"):
        return {}
    out: dict = {}
    p = ps.get("performance_score") or 0
    out["performance_score_mobile"] = {
        "score": 2 if p >= 90 else (1 if p >= 50 else 0),
        "note": f"Lighthouse Performance: {p}/100 (mobile).",
    }
    lcp = ps.get("lcp_ms")
    if lcp is not None:
        out["lcp_mobile_ok"] = {
            "score": 2 if lcp <= 2500 else (1 if lcp <= 4000 else 0),
            "note": f"LCP: {lcp/1000:.2f}s (cel <2.5s; źle >4s).",
        }
    cls = ps.get("cls")
    if cls is not None:
        out["cls_mobile_ok"] = {
            "score": 2 if cls <= 0.1 else (1 if cls <= 0.25 else 0),
            "note": f"CLS: {cls:.3f} (cel <0.1; źle >0.25).",
        }
    tbt = ps.get("tbt_ms")
    if tbt is not None:
        out["tbt_mobile_ok"] = {
            "score": 2 if tbt <= 200 else (1 if tbt <= 600 else 0),
            "note": f"TBT: {tbt}ms (cel <200ms; źle >600ms).",
        }
    fcp = ps.get("fcp_ms")
    if fcp is not None:
        out["fcp_mobile_ok"] = {
            "score": 2 if fcp <= 1800 else (1 if fcp <= 3000 else 0),
            "note": f"FCP: {fcp/1000:.2f}s (cel <1.8s; źle >3s).",
        }
    return out


def build_domain_tech_scores(robots: dict, sitemap: dict, llms: dict, homepage_html_checks: dict, http_headers: dict = None) -> dict:
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
    if http_headers:
        s["hsts_enabled"] = 2 if http_headers.get("hsts") else 0
        s["compression_enabled"] = 2 if http_headers.get("compression") else 0
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

    dl_count = len(body_soup.find_all("dl"))
    tables = body_soup.find_all("table")
    th_scope_count = sum(1 for t in tables for th in t.find_all("th") if th.get("scope"))

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
        "rag_signals": {"dl_count": dl_count, "th_scope_count": th_scope_count},
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
    samples: list[dict] = []
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
                compact = _compact_schema_node(node)
                if compact and len(samples) < 8:
                    samples.append(compact)
    types_lower = {t.lower() for t in found_types}
    return {
        "any": bool(found_types),
        "types": sorted(found_types),
        "samples": samples,
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


def _compact_schema_node(node: dict) -> dict:
    keep = [
        "@type",
        "name",
        "headline",
        "description",
        "url",
        "sameAs",
        "datePublished",
        "dateModified",
        "author",
        "publisher",
        "offers",
        "price",
        "priceCurrency",
        "aggregateRating",
        "review",
        "address",
        "telephone",
        "email",
    ]
    compact = {}
    for key in keep:
        if key in node:
            compact[key] = _compact_schema_value(node[key])
    return compact


def _compact_schema_value(value, depth: int = 0):
    if depth > 2:
        return "..."
    if isinstance(value, dict):
        out = {}
        for key in [
            "@type",
            "name",
            "url",
            "price",
            "priceCurrency",
            "ratingValue",
            "reviewCount",
            "streetAddress",
            "addressLocality",
            "postalCode",
            "addressCountry",
            "telephone",
            "email",
        ]:
            if key in value:
                out[key] = _compact_schema_value(value[key], depth + 1)
        if out:
            return out
        if value.get("@type"):
            return {"@type": value.get("@type")}
        return {}
    if isinstance(value, list):
        return [_compact_schema_value(item, depth + 1) for item in value[:4]]
    if isinstance(value, str):
        return value[:300]
    return value


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
    return round((sum(score_value(value) for value in tech_scores.values()) / len(tech_scores)) * 100)


def weighted_domain_tech_score_pct(tech_scores: dict) -> int:
    """Domain tech scored by DOMAIN_TECH_WEIGHTS — critical AI-access factors dominate."""
    if not tech_scores:
        return 0
    total_weighted = sum(
        score_value(tech_scores.get(k, 0)) * w
        for k, w in DOMAIN_TECH_WEIGHTS.items()
        if k in tech_scores
    )
    total_weight = sum(w for k, w in DOMAIN_TECH_WEIGHTS.items() if k in tech_scores)
    if total_weight == 0:
        return 0
    return round((total_weighted / total_weight) * 100)


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


# Domyślnie model z WYSZUKIWANIEM W SIECI (jak realny ChatGPT) — dzięki temu sekcja
# "Jak Cię widzą LLM-y" zna nawet niszowe firmy, których nie ma w danych treningowych.
# Fallback: lżejszy model z wyszukiwaniem, a na końcu zwykłe modele (gdyby search-preview
# był niedostępny dla klucza).
GPT_MODEL = os.getenv("GPT_MODEL", "gpt-4o-search-preview")
GPT_FALLBACK_MODELS = [m.strip() for m in os.getenv(
    "GPT_FALLBACK_MODELS", "gpt-4o-mini-search-preview,gpt-4o,gpt-4o-mini"
).split(",") if m.strip()]


def _is_search_model(model: str) -> bool:
    return "search" in (model or "").lower()


def _openai_call(prompt: str, model: str = "", max_tokens: int = 500) -> tuple[str, str]:
    """Wywołanie OpenAI z retry/backoff na 429 (rate limit) i 5xx oraz fallbackiem modeli.

    Zwraca (tekst, użyty_model). Modele *-search-preview wyszukują w internecie
    (dodajemy `web_search_options`), więc rozpoznają firmy spoza danych treningowych.

    OpenAI zwraca 429 zarówno przy chwilowym rate-limicie (warto ponowić), jak i przy
    wyczerpanym limicie konta `insufficient_quota` (ponawianie nie pomoże). Rozróżniamy
    te przypadki: quota → szybki, czytelny błąd; rate-limit/5xx → backoff i ponowienie.
    """
    models = [model] if model else list(dict.fromkeys([GPT_MODEL, *GPT_FALLBACK_MODELS]))
    last_err: Exception | None = None
    for mdl in models:
        payload: dict = {"model": mdl, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens}
        if _is_search_model(mdl):
            # Wymuś korzystanie z wyszukiwarki przez model (jak ChatGPT z web search).
            payload["web_search_options"] = {}
        for attempt in range(4):  # do 4 prób na model
            try:
                r = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GPT_KEY}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=40,
                )
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"]["content"], mdl
                # Spróbuj wyłuskać kod błędu OpenAI z treści odpowiedzi.
                err_code = ""
                try:
                    err_code = (r.json().get("error") or {}).get("code", "") or ""
                except Exception:
                    pass
                if r.status_code == 429 and err_code == "insufficient_quota":
                    raise RuntimeError("OpenAI: wyczerpany limit konta (insufficient_quota) — doładuj kredyty/billing.")
                if r.status_code in (400, 404):
                    # Błąd deterministyczny (model niedostępny / nieobsługiwany parametr) —
                    # nie ma sensu ponawiać tego samego modelu, próbujemy kolejny.
                    last_err = RuntimeError(f"OpenAI {r.status_code} (model {mdl}): {err_code or 'niedostępny'}")
                    break
                if r.status_code in (429, 500, 502, 503, 504):
                    last_err = RuntimeError(f"OpenAI {r.status_code} (model {mdl})")
                    if attempt < 3:
                        # Respektuj Retry-After jeśli jest, inaczej backoff wykładniczy.
                        try:
                            wait = float(r.headers.get("Retry-After", ""))
                        except (TypeError, ValueError):
                            wait = 0.0
                        time.sleep(max(wait, 1.5 * (2 ** attempt)))
                        continue
                    break  # wyczerpane próby na tym modelu → następny model
                r.raise_for_status()
            except RuntimeError:
                raise
            except Exception as e:  # noqa: BLE001 — błąd sieci itp.
                last_err = e
                if attempt < 3:
                    time.sleep(1.5 * (2 ** attempt))
                    continue
    raise last_err or RuntimeError("OpenAI: nieznany błąd wywołania.")


def _perplexity_brand_call(prompt: str, max_tokens: int = 600) -> dict:
    r = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {PERPLEXITY_KEY}", "Content-Type": "application/json"},
        json={
            "model": "sonar-pro",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return {
        "text": data["choices"][0]["message"]["content"],
        "citations": data.get("citations", []),
    }


def generate_brand_perception(domain: str, title: str) -> dict:
    """Ask Gemini, Perplexity and GPT what they know about this brand.

    Deliberately does NOT pass page content — we want to see what each model
    independently knows (from training data or live web search), not what we tell it.
    """
    prompt = (
        f"Czym zajmuje się firma działająca pod adresem {domain}? "
        f"Co oferuje i dla kogo? Czy masz o niej jakiekolwiek informacje? "
        f"Odpowiedz szczerze — jeśli nie masz informacji o tej konkretnej firmie, napisz to wprost. "
        f"Odpowiedź powinna mieć 3–5 zdań."
    )
    results: dict = {}

    def _ask_gemini() -> None:
        try:
            text = _gemini_call(prompt, temperature=0.1, max_tokens=400)
            results["gemini"] = {"text": text, "available": True, "source": "training_data"}
        except Exception as e:
            results["gemini"] = {"available": False, "error": str(e)}

    def _ask_perplexity() -> None:
        if not PERPLEXITY_KEY:
            results["perplexity"] = {"available": False, "error": "PERPLEXITY_KEY not set"}
            return
        try:
            data = _perplexity_brand_call(prompt)
            results["perplexity"] = {
                "text": data["text"],
                "citations": data["citations"],
                "available": True,
                "source": "web_search",
            }
        except Exception as e:
            results["perplexity"] = {"available": False, "error": str(e)}

    def _ask_gpt() -> None:
        if not GPT_KEY:
            results["chatgpt"] = {"available": False, "error": "GPT_KEY not set"}
            return
        try:
            text, used_model = _openai_call(prompt)
            results["chatgpt"] = {
                "text": text,
                "available": True,
                "source": "web_search" if _is_search_model(used_model) else "training_data",
                "model": used_model,
            }
        except Exception as e:
            results["chatgpt"] = {"available": False, "error": str(e)}

    with ThreadPoolExecutor(max_workers=3) as executor:
        futs = [executor.submit(_ask_gemini), executor.submit(_ask_perplexity), executor.submit(_ask_gpt)]
        for f in as_completed(futs):
            _ = f.result()  # surface exceptions to log

    return results


def analyze_brand_gaps(perception: dict, domain: str) -> dict:
    """Use Gemini to compare the three model responses and surface discrepancies."""
    texts = []
    for model_name in ["gemini", "perplexity", "chatgpt"]:
        d = perception.get(model_name, {})
        if d.get("available") and d.get("text"):
            texts.append(f"{model_name.upper()}: {d['text']}")
    if not texts:
        return {"available": False, "error": "no model responses available"}

    models_str = "\n\n".join(texts)
    prompt = f"""Poniżej masz odpowiedzi różnych modeli AI na pytanie o firmę pod adresem {domain}:

{models_str}

Przeanalizuj i zwróć JSON (bez markdown, bez komentarzy):
{{
  "brand_known_by": ["nazwy modeli które naprawdę coś wiedziały, np. gemini, perplexity, chatgpt"],
  "discrepancies": ["max 3 rozbieżności między modelami — co inaczej opisują lub czemu jeden wie a drugi nie"],
  "gaps": ["max 3 informacje których żaden nie wspomniał, a powinien dla typowej firmy"],
  "ai_brand_score": 0,
  "score_rationale": "jedno zdanie wyjaśniające wynik",
  "recommendation": "jedna konkretna rekomendacja poprawy rozpoznawalności marki w AI"
}}

ai_brand_score: 0=nieznana przez żaden model, 100=doskonale i spójnie opisana przez wszystkie.
Jeśli wszystkie modele nie mają informacji — score=0."""
    try:
        result = _extract_json(_gemini_call(prompt, temperature=0.2, max_tokens=900))
        result["available"] = True
        return result
    except Exception as e:
        return {"available": False, "error": str(e)}


def _send_lead_email(record: dict) -> None:
    """Best-effort e-mail notification for new lead. Requires LEADS_EMAIL + SMTP_USER + SMTP_PASS."""
    if not (LEADS_EMAIL and SMTP_USER and SMTP_PASS):
        return
    try:
        score_str = f"  Wynik audytu: {record['score']}\n" if record.get("score") is not None else ""
        body = (
            f"Nowy lead z audytu AI SEO\n"
            f"{'─' * 36}\n"
            f"  E-mail:  {record['email']}\n"
            f"  Domena:  {record.get('url', '—')}\n"
            f"{score_str}"
            f"  Czas:    {record['ts']}\n"
        )
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = f"[Kopernik] Lead: {record['email']}"
        msg["From"] = SMTP_USER
        msg["To"] = LEADS_EMAIL
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [LEADS_EMAIL], msg.as_bytes())
    except Exception as e:
        print(f"LEAD_EMAIL_ERR: {e}", flush=True)


def _save_lead_to_firestore(record: dict) -> None:
    """Best-effort Firestore write via REST (no client library required)."""
    project = FIRESTORE_PROJECT
    if not project:
        return
    try:
        tok_r = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
            timeout=5,
        )
        token = tok_r.json()["access_token"]
        fields = {k: {"stringValue": str(v) if v is not None else ""} for k, v in record.items()}
        requests.post(
            f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents/leads",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"fields": fields},
            timeout=10,
        )
    except Exception:
        pass  # stdout already persists the lead


# --- Zapis / odczyt raportów (do udostępniania linkiem i przywoływania ponownie) ---
# Pamięć procesu (przeżywa w obrębie instancji) + best-effort Firestore (między instancjami).
_REPORTS_MEMORY: dict[str, dict] = {}
_REPORTS_LOCK = threading.Lock()
_REPORTS_MAX = 200


def _report_key(domain_or_url: str) -> str:
    """Stabilny klucz dokumentu z domeny (bez www, bez schematu, znormalizowany)."""
    s = (domain_or_url or "").strip().lower()
    try:
        if "://" in s:
            s = urlparse(s).netloc or s
    except Exception:
        pass
    s = s.split("/")[0].replace("www.", "")
    return re.sub(r"[^a-z0-9.-]", "_", s)[:200]


def _save_report_to_firestore(key: str, payload: dict) -> None:
    project = FIRESTORE_PROJECT
    if not project:
        return
    try:
        gz = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        data_b64 = base64.b64encode(gz).decode("ascii")
        # Firestore: limit ~1 MiB na dokument — przy bardzo dużych raportach zapis się nie powiedzie
        # (best-effort; pamięć procesu i tak działa).
        tok_r = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
            timeout=5,
        )
        token = tok_r.json()["access_token"]
        fields = {
            "data_gz_b64": {"stringValue": data_b64},
            "url": {"stringValue": str(payload.get("url", ""))},
            "ts": {"stringValue": datetime.utcnow().isoformat() + "Z"},
        }
        requests.patch(
            f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents/reports/{key}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"fields": fields},
            timeout=15,
        )
    except Exception as e:  # noqa: BLE001
        logging.info("Firestore report save skipped: %s", e)


def _load_report_from_firestore(key: str) -> dict | None:
    project = FIRESTORE_PROJECT
    if not project:
        return None
    try:
        tok_r = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
            timeout=5,
        )
        token = tok_r.json()["access_token"]
        r = requests.get(
            f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents/reports/{key}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        b64 = (r.json().get("fields", {}).get("data_gz_b64", {}) or {}).get("stringValue", "")
        if not b64:
            return None
        return json.loads(gzip.decompress(base64.b64decode(b64)).decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        logging.info("Firestore report load failed: %s", e)
        return None


def save_report(result: dict) -> str:
    """Zapisuje raport pod kluczem domeny. Zwraca klucz (do linku udostępniania)."""
    key = _report_key(result.get("url", ""))
    if not key:
        return ""
    with _REPORTS_LOCK:
        _REPORTS_MEMORY[key] = result
        if len(_REPORTS_MEMORY) > _REPORTS_MAX:
            # usuń najstarszy wpis (FIFO)
            try:
                _REPORTS_MEMORY.pop(next(iter(_REPORTS_MEMORY)))
            except StopIteration:
                pass
    threading.Thread(target=_save_report_to_firestore, args=(key, result), daemon=True).start()
    return key


def load_report(domain_or_url: str) -> dict | None:
    key = _report_key(domain_or_url)
    if not key:
        return None
    with _REPORTS_LOCK:
        cached = _REPORTS_MEMORY.get(key)
    if cached is not None:
        return cached
    fetched = _load_report_from_firestore(key)
    if fetched is not None:
        with _REPORTS_LOCK:
            _REPORTS_MEMORY[key] = fetched
    return fetched


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1:
        text = text[first : last + 1]
    return json.loads(text)


def _page_factor_prompt(page_type: str, url: str, title: str, meta_desc: str, content: str, html_checks: dict | None = None, sitemap_urls: list[str] | None = None) -> str:
    spec = PAGE_TYPE_FACTORS[page_type]
    patent_factors = patent_factor_ids_for_page_type(page_type)
    factors = spec["factors"] + patent_factors
    factor_spec = "\n".join(f'    "{f}": {{"score": 0, "note": "konkretna obserwacja po polsku (cytat/parafraza fragmentu)"}}' for f in factors)
    label = PAGE_TYPE_LABELS.get(page_type, page_type)
    patent_prompt = _build_patent_factor_prompt(page_type)
    patent_section = f"""
<czynniki_z_patentow_google>
To są patent-derived SEO audit signals: traktuj je jako inferencje audytowe z patentów, nie jako dowód aktualnego algorytmu rankingu.
Oceniaj wyłącznie czynniki poniżej i tylko na podstawie dostępnej treści, HTML/schema oraz metadanych tej strony.
Nie wymyślaj danych z GSC, analytics, backlinków ani zewnętrznego SERP-u.
{patent_prompt}
</czynniki_z_patentow_google>
""" if patent_prompt else ""
    html_summary = _build_html_prompt_summary(html_checks)
    cluster_section = ""
    if sitemap_urls and "pillar_or_cluster_page_structure_signals" in factors:
        from urllib.parse import urlparse as _up
        slugs = []
        for u in sitemap_urls[:120]:
            p = _up(u).path.strip("/")
            if p and p != _up(url).path.strip("/"):
                slugs.append(f"- {p}")
        if slugs:
            cluster_section = f"""
<sitemap_urls_dla_oceny_pillar_cluster>
Poniżej lista innych URL-i z tej witryny (slugi). Użyj ich do oceny czynnika `pillar_or_cluster_page_structure_signals`:
- score=2: ta strona jest centrum (pillar) klastra LUB należy do wyraźnego klastra tematycznego (≥3 powiązane slugi)
- score=1: pojedyncze powiązania, brak wyraźnego klastra
- score=0: izolowana, brak powiązanych tematów w sitemap
W "note" wskaż 2-3 powiązane slugi lub stwierdź ich brak.
{chr(10).join(slugs)}
</sitemap_urls_dla_oceny_pillar_cluster>
"""

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
WAŻNE DLA PATENTÓW: czynniki patentowe oceniaj jako sprawdzalne proxy treści/HTML. Jeśli patentowy czynnik wymaga danych niedostępnych w audycie, oceń tylko to, co wynika ze strony i napisz w note, jakie dane byłyby potrzebne do pełnej weryfikacji.
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

<html_schema_i_sygnaly_techniczne>
{html_summary}
</html_schema_i_sygnaly_techniczne>

{patent_section}
{cluster_section}
<treść>
{content}
</treść>

WSZYSTKIE "note" po polsku. Konkret > ogólnik.

Zwróć TYLKO poprawny JSON (bez markdown):
{{
{factor_spec}
}}"""


def analyze_page(url: str, page_type: str, title: str, meta_desc: str, content: str, html_checks: dict | None = None, sitemap_urls: list[str] | None = None) -> dict:
    prompt = _page_factor_prompt(page_type, url, title, meta_desc, content, html_checks, sitemap_urls)
    return _extract_json(_gemini_call(prompt, temperature=0.15, max_tokens=5000))


def generate_fan_out(page_url: str, page_title: str, content: str) -> dict:
    prompt = f"""Jesteś ekspertem AI SEO. Symulujesz query fan-out — zestaw pytań, które użytkownicy zadają ChatGPT/Perplexity w temacie tej konkretnej podstrony.

<zadanie>
1. Na podstawie WYŁĄCZNIE tej jednej podstrony wygeneruj 12 realistycznych pytań, które użytkownicy wpisują w ChatGPT/Perplexity szukając tematu tej strony.
   Różne intencje: informacyjne, transakcyjne, porównawcze, problem-solving.
2. Oceń każde pytanie pod kątem pokrycia przez treść TEJ strony: "covered" | "partial" | "missing".
3. Jeśli partial/missing — napisz konkretnie co trzeba dodać na tej stronie.
</zadanie>

<audytowana_podstrona>
<url>{page_url}</url>
<title>{page_title}</title>
</audytowana_podstrona>

<treść_podstrony>
{content}
</treść_podstrony>

Zwróć TYLKO JSON (po polsku). Wszystkie pytania muszą dotyczyć tematu strony {page_url}:
{{
  "audited_url": "{page_url}",
  "queries": [
    {{"query": "pytanie użytkownika", "coverage": "covered|partial|missing", "gap_note": "co dodać na tej stronie (pusty jeśli covered)"}}
  ]
}}

Dokładnie 12 pytań, różnorodne intencje, wszystkie tematycznie związane z tą podstroną."""
    return _extract_json(_gemini_call(prompt, temperature=0.4, max_tokens=3000))


def generate_ai_snippet_preview(url: str, title: str, content: str) -> dict:
    if not PERPLEXITY_KEY:
        return {"available": False}
    try:
        snippet_content = content[:4000]
        prompt = (
            f"Na podstawie poniższej strony internetowej odpowiedz na pytanie użytkownika: "
            f"\"Czym zajmuje się {title} i co oferuje?\"\n\n"
            f"Strona: {url}\nTreść:\n{snippet_content}\n\n"
            f"Odpowiedz zwięźle (3-4 zdania), tak jak AI asystent odpowiadałby użytkownikowi."
        )
        r = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {PERPLEXITY_KEY}", "Content-Type": "application/json"},
            json={
                "model": "sonar-pro",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        snippet = data["choices"][0]["message"]["content"]
        return {"available": True, "snippet": snippet, "model": "sonar-pro"}
    except Exception as e:
        return {"available": False, "error": str(e)}


def synthesize_findings(page_audits: list[dict], domain_tech: dict, domain_tech_scores: dict, fan_out: dict, homepage_url: str, site_title: str, sitemap_urls: list[str] = None) -> dict:
    """Generate prioritized recommendations with URL + page_type refs."""
    weak_per_page: list[str] = []
    for pa in page_audits:
        url = pa.get("url", "")
        pt = pa.get("page_type", "other")
        for k, v in (pa.get("factors") or {}).items():
            if isinstance(v, dict) and v.get("score", 0) == 0:
                note = v.get("note", "")
                meta = FACTOR_META.get(k, {})
                label = meta.get("label", k)
                source = " [patent]" if meta.get("source") == "google_patent" else ""
                weak_per_page.append(f"[{pt} | {url}] {label}{source}: {note}")
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

    from urllib.parse import urlparse, unquote
    topics = []
    if sitemap_urls:
        for u in sitemap_urls[:100]:
            path = urlparse(u).path.strip('/')
            if path:
                slug = path.split('/')[-1]
                topic = unquote(slug).replace('-', ' ').replace('_', ' ').capitalize()
                if topic:
                    topics.append(topic)
    topics_list = "\n".join(f"- {t}" for t in topics) or "(brak tematów z sitemapy)"

    prompt = f"""Jesteś starszym konsultantem AI SEO. Masz wyniki per-URL audytu. Stwórz syntetyczny werdykt + rekomendacje z przypisaniem do KONKRETNEJ podstrony.

<input>
<strona>{homepage_url}</strona>
<tytuł_domeny>{site_title}</tytuł_domeny>

<lista_istniejacych_tematow_z_sitemapy>
{topics_list}
</lista_istniejacych_tematow_z_sitemapy>

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
- "content_gaps": przeanalizuj <lista_istniejacych_tematow_z_sitemapy>. Wygeneruj 5 NOWYCH tematów, których wyraźnie brakuje na blogu/stronie, ale są istotne w tej niszy. Zwróć jako konkretne tytuły artykułów/podstron (nie kategorie).
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


def translate_for_client_mode(page_audits: list[dict], synthesis: dict, scores: dict, fan_out: dict, site_title: str) -> dict:
    """Osobne zapytanie — tłumaczy techniczny audyt na język klienta-laika (bez żargonu)."""
    critical: list[str] = []
    for pa in page_audits:
        url = pa.get("url", "")
        pt = pa.get("page_type", "other")
        for k, v in (pa.get("factors") or {}).items():
            if isinstance(v, dict) and v.get("score", 0) == 0:
                label = FACTOR_META.get(k, {}).get("label", k)
                critical.append(f"[{pt} | {url}] {label}: {v.get('note','')}")
    critical = critical[:15]

    top_recs = synthesis.get("top_recommendations", [])[:6]
    top_recs_txt = "\n".join(f"- [{r.get('page_type','?')} | {r.get('page_url','?')}] {r.get('text','')}" for r in top_recs) or "(brak)"

    missing_queries = [q.get("query","") for q in fan_out.get("queries", []) if q.get("coverage") in ("missing","partial")][:8]

    overall_score = scores.get("overall", 0)
    verdict_src = synthesis.get("overall_assessment", "")

    prompt = f"""Jesteś copywriterem B2B tłumaczącym audyt AI SEO dla właściciela firmy BEZ wiedzy technicznej.

<kontekst>
Strona: {site_title}
Wynik ogólny: {overall_score}/100
Werdykt ekspercki (do przetłumaczenia na prosty język): {verdict_src}

Najważniejsze rekomendacje techniczne:
{top_recs_txt}

Krytyczne braki treści per podstrona:
{chr(10).join(f"- {c}" for c in critical) or "(brak)"}

Brakujące pytania klientów (fan-out):
{chr(10).join(f"- {q}" for q in missing_queries) or "(brak)"}
</kontekst>

<zasady_tlumaczenia>
ZERO żargonu. NIE używaj słów: RAG, E-E-A-T, crawler, LLM, schema, entity, canonical, meta, markup, fan-out, topical authority, indeksowanie, ekstraktywność.
ZAMIAST: "schema" → "oznaczenia które pomagają AI zrozumieć stronę"; "E-E-A-T" → "oznaki że jesteś ekspertem"; "crawler/LLM" → "AI takie jak ChatGPT i Perplexity"; "fan-out" → "pytania które klienci zadają AI".
Mów językiem korzyści: "więcej klientów z AI", "AI będzie Cię polecać", "stracisz klientów którzy pytają ChatGPT".
Każde wyjaśnienie 1-2 zdania MAX. Krótko, konkretnie, po polsku.
</zasady_tlumaczenia>

Zwróć TYLKO JSON:
{{
  "client_verdict": "3-4 zdania dla właściciela firmy: co to oznacza dla biznesu, czy jest źle/średnio/dobrze, co zyska jeśli poprawi",
  "client_recommendations": [
    {{"priority": 1, "action": "Co zrobić (prosty język)", "why_matters": "Co zyska biznes (1 zdanie)", "page_url": "...", "page_type": "..."}},
    ... (6 pozycji, kolejność jak w rekomendacjach eksperckich)
  ],
  "client_content_gaps": ["Temat 1 który warto opisać na blogu (prosty język, dlaczego warto)", ... 5 pozycji],
  "client_next_step": "1 zdanie: najprostszy pierwszy krok jeśli klient chce sam zacząć"
}}"""
    return _extract_json(_gemini_call(prompt, temperature=0.4, max_tokens=2500))


def generate_strategic_overview(page_audits: list[dict], synthesis: dict, scores: dict, fan_out: dict, site_title: str, client_mode_obj: dict | None = None) -> dict:
    """Strategiczne streszczenie wykonawcze (executive summary) + 5 priorytetów biznesowych.
    Inny poziom abstrakcji niż 'Najpilniejsze akcje' (factor-level) — myśli kierunkami biznesowymi."""
    overall = scores.get("overall", 0)
    group_scores = scores.get("groups", {}) or {}
    verdict_src = synthesis.get("overall_assessment", "")

    top_recs = synthesis.get("top_recommendations", [])[:8]
    top_recs_txt = "\n".join(f"- [{r.get('page_type','?')}] {r.get('text','')}" for r in top_recs) or "(brak)"

    gaps = (client_mode_obj or {}).get("client_content_gaps") or synthesis.get("content_gaps", [])
    gaps_txt = "\n".join(f"- {g}" for g in gaps[:6]) or "(brak)"

    missing_queries = [q.get("query","") for q in fan_out.get("queries", []) if q.get("coverage") in ("missing","partial")][:8]
    queries_txt = "\n".join(f"- {q}" for q in missing_queries) or "(brak)"

    group_summary = ", ".join(f"{k}:{v}" for k, v in group_scores.items()) or "(brak)"

    prompt = f"""Jesteś analitykiem AI SEO przygotowującym DIAGNOSTYCZNE streszczenie audytu dla zarządu.

<kontekst>
Strona: {site_title}
Wynik ogólny: {overall}/100
Wyniki kategorii: {group_summary}
Werdykt ekspercki: {verdict_src}

Rekomendacje techniczne (do uogólnienia, NIE kopiuj 1:1):
{top_recs_txt}

Luki treści:
{gaps_txt}

Brakujące pytania klientów w AI:
{queries_txt}
</kontekst>

<zadanie>
Przygotuj streszczenie stanu domeny po audycie + 5 priorytetów do naprawy — w DWÓCH wersjach językowych tego samego przekazu:

WERSJA A — DIAGNOSTYCZNA (pola: headline, summary, priorities[].title/rationale/outcome)
NEUTRALNA, rzeczowa, dla CEO. ZAKAZ sprzedażowego tonu. NIE mów "co stracicie", "co zyskacie", "klienci trafią do konkurencji", "więcej leadów", "wzrost przychodów". NIE buduj urgency. Po prostu OPISZ stan i wskaż największe problemy. Bez żargonu SEO (no schema/E-E-A-T/canonical/crawler/RAG).

WERSJA B — SPRZEDAŻOWA (pola: headline_sales, summary_sales, priorities[].title_sales/rationale_sales/outcome_sales)
Prosty język dla właściciela firmy BEZ wiedzy technicznej. Mów językiem korzyści i konsekwencji biznesowych: "klienci pytający ChatGPT/Perplexity Cię nie znajdą", "AI nie poleci Twojej firmy", "zyskasz widoczność w odpowiedziach AI". ZERO żargonu (zamiast "schema/E-E-A-T/crawler/RAG/llms.txt" → proste opisy: "AI takie jak ChatGPT i Perplexity", "oznaki, że jesteś ekspertem", "otwarcie strony dla botów AI"). Krótko, konkretnie — ale BEZ przesady i fałszywych obietnic liczbowych.

KLUCZOWE — nagłówek tej listy brzmi "Największe problemy", więc title_sales i rationale_sales MUSZĄ PIĘTNOWAĆ BRAK/PROBLEM, a nie opisywać cel czy rozwiązanie.
ŹLE (cel/rozwiązanie): "Pokazanie sukcesów Twojej firmy", "Budowanie wizerunku eksperta", "Ułatwienie botom AI zrozumienia oferty".
DOBRZE (brak/problem): "Brak ekspozycji opinii i realizacji", "Brak dowodów eksperckości dla AI", "Oferta nieczytelna dla AI", "Brak otwartego dostępu dla botów AI".
Zaczynaj title_sales od słów typu "Brak…", "Niejasne…", "Słabe…", "Niewidoczne…" — to ma być nazwa problemu prostym językiem, nie żargon techniczny. Dopiero outcome_sales (pole "→") mówi językiem korzyści, co firma zyska po naprawie.

Priorytety w obu wersjach to TE SAME 5 obszarów, w tej samej kolejności (od najpilniejszego) — różni się tylko ton.
</zadanie>

Zwróć TYLKO JSON:
{{
  "headline": "WERSJA A: 1 zdanie — rzeczowa diagnoza stanu domeny (np. 'Domena ma solidne fundamenty techniczne, ale brakuje sygnałów eksperckości i otwartego dostępu dla AI')",
  "summary": "WERSJA A: 3-4 zdania opisujące obecny stan: co działa dobrze, gdzie są największe luki, jaki jest największy pojedynczy problem. Neutralnie, bez sprzedaży.",
  "headline_sales": "WERSJA B: 1 zdanie prostym językiem korzyści dla właściciela firmy — co ten audyt oznacza dla jego biznesu w świecie AI.",
  "summary_sales": "WERSJA B: 3-4 zdania prostym językiem sprzedażowym: gdzie firma traci szansę w AI, co jest największym problemem i co realnie zmieni jego naprawa. Bez żargonu.",
  "priorities": [
    {{"title": "WERSJA A: obszar problemu (2-5 słów)", "rationale": "WERSJA A: co konkretnie nie działa lub czego brakuje (1 zdanie, opis stanu)", "outcome": "WERSJA A: co zostanie rozwiązane po naprawie (1 zdanie, opisowo, BEZ obietnic biznesowych)", "title_sales": "WERSJA B: NAZWA PROBLEMU/BRAKU prostym językiem, NIE cel ani rozwiązanie (np. 'Brak ekspozycji opinii i realizacji', 'Oferta nieczytelna dla AI'). 2-7 słów, zwykle zaczyna się od 'Brak…/Niejasne…/Słabe…'.", "rationale_sales": "WERSJA B: dlaczego ten brak szkodzi biznesowi w świecie AI (1 zdanie, prosto, językiem konsekwencji)", "outcome_sales": "WERSJA B: co firma zyska po naprawie (1 zdanie, język korzyści — to jedyne pole pozytywne)"}},
    ... (DOKŁADNIE 5 pozycji, posortowane od największego problemu)
  ]
}}"""
    return _extract_json(_gemini_call(prompt, temperature=0.35, max_tokens=2600))


# --- SCORING ---

def factor_score_pct(factors: dict) -> int:
    """Weighted average: EEAT and patent-derived signals carry more weight than generic content checks."""
    if not factors:
        return 0
    total_weighted = 0.0
    total_max = 0.0
    for key, v in factors.items():
        if not isinstance(v, dict) or "score" not in v:
            continue
        category = FACTOR_META.get(key, {}).get("category", "")
        w = CONTENT_CATEGORY_WEIGHTS.get(category, 1.5)
        total_weighted += score_value(v["score"]) * w
        total_max += w
    if total_max == 0:
        return 0
    return round((total_weighted / total_max) * 100)


def fan_out_score(fan_out: dict) -> int:
    queries = fan_out.get("queries", [])
    if not queries:
        return 0
    pts = sum({"covered": 1.0, "partial": 0.35, "missing": 0.0}.get(q.get("coverage", "missing"), 0) for q in queries)
    return round(pts / len(queries) * 100)


def combined_page_score(factor_pct: int, tech_pct: int) -> int:
    return round(factor_pct * 0.6 + tech_pct * 0.4)


# --- SSE AUDIT STREAM ---

def audit_stream(url: str, picks: list[dict] | None = None):
    def event(step: str, data: dict):
        return f"data: {json.dumps({'step': step, **data})}\n\n"

    try:
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        if picks:
            # User-confirmed selection. Skip Gemini auto-pick.
            yield event("progress", {"message": "Używam podstron wybranych przez użytkownika.", "pct": 10})
            sitemap_urls: list[str] = []
            discovery_source = "user-picked"
        else:
            yield event("progress", {"message": "Wykrywanie podstron (sitemap.xml → Firecrawl /map)...", "pct": 5})
            sitemap_urls = fetch_sitemap_urls(base_url)
            discovery_source = "sitemap" if sitemap_urls else "firecrawl-map"
            if not sitemap_urls:
                sitemap_urls = fetch_firecrawl_map(base_url)
            yield event("progress", {"message": f"Znaleziono {len(sitemap_urls)} URL-i ({discovery_source}). Gemini klasyfikuje + wybiera reprezentację...", "pct": 12})

        homepage_entry = {"url": url, "page_type": "homepage", "reason": "strona główna"}
        url_entries: list[dict] = [homepage_entry]
        seen = {url}

        if picks:
            for p in picks:
                u = (p.get("url") or "").strip()
                pt = p.get("page_type", "other")
                if pt not in PAGE_TYPE_FACTORS:
                    pt = "other"
                if u and u not in seen:
                    url_entries.append({"url": u, "page_type": pt, "reason": "wybór użytkownika"})
                    seen.add(u)
                if len(url_entries) >= MAX_AUDIT_PAGES:
                    break
        else:
            selected = select_and_classify_urls(sitemap_urls, url, base_url) if sitemap_urls else []
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

        yield event("progress", {"message": "Analiza techniczna domeny (robots, sitemap, llms.txt, headers, PageSpeed) + HTML per strona...", "pct": 32})

        # 4. Domain-level checks (PSI runs per-URL later, in performance step)
        with ThreadPoolExecutor(max_workers=4) as _tech_ex:
            _robots_f = _tech_ex.submit(check_robots_txt, base_url)
            _sitemap_f = _tech_ex.submit(check_sitemap, base_url)
            _llms_f = _tech_ex.submit(check_llms_txt, base_url)
            _headers_f = _tech_ex.submit(check_http_headers, base_url)
            robots = _robots_f.result()
            sitemap = _sitemap_f.result()
            llms = _llms_f.result()
            http_headers = _headers_f.result()

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
        domain_tech_scores = build_domain_tech_scores(robots, sitemap, llms, homepage_data["html_checks"], http_headers)

        yield event("progress", {"message": "PageSpeed Insights per URL (mobile)...", "pct": 40})

        # 4b. Per-URL PageSpeed Insights (parallel, mobile strategy)
        def _psi_one(pd):
            ps = check_pagespeed(pd["url"])
            return pd["url"], ps, perf_to_scores(ps)

        psi_map: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=min(len(page_data), 5)) as _psi_ex:
            for fut in as_completed({_psi_ex.submit(_psi_one, pd): pd["url"] for pd in page_data}):
                u, ps, scores = fut.result()
                psi_map[u] = {"raw": ps, "scores": scores}
        for pd in page_data:
            pd["performance_scores"] = psi_map.get(pd["url"], {}).get("scores", {})
            pd["pagespeed_raw"] = psi_map.get(pd["url"], {}).get("raw", {})
        pagespeed = next((m["raw"] for m in psi_map.values() if m.get("raw", {}).get("available")), {"available": False})

        yield event("progress", {"message": "Per-URL analiza: typ strony + czynniki z patentów Google (Gemini, parallel)...", "pct": 48})

        # 5. Parallel per-page Gemini analysis
        _sitemap_urls_for_clusters = (sitemap_urls or [])[:200]
        def _analyze_one(pd):
            content = pd["markdown"][:MAX_CONTENT_CHARS]
            try:
                factors = analyze_page(pd["url"], pd["page_type"], pd["title"], pd["meta_desc"], content, pd["html_checks"], _sitemap_urls_for_clusters)
            except Exception as e:
                factors = {"error": {"score": 0, "note": f"Analiza nieudana: {str(e)[:200]}"}}
            return pd["url"], factors

        with ThreadPoolExecutor(max_workers=5) as ex:
            fut_analyze = {ex.submit(_analyze_one, pd): pd["url"] for pd in page_data}
            analysis_map: dict[str, dict] = {}
            for fut in as_completed(fut_analyze):
                u, factors = fut.result()
                analysis_map[u] = factors

        yield event("progress", {"message": "Symulacja query fan-out dla wybranej podstrony (Gemini)...", "pct": 68})

        # 6. Fan-out on a single representative service or article page (not homepage)
        _fan_out_page = next(
            (pd for pd in page_data if pd["page_type"] == "service"),
            next((pd for pd in page_data if pd["page_type"] == "article"), None),
        )
        if _fan_out_page is None:
            _fan_out_page = next((pd for pd in page_data if pd["page_type"] != "homepage"), page_data[0])
        homepage_title = homepage_data["title"]
        try:
            fan_out = generate_fan_out(
                _fan_out_page["url"],
                _fan_out_page["title"] or homepage_title,
                _fan_out_page["markdown"][:12000],
            )
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
                "performance_scores": pd.get("performance_scores", {}),
                "pagespeed_raw": pd.get("pagespeed_raw", {}),
                "combined_score": combined_page_score(f_pct, t_pct),
                "html_checks": pd["html_checks"],
                "html_checks_summary": {
                    "word_count": pd["html_checks"].get("content", {}).get("word_count", 0),
                    "html_size_kb": pd["html_checks"].get("html_size_kb", 0),
                    "schema_types": pd["html_checks"].get("schema", {}).get("types", []),
                    "h1_count": pd["html_checks"].get("headings", {}).get("h1_count", 0),
                    "internal_links": pd["html_checks"].get("links", {}).get("internal", 0),
                    "external_links": pd["html_checks"].get("links", {}).get("external", 0),
                    "images": pd["html_checks"].get("images", {}),
                    "canonical": pd["html_checks"].get("meta", {}).get("canonical"),
                    "rag_signals": pd["html_checks"].get("rag_signals", {}),
                },
            })

        yield event("progress", {"message": "Synteza: priorytetyzowane rekomendacje per strona...", "pct": 90})

        try:
            synth = synthesize_findings(page_audits, {"robots": robots, "sitemap": sitemap, "llms": llms}, domain_tech_scores, fan_out, url, homepage_title, sitemap_urls)
        except Exception as e:
            synth = {"top_recommendations": [], "content_gaps": [], "overall_assessment": f"Synteza nieudana: {e}"}

        # 8. Aggregate scores + UI index for L1/L2/L3
        domain_tech_pct = weighted_domain_tech_score_pct(domain_tech_scores)
        fan_pct = fan_out_score(fan_out)
        page_scores = [pa["combined_score"] for pa in page_audits] if page_audits else [0]
        avg_page = round(sum(page_scores) / len(page_scores))

        # Legacy category breakdowns kept for debugging/backward comparison.
        legacy_grp = {k: {"val": 0, "max": 0} for k in ("eeat", "topical", "geo", "patent")}
        for pa in page_audits:
            for fk, v in (pa.get("factors") or {}).items():
                cat = FACTOR_META.get(fk, {}).get("category", "")
                if cat in legacy_grp:
                    legacy_grp[cat]["max"] += 2
                    legacy_grp[cat]["val"] += (v.get("score", 0) if isinstance(v, dict) else 0)

        def _grp_pct(g): return round(g["val"] / g["max"] * 100) if g["max"] else 0
        cat_eeat = _grp_pct(legacy_grp["eeat"])
        cat_topical = _grp_pct(legacy_grp["topical"])
        cat_geo = _grp_pct(legacy_grp["geo"])
        cat_patent = _grp_pct(legacy_grp["patent"])

        # Base overall: page scores carry category weights (via factor_score_pct),
        # domain tech is weighted by DOMAIN_TECH_WEIGHTS, fan-out coverage last.
        legacy_base_overall = round(avg_page * 0.55 + domain_tech_pct * 0.30 + fan_pct * 0.15)

        # Per-factor critical penalties — each absent P0 factor subtracts fixed points.
        ds = domain_tech_scores
        penalties = sum(
            penalty for factor, penalty in CRITICAL_FACTOR_PENALTIES.items()
            if ds.get(factor, 2) == 0
        )

        legacy_overall = max(0, legacy_base_overall - penalties)
        homepage_hc = next((pa.get("html_checks") for pa in page_audits if pa.get("page_type") == "homepage"), None) or (page_audits[0].get("html_checks") if page_audits else {})
        domain_tech_raw = {
            "robots": robots,
            "sitemap": sitemap,
            "llms": llms,
            "http_headers": http_headers,
            "homepage_hc": homepage_hc,
        }
        factor_index = build_factor_index(page_audits, domain_tech_scores, domain_tech_raw)
        for pa in page_audits:
            pa.pop("html_checks", None)
        dashboard = build_dashboard(factor_index, page_audits)
        category_scores = {group["id"]: group["score"] if group["score"] is not None else 0 for group in dashboard["groups"]}
        overall = dashboard["overall"]
        scores_obj = {
            "overall": overall,
            "category": category_scores,
            "penalties": penalties,
            "page_average": avg_page,
            "domain_technical": domain_tech_pct,
            "fan_out": fan_pct,
            "legacy": {
                "overall": legacy_overall,
                "base_overall": legacy_base_overall,
                "category": {
                    "eeat": cat_eeat,
                    "topical": cat_topical,
                    "geo": cat_geo,
                    "patent": cat_patent,
                    "accessibility": domain_tech_pct,
                },
            },
        }

        yield event("progress", {"message": "Generowanie podglądu snippeta AI (Perplexity sonar-pro)...", "pct": 93})
        _snippet_page = next(
            (pd for pd in page_data if pd["page_type"] in ("homepage", "service")),
            page_data[0],
        )
        try:
            ai_snippet = generate_ai_snippet_preview(
                _snippet_page["url"],
                _snippet_page["title"] or homepage_title,
                _snippet_page["markdown"][:4000],
            )
        except Exception as e:
            ai_snippet = {"available": False, "error": str(e)}

        yield event("progress", {"message": "Jak Twoją firmę postrzega AI? (Gemini / Perplexity / ChatGPT)...", "pct": 94})
        try:
            _domain = urlparse(url).netloc or url
            brand_perception = generate_brand_perception(_domain, homepage_title)
            brand_gaps = analyze_brand_gaps(brand_perception, _domain)
        except Exception as e:
            brand_perception = {}
            brand_gaps = {"available": False, "error": str(e)}

        yield event("progress", {"message": "Tryb Klient: tłumaczenie wyników na prosty język (osobne zapytanie)...", "pct": 95})
        try:
            client_mode = translate_for_client_mode(page_audits, synth, scores_obj, fan_out, homepage_title)
            client_mode["client_factor_explanations"] = CLIENT_FACTOR_EXPLANATIONS
        except Exception as e:
            client_mode = {"client_verdict": f"Tłumaczenie nieudane: {e}", "client_recommendations": [], "client_content_gaps": [], "client_next_step": "", "client_factor_explanations": CLIENT_FACTOR_EXPLANATIONS}

        yield event("progress", {"message": "Strategiczne streszczenie wykonawcze (Gemini)...", "pct": 97})
        try:
            overview = generate_strategic_overview(page_audits, synth, scores_obj, fan_out, homepage_title, client_mode)
        except Exception as e:
            overview = {"headline": "", "summary": f"Nie udało się wygenerować streszczenia: {e}", "priorities": []}

        result = {
            "url": url,
            "discovery_source": discovery_source,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "homepage_title": homepage_title,
            "homepage_meta_desc": homepage_data["meta_desc"],
            "scores": scores_obj,
            "dashboard": dashboard,
            "factor_index": factor_index,
            "page_audits": page_audits,
            "domain_technical": {
                "scores": domain_tech_scores,
                "score_pct": domain_tech_pct,
                "robots": {k: v for k, v in robots.items() if k != "raw"},
                "sitemap": sitemap,
                "llms_txt": llms,
                "http_headers": http_headers,
                "pagespeed": pagespeed,
            },
            "fan_out": fan_out,
            "synthesis": synth,
            "ai_snippet_preview": ai_snippet,
            "brand_perception": brand_perception,
            "brand_gaps": brand_gaps,
            "client_mode": client_mode,
            "overview": overview,
            "senuto_aio": load_senuto_aio(url),
            "meta": {
                "factor_meta": FACTOR_META,
                "tech_factor_meta": TECH_FACTOR_META,
                "domain_tech_meta": DOMAIN_TECH_META,
                "category_labels": CATEGORY_LABELS,
                "group_labels": UI_GROUP_LABELS,
                "group_order": UI_GROUP_ORDER,
                "group_weights": UI_GROUP_WEIGHTS,
                "score_value_map": SCORE_VALUE_MAP,
                "page_type_labels": PAGE_TYPE_LABELS,
                "patent_factor_count": len(PATENT_FACTORS),
                "patent_scored_factor_count": len(scored_patent_factor_ids()),
            },
        }
        # Zapis raportu (do udostępniania linkiem i przywoływania domeny ponownie).
        try:
            save_report(result)
        except Exception as e:  # noqa: BLE001
            logging.info("save_report skipped: %s", e)
        yield event("done", {"result": result, "pct": 100})

    except Exception as e:
        yield event("error", {"message": str(e)})


# --- ROUTES ---

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/audit/candidates")
async def audit_candidates(url: str):
    url = normalize_input_url(url)
    if not url:
        raise HTTPException(status_code=400, detail="Invalid URL or domain")
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_urls = fetch_sitemap_urls(base_url)
    source = "sitemap" if sitemap_urls else "firecrawl-map"
    if not sitemap_urls:
        sitemap_urls = fetch_firecrawl_map(base_url)
    nav_links = fetch_homepage_nav_links(url, base_url)
    candidates = propose_page_candidates(sitemap_urls, url, base_url, nav_links=nav_links)
    return {
        "homepage": url,
        "base_url": base_url,
        "discovery_source": source,
        "sitemap_count": len(sitemap_urls),
        "nav_link_count": len(nav_links),
        "candidates": candidates,
    }


@app.get("/audit/stream")
async def audit_endpoint(url: str, picks: str = ""):
    url = normalize_input_url(url)
    if not url:
        raise HTTPException(status_code=400, detail="Invalid URL or domain")
    parsed_picks: list[dict] | None = None
    if picks:
        try:
            parsed_picks = json.loads(picks)
            if not isinstance(parsed_picks, list):
                parsed_picks = None
        except Exception:
            parsed_picks = None
    return StreamingResponse(
        audit_stream(url, parsed_picks),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Async job API (do pobierania pełnego audytu przez narzędzia z limitem czasu,
#     np. web_fetch w Cowork). Klient: GET /audit/start -> {job_id},
#     potem polling GET /audit/result?job_id=... aż status == "done"/"error". ---
_AUDIT_JOBS: dict[str, dict] = {}
_AUDIT_JOBS_LOCK = threading.Lock()
_AUDIT_JOBS_MAX = 100
_AUDIT_JOB_TTL = 3600  # sekundy — po tym czasie zakończony job może zostać usunięty


def _prune_audit_jobs():
    """Usuwa stare/zakończone joby, żeby słownik nie rósł w nieskończoność."""
    now = time.time()
    with _AUDIT_JOBS_LOCK:
        stale = [
            jid for jid, j in _AUDIT_JOBS.items()
            if j["status"] in ("done", "error") and now - j.get("finished_at", now) > _AUDIT_JOB_TTL
        ]
        for jid in stale:
            _AUDIT_JOBS.pop(jid, None)
        if len(_AUDIT_JOBS) > _AUDIT_JOBS_MAX:
            oldest = sorted(_AUDIT_JOBS.items(), key=lambda kv: kv[1].get("created_at", 0))
            for jid, _ in oldest[: len(_AUDIT_JOBS) - _AUDIT_JOBS_MAX]:
                _AUDIT_JOBS.pop(jid, None)


def _consume_audit_job(job_id: str, url: str, picks: list[dict] | None):
    """Konsumuje generator audit_stream w wątku i aktualizuje stan joba."""
    job = _AUDIT_JOBS[job_id]
    try:
        for chunk in audit_stream(url, picks):
            line = chunk.strip()
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[len("data:"):].strip())
            except Exception:
                continue
            step = payload.get("step")
            if step == "progress":
                job["pct"] = payload.get("pct")
                job["message"] = payload.get("message", "")
            elif step == "done":
                job["result"] = payload.get("result")
                job["pct"] = 100
            elif step == "error":
                job["error"] = payload.get("message", "Błąd audytu")
        if not job.get("error") and job.get("result") is None:
            job["error"] = "Audyt zakończony bez wyniku."
        job["status"] = "error" if job.get("error") else "done"
    except Exception as e:  # noqa: BLE001
        job["error"] = str(e)
        job["status"] = "error"
    finally:
        job["finished_at"] = time.time()


@app.get("/audit/start")
async def audit_start(url: str, picks: str = ""):
    """Startuje audyt w tle. Zwraca {job_id} natychmiast (bez czekania)."""
    url = normalize_input_url(url)
    if not url:
        raise HTTPException(status_code=400, detail="Invalid URL or domain")
    parsed_picks: list[dict] | None = None
    if picks:
        try:
            parsed_picks = json.loads(picks)
            if not isinstance(parsed_picks, list):
                parsed_picks = None
        except Exception:
            parsed_picks = None
    _prune_audit_jobs()
    job_id = uuid.uuid4().hex
    _AUDIT_JOBS[job_id] = {
        "status": "running",
        "pct": 0,
        "message": "Start audytu...",
        "result": None,
        "error": None,
        "url": url,
        "created_at": time.time(),
    }
    threading.Thread(
        target=_consume_audit_job, args=(job_id, url, parsed_picks), daemon=True
    ).start()
    return {"job_id": job_id, "status": "running", "url": url}


@app.get("/audit/result")
async def audit_result(job_id: str, fields: str = ""):
    """Status/wynik joba. Gdy status == 'done', zawiera pełny 'result'.

    Opcjonalny `fields` (klucze top-level po przecinku, np. 'scores,synthesis')
    ogranicza zwracany 'result' do wybranych sekcji — przydatne, gdy klient
    ma limit rozmiaru odpowiedzi.
    """
    job = _AUDIT_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job_id")
    resp = {
        "job_id": job_id,
        "status": job["status"],
        "pct": job.get("pct"),
        "message": job.get("message"),
        "url": job.get("url"),
    }
    if job["status"] == "done":
        result = job["result"]
        if fields and isinstance(result, dict):
            keys = [k.strip() for k in fields.split(",") if k.strip()]
            result = {k: result.get(k) for k in keys}
        resp["result"] = result
    elif job["status"] == "error":
        resp["error"] = job["error"]
    return resp


@app.get("/report")
async def get_report(domain: str = "", url: str = ""):
    """Zwraca wcześniej zapisany raport dla danej domeny (do udostępniania linkiem
    i przywoływania ponownie). Zwraca {found: bool, result?: dict}."""
    key_src = domain or url
    if not key_src:
        raise HTTPException(status_code=400, detail="Podaj parametr 'domain' lub 'url'")
    result = load_report(key_src)
    if result is None:
        return {"found": False, "domain": _report_key(key_src)}
    return {"found": True, "domain": _report_key(key_src), "result": result}


@app.get("/health")
async def health():
    return {"status": "ok"}


class LeadRequest(BaseModel):
    email: str
    url: str | None = None
    score: int | None = None


@app.post("/lead")
async def capture_lead(lead: LeadRequest):
    email = (lead.email or "").strip()
    if "@" not in email or "." not in email or len(email) > 200:
        raise HTTPException(status_code=400, detail="Invalid email")
    record = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "email": email,
        "url": (lead.url or "").strip()[:500],
        "score": lead.score,
    }
    # 1. stdout → Cloud Logging (persists across restarts)
    print(f"LEAD: {json.dumps(record, ensure_ascii=False)}", flush=True)
    # 2. In-memory (survives within the same instance, readable via GET /leads)
    with _LEADS_LOCK:
        _LEADS_MEMORY.append(record)
    # 3. File (only if LEADS_FILE env var explicitly set to a persistent path)
    path = os.getenv("LEADS_FILE", "")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass
    # 4. Firestore (best-effort, if FIRESTORE_PROJECT is set)
    threading.Thread(target=_save_lead_to_firestore, args=(record,), daemon=True).start()
    # 5. E-mail notification (best-effort, if LEADS_EMAIL + SMTP_USER + SMTP_PASS set)
    threading.Thread(target=_send_lead_email, args=(record,), daemon=True).start()
    return {"status": "ok"}


@app.get("/leads")
async def list_leads(token: str = ""):
    """Return in-memory leads for this instance. Protect with LEADS_TOKEN env var."""
    tok = LEADS_TOKEN
    if not tok or token != tok:
        raise HTTPException(status_code=403, detail="Forbidden — set LEADS_TOKEN and pass ?token=...")
    with _LEADS_LOCK:
        return {"leads": list(_LEADS_MEMORY), "count": len(_LEADS_MEMORY)}


# --- Serwowanie pod prefiksem /llms-audit (za Firebase Hosting) ---
# Cała dotychczasowa appka (trasy, /static) zostaje zamontowana pod /llms-audit,
# żeby działała pod adresem strategiczni.ai/llms-audit. Uruchamiamy "main:root".
root = FastAPI()
root.mount("/llms-audit", app)


@root.get("/")
def _root_redirect():
    return RedirectResponse("/llms-audit/")
