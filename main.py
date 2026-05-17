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
PAGESPEED_KEY = os.getenv("PAGESPEED_KEY", "")
PERPLEXITY_KEY = os.getenv("PERPLEXITY_KEY", "")

AI_BOTS = ["GPTBot", "PerplexityBot", "OAI-SearchBot", "ClaudeBot", "anthropic-ai", "Google-Extended"]
MAX_AUDIT_PAGES = 5
SITEMAP_CAP = 300
MAX_CONTENT_CHARS = 7000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATENT_FACTORS_PATH = os.path.join(BASE_DIR, "google-patent-seo-skill", "references", "factors.jsonl")
PATENT_SCORING_CONFIDENCE = {"high", "medium"}


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
    "pagespeed_mobile_ok": "Strona szybko ładuje się na telefonach – kluczowe, bo większość użytkowników przegląda na mobile.",
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
    "pagespeed_mobile_ok": {"label": "PageSpeed Mobile (Core Web Vitals)", "category": "tech"},
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
    "pagespeed_mobile_ok": 7,
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

UI_GROUP_ORDER = ["technical", "onpage", "schema", "eeat", "patents", "ai_aeo"]
UI_GROUP_LABELS = {
    "technical": "Techniczne SEO",
    "onpage": "On-page",
    "schema": "Schema",
    "eeat": "E-E-A-T",
    "patents": "Patenty Google",
    "ai_aeo": "AI / AEO",
}
UI_GROUP_WEIGHTS = {
    "technical": 20,
    "onpage": 10,
    "schema": 20,
    "eeat": 25,
    "patents": 10,
    "ai_aeo": 15,
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


def _ui_group_for_factor(factor_id: str, meta: dict | None = None, *, is_tech: bool = False, is_domain: bool = False) -> str:
    meta = meta or {}
    if meta.get("source") == "google_patent":
        return "patents"
    if factor_id in SCHEMA_FACTOR_IDS or "schema" in factor_id:
        return "schema"
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
    elif group == "schema":
        impact = 3
        effort = 1
    elif group == "eeat":
        impact = 3
        effort = 2
    elif group == "ai_aeo":
        impact = 3 if factor_id in {"direct_answer_near_content_start", "citable-fragment-density"} else 2
        effort = 2
    elif group == "technical":
        impact = 3 if factor_id in CRITICAL_FACTOR_PENALTIES or is_domain else 2
        effort = 1
    else:
        impact = 2
        effort = 2

    high_effort_tokens = ("depth", "original", "external", "citations", "case", "comprehensive", "differentiation")
    low_effort_tokens = ("date", "cta", "title", "h1", "mailto", "tel", "viewport", "lang", "canonical")
    if any(token in factor_id for token in high_effort_tokens):
        effort = max(effort, 3)
    if any(token in factor_id for token in low_effort_tokens):
        effort = min(effort, 1)
    if factor_id == "pagespeed_mobile_ok":
        effort = 3

    return _clamp_score(impact), _clamp_score(effort)


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

    if group == "schema":
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
        meta.setdefault("detail", _generic_detail(factor_id, meta.get("label", factor_id), group, meta, is_tech=True))

    for factor_id, meta in DOMAIN_TECH_META.items():
        group = _ui_group_for_factor(factor_id, meta, is_domain=True)
        impact, effort = _impact_effort_for_factor(factor_id, group, meta, is_domain=True)
        meta.setdefault("group", group)
        meta.setdefault("group_label", UI_GROUP_LABELS[group])
        meta.setdefault("applies_to", ["domain"])
        meta.setdefault("impact", impact)
        meta.setdefault("effort", effort)
        meta.setdefault("detail", _generic_detail(factor_id, meta.get("label", factor_id), group, meta, is_domain=True))


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


def _ensure_factor_record(index: dict[str, dict], key: str, meta: dict, *, is_tech: bool = False, is_domain: bool = False) -> dict:
    uid = f"domain:{key}" if is_domain else f"tech:{key}" if is_tech else f"factor:{key}"
    if uid not in index:
        group = meta.get("group") or _ui_group_for_factor(key, meta, is_tech=is_tech, is_domain=is_domain)
        index[uid] = {
            "uid": uid,
            "id": key,
            "label": meta.get("label", key),
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


def build_factor_index(page_audits: list[dict], domain_tech_scores: dict) -> list[dict]:
    index: dict[str, dict] = {}

    for pa in page_audits:
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
                "note": _tech_auto_note(score),
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
            "note": _tech_auto_note(score, domain=True),
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


def propose_page_candidates(all_urls: list[str], homepage_url: str, base_url: str, per_type: int = 4) -> dict:
    """Gemini groups sitemap URLs into buckets (service/article/about/other). User confirms picks."""
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

    empty = {"service": [], "article": [], "about": [], "other": []}
    if not clean:
        return empty
    candidates = clean[:80]

    prompt = f"""Jesteś ekspertem SEO. Klasyfikujesz podstrony witryny i sugerujesz NAJLEPSZYCH kandydatów do audytu.

<cel>
Pogrupuj kandydatów w 4 kubełki: service (sprzedażowa/oferta/produkt/cennik/landing), article (blog/poradnik/case study/news),
about (o nas/zespół/historia), other (portfolio, FAQ, referencje). Dla każdego kubełka WYBIERZ do {per_type} najbardziej reprezentatywnych URL-i.
Strona główna {homepage_url} jest JUŻ wybrana — nie wliczaj jej.
</cel>

<wykluczenia>
polityka prywatności, regulamin, RODO, cookies; paginacja; tagi/archiwa/wyniki wyszukiwania; logowanie/koszyk/konto;
URL-e z parametrami śledzenia; strony błędów/staging.
</wykluczenia>

<wskazówki>
- Slug głębszy = bardziej konkretna treść (preferuj "/uslugi/seo-techniczny" nad "/uslugi").
- Dla 'article' wybierz najmocniejsze tematycznie wpisy (nie listingi).
- W każdym kubełku unikaj duplikatów tematycznych.
- Jeśli kubełek pusty - zwróć [].
</wskazówki>

<kandydaci>
{chr(10).join(f"- {u}" for u in candidates)}
</kandydaci>

Zwróć TYLKO JSON:
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


def check_pagespeed(url: str) -> dict:
    if not PAGESPEED_KEY:
        return {"available": False}
    try:
        r = requests.get(
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
            params={"url": url, "strategy": "mobile", "key": PAGESPEED_KEY},
            timeout=60,
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


def build_domain_tech_scores(robots: dict, sitemap: dict, llms: dict, homepage_html_checks: dict, http_headers: dict = None, pagespeed: dict = None) -> dict:
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
    if pagespeed and pagespeed.get("available"):
        ps = pagespeed.get("performance_score", 0)
        s["pagespeed_mobile_ok"] = 2 if ps >= 70 else (1 if ps >= 50 else 0)
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


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1:
        text = text[first : last + 1]
    return json.loads(text)


def _page_factor_prompt(page_type: str, url: str, title: str, meta_desc: str, content: str, html_checks: dict | None = None) -> str:
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

<treść>
{content}
</treść>

WSZYSTKIE "note" po polsku. Konkret > ogólnik.

Zwróć TYLKO poprawny JSON (bez markdown):
{{
{factor_spec}
}}"""


def analyze_page(url: str, page_type: str, title: str, meta_desc: str, content: str, html_checks: dict | None = None) -> dict:
    prompt = _page_factor_prompt(page_type, url, title, meta_desc, content, html_checks)
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

        # 4. Domain-level checks — run PageSpeed in parallel with other checks
        with ThreadPoolExecutor(max_workers=4) as _tech_ex:
            _robots_f = _tech_ex.submit(check_robots_txt, base_url)
            _sitemap_f = _tech_ex.submit(check_sitemap, base_url)
            _llms_f = _tech_ex.submit(check_llms_txt, base_url)
            _headers_f = _tech_ex.submit(check_http_headers, base_url)
            _pagespeed_f = _tech_ex.submit(check_pagespeed, url)
            robots = _robots_f.result()
            sitemap = _sitemap_f.result()
            llms = _llms_f.result()
            http_headers = _headers_f.result()
            pagespeed = _pagespeed_f.result()

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
        domain_tech_scores = build_domain_tech_scores(robots, sitemap, llms, homepage_data["html_checks"], http_headers, pagespeed)

        yield event("progress", {"message": "Per-URL analiza: typ strony + czynniki z patentów Google (Gemini, parallel)...", "pct": 48})

        # 5. Parallel per-page Gemini analysis
        def _analyze_one(pd):
            content = pd["markdown"][:MAX_CONTENT_CHARS]
            try:
                factors = analyze_page(pd["url"], pd["page_type"], pd["title"], pd["meta_desc"], content, pd["html_checks"])
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
        factor_index = build_factor_index(page_audits, domain_tech_scores)
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

        yield event("progress", {"message": "Tryb Klient: tłumaczenie wyników na prosty język (osobne zapytanie)...", "pct": 95})
        try:
            client_mode = translate_for_client_mode(page_audits, synth, scores_obj, fan_out, homepage_title)
            client_mode["client_factor_explanations"] = CLIENT_FACTOR_EXPLANATIONS
        except Exception as e:
            client_mode = {"client_verdict": f"Tłumaczenie nieudane: {e}", "client_recommendations": [], "client_content_gaps": [], "client_next_step": "", "client_factor_explanations": CLIENT_FACTOR_EXPLANATIONS}

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
            "client_mode": client_mode,
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
    if not url:
        raise HTTPException(status_code=400, detail="URL required")
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_urls = fetch_sitemap_urls(base_url)
    source = "sitemap" if sitemap_urls else "firecrawl-map"
    if not sitemap_urls:
        sitemap_urls = fetch_firecrawl_map(base_url)
    candidates = propose_page_candidates(sitemap_urls, url, base_url)
    return {
        "homepage": url,
        "base_url": base_url,
        "discovery_source": source,
        "sitemap_count": len(sitemap_urls),
        "candidates": candidates,
    }


@app.get("/audit/stream")
async def audit_endpoint(url: str, picks: str = ""):
    if not url:
        raise HTTPException(status_code=400, detail="URL required")
    if not url.startswith("http"):
        url = "https://" + url
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


@app.get("/health")
async def health():
    return {"status": "ok"}
