#!/usr/bin/env python3
"""Build the Google patent SEO knowledge base from local PDFs.

The script intentionally separates three layers:
1. patent/document inventory,
2. evidence extracted from text/OCR/figures,
3. SEO factors inferred from the evidence.

High confidence should come from textual patent evidence. Figure evidence is
stored as context and should be paired with text before being used strongly.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader
except ImportError as exc:  # pragma: no cover - gives actionable CLI failure.
    raise SystemExit(
        "pypdf is required. Run with the bundled Codex Python runtime or install pypdf."
    ) from exc


ROOT = Path(__file__).resolve().parents[2]
KB_DIR = ROOT / "seo-patent-kb"
DATA_DIR = KB_DIR / "data"
DOCS_DIR = KB_DIR / "docs"
FIGURES_DIR = KB_DIR / "figures"
TEXT_DIR = KB_DIR / "extracted_text"
SKILL_DIR = ROOT / "google-patent-seo-skill"
SKILL_REF_DIR = SKILL_DIR / "references"
SKILL_SCRIPT_DIR = SKILL_DIR / "scripts"

GS = shutil.which("gs") or shutil.which("/opt/homebrew/bin/gs")
TESSERACT = shutil.which("tesseract") or shutil.which("/opt/homebrew/bin/tesseract")


EXPECTED_PATENTS: list[dict[str, str]] = [
    {"patent_id": "US10102187B2", "title": "Extensible framework for ereader tools, including named entity information"},
    {"patent_id": "US10585927B1", "title": "Determining a set of steps responsive to a how-to query"},
    {"patent_id": "US10832001B2", "title": "Machine learning to identify opinions in documents"},
    {"patent_id": "US12073187B2", "title": "Automatic evaluation of natural language text generated based on structured data"},
    {"patent_id": "US12073189B2", "title": "Learned evaluation model for grading quality of natural language generation outputs"},
    {"patent_id": "US12197525B2", "title": "Techniques for presenting graphical content in a search result"},
    {"patent_id": "US12223273B2", "title": "Learned evaluation model for grading quality of natural language generation outputs"},
    {"patent_id": "US20120016870A1", "title": "Unknown locally - referenced by source article"},
    {"patent_id": "US20130055089A1", "title": "Share box for endorsements"},
    {"patent_id": "US20170093934A1", "title": "Entity page recommendation based on post content"},
    {"patent_id": "US20190034530A1", "title": "Content selection and presentation of electronic content"},
    {"patent_id": "US20220067309A1", "title": "Learned evaluation model for grading quality of natural language generation outputs"},
    {"patent_id": "US20240012999A1", "title": "Learned evaluation model for grading quality of natural language generation outputs"},
    {"patent_id": "US20240135187A1", "title": "Method for training large language models to perform query intent classification"},
    {"patent_id": "US20240428015A1", "title": "Learning self-evaluation to improve selective prediction in LLMs"},
    {"patent_id": "US20250103640A1", "title": "Providing generative answers including citations to source documents"},
    {"patent_id": "US20250103826A1", "title": "Processing documents in cloud storage using query embeddings"},
    {"patent_id": "US20250217626A1", "title": "Generating content via a machine-learned model based on source content selected by a user"},
    {"patent_id": "US20250356223A1", "title": "Unknown locally - referenced by source article"},
    {"patent_id": "US8244689B2", "title": "Attribute entropy as a signal in object normalization"},
    {"patent_id": "US8788477B1", "title": "Identifying addresses and titles of authoritative web pages by analyzing search queries in query logs"},
    {"patent_id": "US8954412B1", "title": "Corroborating facts in electronic documents"},
    {"patent_id": "US9195944B1", "title": "Scoring site quality"},
    {"patent_id": "US9317592B1", "title": "Unknown locally - referenced by source article"},
    {"patent_id": "US9619450B2", "title": "Automatic generation of headlines"},
    {"patent_id": "US9679018B1", "title": "Document ranking based on entity frequency"},
    {"patent_id": "US6961720B1", "title": "System and method for automatic task prioritization"},
]


STEM_TO_PATENT_ID = {
    "US10102187": "US10102187B2",
    "US10585927": "US10585927B1",
    "US10832001": "US10832001B2",
    "US12073187": "US12073187B2",
    "US12073189": "US12073189B2",
    "US12197525": "US12197525B2",
    "US12223273": "US12223273B2",
    "US20130055089A1": "US20130055089A1",
    "US20170093934A1": "US20170093934A1",
    "US20190034530A1": "US20190034530A1",
    "US20220067309A1": "US20220067309A1",
    "US20240012999A1": "US20240012999A1",
    "US20240135187A1": "US20240135187A1",
    "US20240428015A1": "US20240428015A1",
    "US20250103640A1": "US20250103640A1",
    "US20250103826A1": "US20250103826A1",
    "US20250217626A1": "US20250217626A1",
    "US6961720": "US6961720B1",
    "US8244689": "US8244689B2",
    "US8954412": "US8954412B1",
    "US9619450": "US9619450B2",
    "US9679018": "US9679018B1",
}


ARTICLE_ID = "LINKEDIN-2026-BOROWIEC-202-PATENTS"
ARTICLE_PREFIX = "(1) Jak"


FACTOR_SEEDS: list[dict[str, Any]] = [
    {
        "factor_id": "information-gain-score",
        "name_pl": "Information Gain Score",
        "category": "content_quality",
        "definition_pl": "Miara tego, czy dokument wnosi nowe, relewantne atrybuty lub fakty ponad to, co jest już obecne w konkurencyjnych dokumentach dla tego samego zapytania.",
        "mechanism_pl": "LLM lub system audytu porównuje pokrycie encji, atrybutów i twierdzeń z topowymi dokumentami, a następnie premiuje treści dodające brakujące, użyteczne informacje.",
        "how_to_satisfy_pl": "Przed pisaniem zbuduj macierz top 10: encje, atrybuty, pytania, dane i przykłady. Dodaj 3-5 unikalnych punktów, które są prawdziwe, istotne i łatwe do zacytowania.",
        "example_pl": "W artykule o indeksowaniu JS dodaj porównanie renderowania CSR, SSR i hydration errors z logami z GSC, jeśli konkurencja omawia tylko ogólne crawlowanie.",
        "audit_checks_pl": ["Czy tekst zawiera fakty lub atrybuty nieobecne w top 10?", "Czy nowe informacje są powiązane z intencją query?", "Czy są oznaczone nagłówkiem lub topic sentence?"],
        "measurement_inputs": ["content", "serp_top10", "entity_map"],
        "source_patents": [{"patent_id": "US9317592B1", "support_type": "missing_source"}],
        "evidence_ids": ["US9317592B1-text-abstract"],
        "evidence_summary_pl": "Źródło wymaga uzupełnienia; rekord zachowuje hipotezę z artykułu mapującego patenty.",
        "seo_inference_level": "moderate",
        "confidence": "low",
        "anti_patterns_pl": ["Parafrazowanie top 10 bez dodania nowych atrybutów.", "Dodawanie ciekawostek bez związku z intencją użytkownika."],
        "tags": ["content", "information-gain", "serp-differentiation"],
    },
    {
        "factor_id": "cross-document-factual-consistency",
        "name_pl": "Cross-Document Factual Consistency",
        "category": "content_quality",
        "definition_pl": "Stopień, w jakim kluczowe twierdzenia dokumentu są zgodne z niezależnymi, wiarygodnymi źródłami.",
        "mechanism_pl": "System porównuje twierdzenia z innymi dokumentami i może wykrywać potwierdzenia, sprzeczności oraz liczbę niezależnych źródeł wspierających fakt.",
        "how_to_satisfy_pl": "Dla danych liczbowych, definicji i mocnych claimów dodawaj kilka niezależnych źródeł pierwotnych oraz unikaj niespójnych wartości między sekcjami.",
        "example_pl": "Claim o udziale kanału organicznego podeprzyj danymi GSC, raportem branżowym i dokumentacją platformy, zamiast jednym blogpostem agregującym.",
        "audit_checks_pl": ["Czy najważniejsze claimy mają źródła pierwotne?", "Czy liczby są spójne w treści, tabelach i schema?", "Czy źródła są niezależne od siebie?"],
        "measurement_inputs": ["content", "external_sources", "citations"],
        "source_patents": [{"patent_id": "US8954412B1", "support_type": "direct"}, {"patent_id": "US9619450B2", "support_type": "supporting"}],
        "evidence_ids": ["US8954412B1-text-abstract", "US8954412B1-text-claims", "US9619450B2-text-abstract"],
        "evidence_summary_pl": "Patent o corroborating facts opisuje potwierdzanie faktów w dokumentach elektronicznych; patent headline generation wspiera analizę istotnych treści źródłowych.",
        "seo_inference_level": "direct",
        "confidence": "high",
        "anti_patterns_pl": ["Jeden agregator jako jedyne źródło.", "Rozbieżne liczby w tytule, treści i tabeli."],
        "tags": ["facts", "citations", "trust"],
    },
    {
        "factor_id": "content-data-alignment-score",
        "name_pl": "Content-Data Alignment Score",
        "category": "structured_data_alignment",
        "definition_pl": "Zgodność tekstu wygenerowanego lub opublikowanego na stronie z danymi strukturalnymi, tabelami, feedami i faktami wejściowymi.",
        "mechanism_pl": "Model ocenia, czy tekst zachowuje wierność wobec danych bazowych; rozjazdy obniżają wiarygodność generowanego opisu.",
        "how_to_satisfy_pl": "Synchronizuj liczby, ceny, daty, oceny i parametry między body, tabelami oraz schema.org. Każdy fakt możliwy do ustrukturyzowania powinien mieć tę samą wartość w danych.",
        "example_pl": "Jeśli opis produktu mówi o cenie 1200 zł, schema Product i feed merchant nie mogą wskazywać 1450 zł.",
        "audit_checks_pl": ["Czy wartości liczbowe w tekście zgadzają się ze schema?", "Czy recenzje, kursy i produkty mają właściwe typy schema.org?", "Czy dane wejściowe są aktualniejsze niż opis?"],
        "measurement_inputs": ["content", "schema", "structured_data", "product_feed"],
        "source_patents": [{"patent_id": "US12073187B2", "support_type": "direct"}],
        "evidence_ids": ["US12073187B2-text-abstract", "US12073187B2-text-claims", "US12073187B2-fig-p003"],
        "evidence_summary_pl": "Patent opisuje automatyczną ocenę tekstu naturalnego wygenerowanego na podstawie danych strukturalnych.",
        "seo_inference_level": "direct",
        "confidence": "high",
        "anti_patterns_pl": ["Tekst marketingowy z wartościami innymi niż schema.", "Dane strukturalne dodane dekoracyjnie, bez pokrycia w treści."],
        "tags": ["schema", "structured-data", "factuality"],
    },
    {
        "factor_id": "semantic-coherence-score",
        "name_pl": "Semantic Coherence Score",
        "category": "content_quality",
        "definition_pl": "Spójność znaczeniowa między zapytaniem, intencją, nagłówkami, akapitami, terminologią i odpowiedzią końcową.",
        "mechanism_pl": "Modele językowe i klasyfikatory intencji mogą oceniać, czy kolejne fragmenty tekstu zachowują jeden temat, konsekwentną terminologię i logiczny ciąg argumentacji.",
        "how_to_satisfy_pl": "Używaj jednego słownika pojęć, porządkuj nagłówki według intencji użytkownika i usuwaj dygresje, które nie wspierają odpowiedzi.",
        "example_pl": "Poradnik o migracji SEO powinien trzymać się kroków migracji, a nie mieszać ich z ogólną sprzedażą audytów SEO.",
        "audit_checks_pl": ["Czy każdy H2 odpowiada na część głównej intencji?", "Czy terminologia jest konsekwentna?", "Czy akapity nie przeskakują między tematami bez łącznika?"],
        "measurement_inputs": ["content", "query", "intent_labels"],
        "source_patents": [{"patent_id": "US20240012999A1", "support_type": "direct"}, {"patent_id": "US20220067309A1", "support_type": "direct"}, {"patent_id": "US20240135187A1", "support_type": "supporting"}],
        "evidence_ids": ["US20240012999A1-text-abstract", "US20220067309A1-text-abstract", "US20240135187A1-text-abstract"],
        "evidence_summary_pl": "Patenty dotyczą oceny jakości generacji języka naturalnego i klasyfikacji intencji zapytań.",
        "seo_inference_level": "moderate",
        "confidence": "medium",
        "anti_patterns_pl": ["Sekcje dopisane pod słowa kluczowe bez związku z intencją.", "Synonimy używane chaotycznie dla tego samego pojęcia."],
        "tags": ["semantic-seo", "intent", "nlp-quality"],
    },
    {
        "factor_id": "entity-coverage-depth",
        "name_pl": "Entity Coverage Depth",
        "category": "entity_graph",
        "definition_pl": "Głębokość pokrycia encji istotnych dla tematu, czyli liczba i jakość unikalnych encji dziedzinowych obsłużonych w dokumencie.",
        "mechanism_pl": "System może wykorzystywać rozpoznane encje i ich częstotliwość w korpusie do rankingu lub scoringu dokumentów względem zainteresowań i tematu.",
        "how_to_satisfy_pl": "Zbuduj mapę encji dla klastra tematycznego i upewnij się, że tekst pokrywa encje główne, podrzędne, narzędzia, metody, metryki i znane byty.",
        "example_pl": "Tekst o topical authority powinien obejmować m.in. Knowledge Graph, entity salience, PageRank, anchor context, topical clusters i internal linking.",
        "audit_checks_pl": ["Czy tekst zawiera encje specjalistyczne, nie tylko generyczne?", "Czy encje są wyjaśnione i powiązane?", "Czy brakuje kluczowych encji z topowych dokumentów?"],
        "measurement_inputs": ["content", "entity_map", "serp_top10"],
        "source_patents": [{"patent_id": "US9679018B1", "support_type": "direct"}],
        "evidence_ids": ["US9679018B1-text-abstract", "US9679018B1-text-summary", "US9679018B1-fig-p003"],
        "evidence_summary_pl": "Patent opisuje ranking dokumentów na podstawie częstotliwości encji i tematów zainteresowania użytkownika.",
        "seo_inference_level": "direct",
        "confidence": "high",
        "anti_patterns_pl": ["Tekst oparty wyłącznie o ogólne terminy.", "Lista encji bez pokazania relacji między nimi."],
        "tags": ["entities", "topical-authority", "coverage"],
    },
    {
        "factor_id": "entity-group-rarity",
        "name_pl": "Entity Group Rarity",
        "category": "entity_graph",
        "definition_pl": "Waga rzadkich lub specjalistycznych grup encji, które odróżniają dokument od ogólnego omówienia tematu.",
        "mechanism_pl": "Rzadkość encji lub grupy encji w korpusie działa podobnie do sygnału IDF: mniej powszechne, ale tematycznie relewantne grupy mogą lepiej sygnalizować specjalizację.",
        "how_to_satisfy_pl": "Dodawaj specjalistyczne encje i ich relacje tylko wtedy, gdy realnie wspierają intencję; nie upychaj nazw bez kontekstu.",
        "example_pl": "W treści o patentach SEO użycie Reasonable Surfer, Hilltop, NavBoost i entity frequency jest silniejsze niż samo powtarzanie 'ranking Google'.",
        "audit_checks_pl": ["Czy są encje rzadkie dla ogólnego webu, ale typowe dla niszy?", "Czy są opisane w kontekście?", "Czy nie są to losowe buzzwordy?"],
        "measurement_inputs": ["content", "entity_map", "corpus_frequency"],
        "source_patents": [{"patent_id": "US9679018B1", "support_type": "direct"}, {"patent_id": "US8244689B2", "support_type": "supporting"}],
        "evidence_ids": ["US9679018B1-text-summary", "US8244689B2-text-abstract"],
        "evidence_summary_pl": "Patent entity frequency opisuje wartości odpowiadające częstości encji/grup w korpusie; attribute entropy wspiera rozróżnianie obiektów przez informacyjne atrybuty.",
        "seo_inference_level": "direct",
        "confidence": "high",
        "anti_patterns_pl": ["Sztuczne dokładanie terminów bez wyjaśnienia.", "Zastępowanie odpowiedzi słownikiem pojęć."],
        "tags": ["entities", "idf", "specialization"],
    },
    {
        "factor_id": "entity-salience",
        "name_pl": "Entity Salience",
        "category": "entity_graph",
        "definition_pl": "Centralność encji w dokumencie: czy encja jest głównym tematem, czy tylko poboczną wzmianką.",
        "mechanism_pl": "Systemy selekcji i prezentacji treści mogą wykorzystywać relację między treścią a encjami, analizując pozycję, częstość, współwystępowanie i kontekst encji.",
        "how_to_satisfy_pl": "Umieść główną encję w title, H1, wstępie i sekcjach decyzyjnych; pokazuj jej relacje z encjami sąsiednimi.",
        "example_pl": "Artykuł o 'schema Product' powinien omawiać Product, Offer, AggregateRating i Merchant Center jako rdzeń, nie jako poboczną listę.",
        "audit_checks_pl": ["Czy główna encja pojawia się we wstępie?", "Czy jest podmiotem zdań?", "Czy występuje w sekcjach decyzyjnych, a nie tylko w glossary?"],
        "measurement_inputs": ["content", "title", "headings", "entity_map"],
        "source_patents": [{"patent_id": "US20190034530A1", "support_type": "direct"}, {"patent_id": "US10102187B2", "support_type": "supporting"}],
        "evidence_ids": ["US20190034530A1-text-abstract", "US10102187B2-text-abstract"],
        "evidence_summary_pl": "Patenty dotyczą selekcji/prezentacji treści i narzędzi encji nazwanych, co wspiera operacyjne rozumienie centralności encji.",
        "seo_inference_level": "moderate",
        "confidence": "medium",
        "anti_patterns_pl": ["Encja pojawia się tylko raz w przykładzie.", "Title mówi o jednej encji, a treść koncentruje się na innej."],
        "tags": ["entity-salience", "named-entities", "content-focus"],
    },
    {
        "factor_id": "content-entity-alignment",
        "name_pl": "Content-Entity Alignment",
        "category": "entity_graph",
        "definition_pl": "Dopasowanie treści, nagłówków i przykładów do encji, dla których dokument ma budować rozpoznawalność i autorytet.",
        "mechanism_pl": "Rozpoznanie encji i ich użycia w treści pozwala ocenić, czy dokument faktycznie odpowiada na temat przypisany do encji.",
        "how_to_satisfy_pl": "Zdefiniuj główne encje przed pisaniem i usuń sekcje, które nie wspierają ich relacji, atrybutów lub pytań użytkownika.",
        "example_pl": "Strona autora SEO powinna łączyć osobę z publikacjami, konferencjami, case studies i tematami, w których ma być rozpoznawana.",
        "audit_checks_pl": ["Czy każdy przykład wzmacnia główną encję?", "Czy encje w schema odpowiadają encjom w treści?", "Czy linkowanie wewnętrzne wzmacnia te same byty?"],
        "measurement_inputs": ["content", "schema", "internal_links", "entity_map"],
        "source_patents": [{"patent_id": "US10102187B2", "support_type": "direct"}, {"patent_id": "US20190034530A1", "support_type": "supporting"}],
        "evidence_ids": ["US10102187B2-text-abstract", "US20190034530A1-text-abstract"],
        "evidence_summary_pl": "Źródła opisują narzędzia do informacji o encjach nazwanych i selekcję treści elektronicznej.",
        "seo_inference_level": "moderate",
        "confidence": "medium",
        "anti_patterns_pl": ["Schema opisuje inną encję niż body.", "Linkowanie prowadzi do niepowiązanych klastrów."],
        "tags": ["entities", "schema", "alignment"],
    },
    {
        "factor_id": "verified-entity-status",
        "name_pl": "Verified Entity Status",
        "category": "entity_graph",
        "definition_pl": "Stopień, w jakim osoba, organizacja, produkt lub źródło jest jednoznacznie identyfikowalne i potwierdzone przez zewnętrzne profile lub dane.",
        "mechanism_pl": "Prezentowanie graficznych wyników i powiązań encji wymaga rozpoznania, do jakiej encji odnosi się treść oraz czy istnieją wiarygodne powiązania.",
        "how_to_satisfy_pl": "Dodawaj strony autorów, Organization/Person schema, sameAs, spójny NAP, profile branżowe i historię publikacji powiązaną z tematem.",
        "example_pl": "Ekspert medyczny ma stronę autora z numerem prawa wykonywania zawodu, publikacjami, ORCID i tym samym profilem w schema Person.",
        "audit_checks_pl": ["Czy autor ma dedykowaną stronę?", "Czy sameAs prowadzi do realnych profili?", "Czy nazwa encji jest spójna w źródłach zewnętrznych?"],
        "measurement_inputs": ["schema", "author_profile", "external_profiles", "knowledge_graph"],
        "source_patents": [{"patent_id": "US12197525B2", "support_type": "supporting"}],
        "evidence_ids": ["US12197525B2-text-abstract", "US12197525B2-fig-p003"],
        "evidence_summary_pl": "Patent dotyczący prezentowania treści graficznych w wynikach wyszukiwania wspiera wymóg jednoznacznego powiązania treści z encjami.",
        "seo_inference_level": "moderate",
        "confidence": "medium",
        "anti_patterns_pl": ["Anonimowy autor przy tematach YMYL.", "sameAs do pustych lub niespójnych profili."],
        "tags": ["authors", "sameAs", "entity-verification"],
    },
    {
        "factor_id": "entity-disambiguation-strength",
        "name_pl": "Entity Disambiguation Strength",
        "category": "entity_graph",
        "definition_pl": "Siła sygnałów pozwalających odróżnić właściwą encję od encji o podobnej nazwie.",
        "mechanism_pl": "Atrybuty o wysokiej entropii i powiązane encje pomagają normalizować obiekty oraz zmniejszać ryzyko błędnego przypisania.",
        "how_to_satisfy_pl": "Dodawaj unikalne identyfikatory: lokalizacja, rola, branża, produkt, daty, profile, identyfikatory prawne i powiązane encje.",
        "example_pl": "Dla autora 'Jan Kowalski' podaj firmę, specjalizację, URL profilu, publikacje i schema sameAs, zamiast samego imienia i nazwiska.",
        "audit_checks_pl": ["Czy nazwa encji jest dwuznaczna?", "Czy tekst ma atrybuty odróżniające?", "Czy dane strukturalne zawierają identyfikatory?"],
        "measurement_inputs": ["content", "schema", "knowledge_graph", "external_profiles"],
        "source_patents": [{"patent_id": "US8244689B2", "support_type": "direct"}, {"patent_id": "US12197525B2", "support_type": "supporting"}],
        "evidence_ids": ["US8244689B2-text-abstract", "US12197525B2-text-abstract"],
        "evidence_summary_pl": "Attribute entropy opisuje informacyjność atrybutów w normalizacji obiektów.",
        "seo_inference_level": "direct",
        "confidence": "high",
        "anti_patterns_pl": ["Ogólny opis autora bez wyróżników.", "Kilka encji o tej samej nazwie bez rozróżnienia."],
        "tags": ["disambiguation", "entity-normalization", "attributes"],
    },
    {
        "factor_id": "source-authority-for-entity-topic",
        "name_pl": "Source Authority for Entity/Topic",
        "category": "source_authority",
        "definition_pl": "Autorytet źródła liczony tematycznie dla konkretnej encji lub obszaru, a nie jako ogólna moc domeny.",
        "mechanism_pl": "Źródło może być oceniane przez historię publikacji, powiązania z encją, potwierdzenia i profil linków w danej niszy.",
        "how_to_satisfy_pl": "Buduj zwarte klastry tematyczne, linkowanie wewnętrzne i zewnętrzne cytowania wokół jednej dziedziny, zamiast rozpraszać publikacje po wielu niszach.",
        "example_pl": "Domena z 200 tekstami o technicznym SEO może być silniejsza dla crawl budget niż ogólny portal marketingowy o większym DR.",
        "audit_checks_pl": ["Czy domena ma historię publikacji o tej encji?", "Czy linki i cytowania pochodzą z tej samej niszy?", "Czy klaster jest spójny?"],
        "measurement_inputs": ["site_history", "backlinks", "internal_links", "entity_map"],
        "source_patents": [{"patent_id": "US8244689B2", "support_type": "supporting"}, {"patent_id": "US20170093934A1", "support_type": "direct"}],
        "evidence_ids": ["US8244689B2-text-abstract", "US20170093934A1-text-abstract"],
        "evidence_summary_pl": "Patenty wspierają rozumienie autorytetu przez atrybuty encji oraz rekomendacje stron encji na podstawie treści postów.",
        "seo_inference_level": "moderate",
        "confidence": "medium",
        "anti_patterns_pl": ["Publikowanie losowych tematów dla ruchu.", "Budowanie linków z domen niezwiązanych z encją."],
        "tags": ["topical-authority", "source", "entities"],
    },
    {
        "factor_id": "source-confidence-score",
        "name_pl": "Source Confidence Score",
        "category": "source_authority",
        "definition_pl": "Ocena zaufania do źródeł użytych do wyprowadzenia odpowiedzi, instrukcji lub zestawu kroków.",
        "mechanism_pl": "System wybierający kroki dla zapytania how-to może agregować informacje z wielu źródeł i oceniać ich wiarygodność oraz zgodność.",
        "how_to_satisfy_pl": "Dla poradników opieraj kroki na źródłach pierwotnych, dokumentacji producentów, danych własnych i zgodnych instrukcjach z autorytatywnych miejsc.",
        "example_pl": "Instrukcja migracji GA4 cytuje dokumentację Google, checklistę wdrożeniową i realny case, a nie tylko komentarz z forum.",
        "audit_checks_pl": ["Czy źródła kroków są wiarygodne?", "Czy kroki są zgodne między źródłami?", "Czy konflikt źródeł jest wyjaśniony?"],
        "measurement_inputs": ["sources", "citations", "how_to_steps"],
        "source_patents": [{"patent_id": "US10585927B1", "support_type": "direct"}],
        "evidence_ids": ["US10585927B1-text-abstract", "US10585927B1-text-claims", "US10585927B1-fig-p003"],
        "evidence_summary_pl": "Patent opisuje określanie zestawu kroków odpowiadających zapytaniu how-to i łączenie kroków z wielu źródeł.",
        "seo_inference_level": "direct",
        "confidence": "high",
        "anti_patterns_pl": ["Instrukcje bez źródła.", "Kroki sprzeczne z dokumentacją narzędzia."],
        "tags": ["how-to", "sources", "confidence"],
    },
    {
        "factor_id": "multi-source-consensus",
        "name_pl": "Multi-Source Consensus",
        "category": "source_authority",
        "definition_pl": "Wzmocnienie twierdzenia lub kroku przez zgodność wielu niezależnych, wiarygodnych źródeł.",
        "mechanism_pl": "Gdy kilka źródeł niezależnie wskazuje ten sam fakt lub krok, system ma mocniejszą podstawę do użycia go w odpowiedzi lub rankingu informacji.",
        "how_to_satisfy_pl": "Dla kluczowych porad i statystyk pokaż zgodność co najmniej 2-3 niezależnych źródeł, zwłaszcza przy YMYL i decyzjach zakupowych.",
        "example_pl": "Rekomendację dotyczącą canonicali poprzyj dokumentacją Google, testem własnym i case study technicznym.",
        "audit_checks_pl": ["Czy źródła są niezależne?", "Czy potwierdzają ten sam claim?", "Czy podano źródło pierwotne zamiast cytowania cytowania?"],
        "measurement_inputs": ["citations", "source_graph", "claims"],
        "source_patents": [{"patent_id": "US10585927B1", "support_type": "direct"}, {"patent_id": "US8954412B1", "support_type": "supporting"}],
        "evidence_ids": ["US10585927B1-text-abstract", "US8954412B1-text-abstract"],
        "evidence_summary_pl": "Źródła opisują agregację kroków i potwierdzanie faktów w dokumentach.",
        "seo_inference_level": "direct",
        "confidence": "high",
        "anti_patterns_pl": ["Trzy linki do tej samej grupy medialnej.", "Źródła wtórne bez źródła pierwotnego."],
        "tags": ["consensus", "citations", "trust"],
    },
    {
        "factor_id": "citation-quality-source-verifiability",
        "name_pl": "Citation Quality and Source Verifiability",
        "category": "source_authority",
        "definition_pl": "Jakość cytowań rozumiana jako łatwość weryfikacji, źródło pierwotne, jasna atrybucja i przydatność cytatu dla odpowiedzi generatywnej.",
        "mechanism_pl": "Systemy generatywne z cytowaniami mogą wybierać dokumenty i fragmenty, które dają się powiązać z konkretnym źródłem i potwierdzają odpowiedź.",
        "how_to_satisfy_pl": "Dodawaj inline attribution, link do źródła pierwotnego, konkretną liczbę lub fakt i krótkie zdanie możliwe do zacytowania.",
        "example_pl": "Zamiast 'badania pokazują', napisz: 'Według dokumentacji Google z marca 2025, parametr X wpływa na Y' i podaj URL.",
        "audit_checks_pl": ["Czy każdy mocny claim ma atrybucję?", "Czy źródło jest pierwotne?", "Czy fragment da się wyrwać z kontekstu bez utraty sensu?"],
        "measurement_inputs": ["content", "citations", "source_urls"],
        "source_patents": [{"patent_id": "US20250103640A1", "support_type": "direct"}, {"patent_id": "US20250217626A1", "support_type": "supporting"}],
        "evidence_ids": ["US20250103640A1-text-abstract", "US20250217626A1-text-abstract"],
        "evidence_summary_pl": "Patenty dotyczą generatywnych odpowiedzi z cytowaniami oraz generowania treści na podstawie wybranego source content.",
        "seo_inference_level": "moderate",
        "confidence": "medium",
        "anti_patterns_pl": ["Linki do agregatorów zamiast źródeł pierwotnych.", "Cytat bez wskazania, co dokładnie potwierdza."],
        "tags": ["citations", "ai-overviews", "source-verifiability"],
    },
    {
        "factor_id": "how-to-step-consensus",
        "name_pl": "How-To Step Consensus",
        "category": "content_quality",
        "definition_pl": "Zgodność i kompletność kroków instrukcji względem wielu źródeł oraz intencji użytkownika how-to.",
        "mechanism_pl": "System może identyfikować zapytania how-to, grupować kroki z wielu źródeł i prezentować zestaw kroków odpowiadający zadaniu.",
        "how_to_satisfy_pl": "Twórz kroki atomowe, w prawidłowej kolejności, z warunkami wejścia/wyjścia i źródłem, jeśli krok wynika z dokumentacji.",
        "example_pl": "Poradnik 'jak zmienić domenę bez utraty SEO' powinien mieć pre-migration, redirect map, staging, launch, monitoring i rollback.",
        "audit_checks_pl": ["Czy kroki są w kolejności wykonania?", "Czy brakuje warunków wstępnych?", "Czy instrukcja pokrywa typowe warianty zadania?"],
        "measurement_inputs": ["content", "how_to_steps", "sources"],
        "source_patents": [{"patent_id": "US10585927B1", "support_type": "direct"}],
        "evidence_ids": ["US10585927B1-text-abstract", "US10585927B1-text-claims"],
        "evidence_summary_pl": "Patent bezpośrednio dotyczy identyfikowania i prezentowania kroków dla zapytań how-to.",
        "seo_inference_level": "direct",
        "confidence": "high",
        "anti_patterns_pl": ["Esej zamiast kroków.", "Kroki pomijające najczęstszy błąd lub wariant."],
        "tags": ["how-to", "instructions", "steps"],
    },
    {
        "factor_id": "position-normalized-ctr",
        "name_pl": "Position-Normalized CTR",
        "category": "behavioral_signals",
        "definition_pl": "CTR skorygowany o oczekiwaną klikalność danej pozycji w SERP.",
        "mechanism_pl": "Porównanie rzeczywistego CTR z bazowym CTR pozycji pozwala ocenić, czy wynik jest wybierany częściej lub rzadziej niż oczekiwano.",
        "how_to_satisfy_pl": "Pisz title i description jak obietnicę odpowiedzi: konkretnie, odróżniająco i zgodnie z intencją, bez clickbaitowej niespójności.",
        "example_pl": "Dla pozycji 4 tytuł 'Checklist migracji SEO: 42 testy przed zmianą domeny' może wygrać z generycznym 'Migracja SEO - poradnik'.",
        "audit_checks_pl": ["Czy CTR w GSC przekracza średnią dla pozycji?", "Czy title komunikuje unikalną wartość?", "Czy snippet nie obiecuje czegoś, czego strona nie daje?"],
        "measurement_inputs": ["gsc", "serp_position", "title", "meta_description"],
        "source_patents": [{"patent_id": "US8788477B1", "support_type": "direct"}],
        "evidence_ids": ["US8788477B1-text-abstract"],
        "evidence_summary_pl": "Patent opisuje użycie query logów, selekcji wyników i pozycji wyników do identyfikacji autorytatywnych stron dla zapytania.",
        "seo_inference_level": "moderate",
        "confidence": "medium",
        "anti_patterns_pl": ["Title pisany tylko pod exact match.", "Clickbait zwiększający pogo-sticking."],
        "tags": ["ctr", "serp", "behavior"],
    },
    {
        "factor_id": "query-specific-selection-share",
        "name_pl": "Query-Specific Selection Share",
        "category": "behavioral_signals",
        "definition_pl": "Udział wyborów danego URL dla konkretnego zapytania po obejrzeniu SERP.",
        "mechanism_pl": "System może uczyć się preferencji użytkowników na poziomie query, niezależnie od globalnej popularności strony.",
        "how_to_satisfy_pl": "Dopasuj snippet do mikrointencji query; testuj różne tytuły dla zapytań, w których masz wysokie impressions i słaby CTR.",
        "example_pl": "Dla query 'audyt schema product' tytuł powinien obiecywać audyt schema Product, nie ogólny poradnik structured data.",
        "audit_checks_pl": ["Czy dane GSC są analizowane per query, nie tylko per page?", "Czy snippet odpowiada dokładnej intencji?", "Czy strona spełnia obietnicę snippetu?"],
        "measurement_inputs": ["gsc", "query", "url", "serp_snippet"],
        "source_patents": [{"patent_id": "US8788477B1", "support_type": "direct"}],
        "evidence_ids": ["US8788477B1-text-abstract"],
        "evidence_summary_pl": "Patent opisuje metryki z query logów i selekcji konkretnych wyników dla konkretnego zapytania.",
        "seo_inference_level": "direct",
        "confidence": "high",
        "anti_patterns_pl": ["Optymalizacja strony po średnim CTR bez rozbicia na query.", "Ten sam title dla kilku różnych intencji."],
        "tags": ["ctr", "query", "behavior"],
    },
    {
        "factor_id": "site-engagement-duration",
        "name_pl": "Site Engagement Duration",
        "category": "behavioral_signals",
        "definition_pl": "Zagregowany czas lub jakość zaangażowania użytkowników z witryną, interpretowana jako długoterminowy sygnał satysfakcji.",
        "mechanism_pl": "Warianty systemów rankingowych mogą wykorzystywać agregaty zachowania dla witryny, ale lokalny patent wymaga uzupełnienia przed mocnym wnioskiem.",
        "how_to_satisfy_pl": "Zadbaj o szybkie dojście do odpowiedzi, logiczne następne kroki, linkowanie wewnętrzne i elementy, które realnie pomagają kontynuować zadanie.",
        "example_pl": "Po definicji dodaj diagnostykę, przykłady i checklistę wdrożenia, zamiast kończyć tekst po ogólnym wstępie.",
        "audit_checks_pl": ["Czy użytkownik dostaje odpowiedź bez scrollowania przez wypełniacz?", "Czy ma jasny następny krok?", "Czy sekcje utrzymują intencję?"],
        "measurement_inputs": ["analytics", "engagement_time", "internal_links"],
        "source_patents": [{"patent_id": "US9195944B1", "support_type": "direct"}],
        "evidence_ids": ["US9195944B1-text-abstract"],
        "evidence_summary_pl": "Patent opisuje scoring site quality na podstawie pomiarów czasu wizyt użytkowników w zasobach witryny.",
        "seo_inference_level": "direct",
        "confidence": "high",
        "anti_patterns_pl": ["Długi wstęp bez odpowiedzi.", "Brak ścieżki do kolejnego działania użytkownika."],
        "tags": ["engagement", "behavior", "site-quality"],
    },
    {
        "factor_id": "authoritative-content-exemption",
        "name_pl": "Authoritative Content Exemption",
        "category": "source_authority",
        "definition_pl": "Hipoteza, że treści o silnych zewnętrznych sygnałach autorytetu mogą być mniej podatne na krótkoterminowe słabe sygnały zachowania.",
        "mechanism_pl": "Jeśli źródło jest silnie potwierdzone linkami, cytowaniami, wzmiankami i historią wyników, system może traktować chwilowe spadki inaczej niż przy słabym źródle.",
        "how_to_satisfy_pl": "Buduj autorytet tematyczny przez cytowania, profile autorów, dane pierwotne i stabilne klastry, zamiast polegać wyłącznie na optymalizacji snippetów.",
        "example_pl": "Raport branżowy cytowany przez media i linkowany przez dokumentacje może utrzymać widoczność mimo okresowo niższego CTR.",
        "audit_checks_pl": ["Czy treść ma linki i cytowania zewnętrzne?", "Czy źródło jest rozpoznawalne w temacie?", "Czy istnieją branded + topic searches?"],
        "measurement_inputs": ["backlinks", "mentions", "brand_queries", "citation_graph"],
        "source_patents": [{"patent_id": "US20120016870A1", "support_type": "missing_source"}],
        "evidence_ids": ["US20120016870A1-text-abstract"],
        "evidence_summary_pl": "Lokalny PDF jest brakujący; do czasu uzupełnienia traktować jako hipotezę niskiego confidence.",
        "seo_inference_level": "speculative",
        "confidence": "low",
        "anti_patterns_pl": ["Ignorowanie CTR przy braku autorytetu.", "Twierdzenie o odporności bez linków/cytowań."],
        "tags": ["authority", "behavior", "resilience"],
    },
    {
        "factor_id": "branded-search-topic-affinity",
        "name_pl": "Branded Search Topic Affinity",
        "category": "source_authority",
        "definition_pl": "Współwystępowanie marki lub wydawcy z terminami tematycznymi w zapytaniach, treściach i wzmiankach.",
        "mechanism_pl": "Wzorce endorsement, opinii i wzmianek mogą łączyć wydawcę lub markę z określonymi tematami i encjami.",
        "how_to_satisfy_pl": "Publikuj rozpoznawalne serie, raporty, narzędzia i case studies, które generują zapytania brand + topic oraz wzmianki bezlinkowe.",
        "example_pl": "Wzrost zapytań 'twoja marka schema audit' sygnalizuje powiązanie marki z konkretną usługą lub tematem.",
        "audit_checks_pl": ["Czy GSC pokazuje brand + topic queries?", "Czy marka współwystępuje z tematem w zewnętrznych wzmiankach?", "Czy treści mają konsekwentne nazewnictwo?"],
        "measurement_inputs": ["gsc", "mentions", "social_posts", "brand_queries"],
        "source_patents": [{"patent_id": "US20130055089A1", "support_type": "supporting"}, {"patent_id": "US10832001B2", "support_type": "supporting"}],
        "evidence_ids": ["US20130055089A1-text-abstract", "US10832001B2-text-abstract"],
        "evidence_summary_pl": "Patenty dotyczą endorsementów oraz identyfikacji opinii, co wspiera analizę powiązań marki z tematami i sentymentem.",
        "seo_inference_level": "moderate",
        "confidence": "medium",
        "anti_patterns_pl": ["Brand mentions bez związku z tematem.", "Jednorazowa kampania bez trwałego klastra."],
        "tags": ["brand", "mentions", "topic-affinity"],
    },
    {
        "factor_id": "brand-mentions-authority-proxy",
        "name_pl": "Brand Mentions as Authority Proxy",
        "category": "source_authority",
        "definition_pl": "Wzmianki o marce, autorze lub źródle jako pomocniczy proxy autorytetu, szczególnie gdy współwystępują z encjami tematu.",
        "mechanism_pl": "Systemy analizy opinii i endorsementów mogą wykrywać wzorce, w których marka jest wymieniana jako źródło, ekspert lub punkt odniesienia.",
        "how_to_satisfy_pl": "Twórz zasoby cytowalne: dane własne, benchmarki, definicje i narzędzia, które inni będą naturalnie wymieniać przy danym temacie.",
        "example_pl": "Raport 'SEO patents map' cytowany w newsletterach i podcastach buduje powiązanie autora z patent-based SEO.",
        "audit_checks_pl": ["Czy wzmianki pojawiają się poza własną domeną?", "Czy są w kontekście właściwego tematu?", "Czy wzmianki mają pozytywny lub ekspercki charakter?"],
        "measurement_inputs": ["mentions", "sentiment", "entity_cooccurrence"],
        "source_patents": [{"patent_id": "US10832001B2", "support_type": "supporting"}, {"patent_id": "US20130055089A1", "support_type": "supporting"}],
        "evidence_ids": ["US10832001B2-text-abstract", "US20130055089A1-text-abstract"],
        "evidence_summary_pl": "Źródła dotyczą opinii i endorsementów, a SEO interpretacja wymaga ostrożności.",
        "seo_inference_level": "moderate",
        "confidence": "medium",
        "anti_patterns_pl": ["Kupione wzmianki bez kontekstu.", "Wzmianki negatywne lub niezwiązane z tematem."],
        "tags": ["brand", "mentions", "authority"],
    },
    {
        "factor_id": "human-likeness-score",
        "name_pl": "Human-Likeness Score",
        "category": "generative_ai",
        "definition_pl": "Ocena, czy tekst generowany brzmi i zachowuje się jak naturalny tekst człowieka, z konkretem, zmiennością i osadzeniem w doświadczeniu.",
        "mechanism_pl": "Modele oceny jakości NLG mogą porównywać wygenerowane wyjścia z cechami tekstu naturalnego, spójnością, pokryciem danych i jakością językową.",
        "how_to_satisfy_pl": "Dodawaj własne dane, konkretne nazwy narzędzi, daty, przypadki, wnioski autora i naturalną zmienność zdań; unikaj sterylnej symetrii LLM.",
        "example_pl": "Zamiast 'warto monitorować wyniki', napisz, jakie alerty ustawiono w GSC, kiedy i jaki błąd wykryły.",
        "audit_checks_pl": ["Czy tekst zawiera doświadczenie autora?", "Czy są konkretne dane i daty?", "Czy styl nie jest jednorodnie generyczny?"],
        "measurement_inputs": ["content", "author_notes", "case_data"],
        "source_patents": [{"patent_id": "US12073189B2", "support_type": "direct"}, {"patent_id": "US20240012999A1", "support_type": "direct"}, {"patent_id": "US12223273B2", "support_type": "direct"}],
        "evidence_ids": ["US12073189B2-text-abstract", "US20240012999A1-text-abstract", "US12223273B2-text-abstract"],
        "evidence_summary_pl": "Seria patentów opisuje modele oceny jakości generacji języka naturalnego.",
        "seo_inference_level": "moderate",
        "confidence": "medium",
        "anti_patterns_pl": ["Równe akapity bez danych własnych.", "Ogólne porady możliwe do wygenerowania dla każdej branży."],
        "tags": ["ai-content", "human-likeness", "experience"],
    },
    {
        "factor_id": "non-syntheticity-index",
        "name_pl": "Non-Syntheticity Index",
        "category": "generative_ai",
        "definition_pl": "Praktyczna ocena, czy tekst zawiera sygnały pochodzenia z realnej obserwacji, danych i procesu, a nie tylko syntetycznego uśrednienia.",
        "mechanism_pl": "Ocena jakości generacji i porównania semantyczne mogą ujawniać teksty zbyt gładkie, zbyt ogólne lub słabo zakotwiczone w źródłach.",
        "how_to_satisfy_pl": "Wprowadzaj dane pierwotne, cytowalne obserwacje, konkretne ograniczenia, błędy i decyzje, których nie da się łatwo zgadnąć bez doświadczenia.",
        "example_pl": "W audycie contentu pokaż wynik crawl logów, liczbę URL-i dotkniętych problemem i decyzję, której alternatywy odrzucono.",
        "audit_checks_pl": ["Czy są dane niepubliczne lub case-specific?", "Czy są decyzje i tradeoffy?", "Czy tekst unika pustych uniwersalnych fraz?"],
        "measurement_inputs": ["content", "case_data", "source_materials"],
        "source_patents": [{"patent_id": "US12223273B2", "support_type": "direct"}, {"patent_id": "US20220067309A1", "support_type": "supporting"}],
        "evidence_ids": ["US12223273B2-text-abstract", "US20220067309A1-text-abstract"],
        "evidence_summary_pl": "Patenty o ocenie jakości NLG wspierają audyt syntetyczności jako jakościowy sygnał treści.",
        "seo_inference_level": "moderate",
        "confidence": "medium",
        "anti_patterns_pl": ["Generic AI prose.", "Deklarowanie eksperckości bez danych i przykładów."],
        "tags": ["ai-content", "originality", "experience"],
    },
    {
        "factor_id": "citable-fragment-density",
        "name_pl": "Citable Fragment Density",
        "category": "generative_ai",
        "definition_pl": "Gęstość krótkich, samodzielnych fragmentów, które można bezpiecznie zacytować w odpowiedzi generatywnej lub featured answer.",
        "mechanism_pl": "Systemy odpowiedzi generatywnych z cytowaniami preferują fragmenty, które są konkretne, źródłowe, jednoznaczne i dobrze osadzone w dokumencie.",
        "how_to_satisfy_pl": "Każdy H2/H3 zaczynaj od jednozdaniowej odpowiedzi, a statystyki zapisuj z atrybucją inline i źródłem pierwotnym.",
        "example_pl": "Sekcja 'Co to jest crawl budget?' zaczyna się definicją w 25 słowach, potem dopiero rozwija wyjątki i przykłady.",
        "audit_checks_pl": ["Czy każdy akapit ma topic sentence?", "Czy fragment ma źródło lub atrybucję?", "Czy może zostać wyrwany bez utraty sensu?"],
        "measurement_inputs": ["content", "citations", "headings"],
        "source_patents": [{"patent_id": "US20250217626A1", "support_type": "direct"}, {"patent_id": "US20250103640A1", "support_type": "direct"}],
        "evidence_ids": ["US20250217626A1-text-abstract", "US20250103640A1-text-abstract"],
        "evidence_summary_pl": "Patenty dotyczą generowania treści na podstawie źródeł oraz generatywnych odpowiedzi z cytowaniami.",
        "seo_inference_level": "moderate",
        "confidence": "medium",
        "anti_patterns_pl": ["Długie akapity bez jednoznacznego claimu.", "Definicje rozproszone po kilku zdaniach."],
        "tags": ["ai-overviews", "citability", "fragments"],
    },
    {
        "factor_id": "source-authority-generative-context",
        "name_pl": "Source Authority for Generative Context",
        "category": "generative_ai",
        "definition_pl": "Prawdopodobieństwo, że źródło zostanie uznane za wystarczająco wiarygodne do użycia w kontekście odpowiedzi generatywnej.",
        "mechanism_pl": "Systemy LLM mogą uczyć się selektywnego przewidywania i dobierania kontekstu na podstawie jakości źródła, confidence i zgodności z pytaniem.",
        "how_to_satisfy_pl": "Łącz autorytet tematyczny, cytowalne fragmenty, aktualność, źródła pierwotne i dobrze opisane dane autora/wydawcy.",
        "example_pl": "Raport SEO z metodologią, tabelą danych i profilami autorów ma większą użyteczność jako źródło odpowiedzi niż krótki post opinii.",
        "audit_checks_pl": ["Czy źródło ma metodologię?", "Czy fragmenty są cytowalne?", "Czy autor/wydawca jest weryfikowalny?", "Czy dane są aktualne?"],
        "measurement_inputs": ["content", "citations", "author_profile", "source_quality"],
        "source_patents": [{"patent_id": "US20240428015A1", "support_type": "supporting"}, {"patent_id": "US20250356223A1", "support_type": "missing_source"}],
        "evidence_ids": ["US20240428015A1-text-abstract", "US20250356223A1-text-abstract"],
        "evidence_summary_pl": "Lokalny patent o self-evaluation wspiera ocenę confidence; drugi wskazany patent wymaga uzupełnienia.",
        "seo_inference_level": "speculative",
        "confidence": "low",
        "anti_patterns_pl": ["Brak metodologii przy danych.", "Treść opiniowa przedstawiana jako źródło faktów."],
        "tags": ["llm", "generative-context", "source-quality"],
    },
    {
        "factor_id": "query-intent-classification-alignment",
        "name_pl": "Query Intent Classification Alignment",
        "category": "content_quality",
        "definition_pl": "Dopasowanie formatu, zakresu i głębokości treści do sklasyfikowanej intencji zapytania.",
        "mechanism_pl": "Patent LLM intent classification wskazuje, że zapytania można klasyfikować modelami językowymi, a odpowiedź powinna pasować do rozpoznanej intencji.",
        "how_to_satisfy_pl": "Przed pisaniem oznacz intencję jako informacyjną, porównawczą, transakcyjną, lokalną, how-to lub troubleshooting i dobierz układ treści.",
        "example_pl": "Query 'best log file analyzer for SEO' wymaga porównania narzędzi i kryteriów wyboru, a nie definicji log files.",
        "audit_checks_pl": ["Czy format pasuje do intencji?", "Czy pierwsza sekcja odpowiada na query?", "Czy CTA nie koliduje z etapem użytkownika?"],
        "measurement_inputs": ["query", "content", "intent_labels", "serp_features"],
        "source_patents": [{"patent_id": "US20240135187A1", "support_type": "direct"}],
        "evidence_ids": ["US20240135187A1-text-abstract", "US20240135187A1-fig-p003"],
        "evidence_summary_pl": "Patent dotyczy trenowania LLM do klasyfikacji intencji zapytań.",
        "seo_inference_level": "direct",
        "confidence": "high",
        "anti_patterns_pl": ["Definicja dla query porównawczego.", "Sprzedażowy landing dla query troubleshooting."],
        "tags": ["intent", "query", "content-design"],
    },
    {
        "factor_id": "query-embedding-source-match",
        "name_pl": "Query Embedding Source Match",
        "category": "generative_ai",
        "definition_pl": "Dopasowanie źródłowych dokumentów do zapytania przez reprezentacje embeddingowe i semantyczne, nie tylko przez exact match.",
        "mechanism_pl": "Systemy przetwarzania dokumentów mogą używać query embeddings do wyboru fragmentów i dokumentów w chmurze lub indeksie.",
        "how_to_satisfy_pl": "Pisz sekcje semantycznie pełne: synonimy, warianty zapytań, przykłady i terminy powiązane powinny naturalnie pokrywać przestrzeń intencji.",
        "example_pl": "Sekcja o 'renderowaniu JS' powinna obejmować hydration, CSR, SSR, dynamic rendering, crawl queue i indexing delay.",
        "audit_checks_pl": ["Czy tekst pokrywa warianty językowe query?", "Czy przykłady odpowiadają realnym problemom?", "Czy sekcje są retrievable jako samodzielne fragmenty?"],
        "measurement_inputs": ["content", "query_variants", "embeddings"],
        "source_patents": [{"patent_id": "US20250103826A1", "support_type": "direct"}],
        "evidence_ids": ["US20250103826A1-text-abstract"],
        "evidence_summary_pl": "Patent opisuje przetwarzanie dokumentów z wykorzystaniem query embeddings.",
        "seo_inference_level": "moderate",
        "confidence": "medium",
        "anti_patterns_pl": ["Exact-match stuffing.", "Sekcje zbyt krótkie, by mogły być dobrane semantycznie."],
        "tags": ["embeddings", "semantic-search", "retrieval"],
    },
    {
        "factor_id": "headline-summary-fit",
        "name_pl": "Headline Summary Fit",
        "category": "content_quality",
        "definition_pl": "Zgodność nagłówka z najważniejszą treścią dokumentu oraz zdolność nagłówka do streszczenia relewantnego sensu.",
        "mechanism_pl": "Automatyczne generowanie nagłówków wymaga identyfikacji istotnych fragmentów i skrócenia ich bez utraty sensu.",
        "how_to_satisfy_pl": "Tytuł i H1 powinny precyzyjnie streszczać główną odpowiedź, a nie tylko zawierać keyword.",
        "example_pl": "Lepsze: 'Canonical nie rozwiązuje duplikacji, gdy Google wybiera inny URL' niż 'Canonical SEO - poradnik'.",
        "audit_checks_pl": ["Czy headline obiecuje dokładnie to, co daje treść?", "Czy zawiera rozróżnik informacji?", "Czy nie jest clickbaitem?"],
        "measurement_inputs": ["title", "h1", "content_summary", "serp"],
        "source_patents": [{"patent_id": "US9619450B2", "support_type": "direct"}],
        "evidence_ids": ["US9619450B2-text-abstract", "US9619450B2-text-claims"],
        "evidence_summary_pl": "Patent dotyczy automatycznego generowania nagłówków z treści.",
        "seo_inference_level": "direct",
        "confidence": "high",
        "anti_patterns_pl": ["Headline obiecujący listę, gdy tekst jest esejem.", "Keyword-first title bez wartości informacyjnej."],
        "tags": ["headlines", "summary", "ctr"],
    },
    {
        "factor_id": "opinion-subjectivity-detection",
        "name_pl": "Opinion and Subjectivity Detection",
        "category": "content_quality",
        "definition_pl": "Rozróżnienie faktów, opinii i ocen subiektywnych w dokumencie.",
        "mechanism_pl": "System ML może identyfikować opinie w dokumentach, co pomaga oddzielać twierdzenia wymagające dowodu od komentarza eksperckiego.",
        "how_to_satisfy_pl": "Oznaczaj opinie jako opinie, fakty jako fakty, a rekomendacje uzasadniaj danymi lub doświadczeniem autora.",
        "example_pl": "Napisz 'uważam, że ten test jest lepszy dla małych sklepów, bo...' zamiast przedstawiać preferencję jako uniwersalny fakt.",
        "audit_checks_pl": ["Czy opinie są jawnie oznaczone?", "Czy fakty mają dowód?", "Czy rekomendacje mają kryteria?"],
        "measurement_inputs": ["content", "claims", "author_notes"],
        "source_patents": [{"patent_id": "US10832001B2", "support_type": "direct"}],
        "evidence_ids": ["US10832001B2-text-abstract", "US10832001B2-text-claims"],
        "evidence_summary_pl": "Patent dotyczy użycia uczenia maszynowego do identyfikacji opinii w dokumentach.",
        "seo_inference_level": "direct",
        "confidence": "high",
        "anti_patterns_pl": ["Opinie jako bezwarunkowe fakty.", "Brak kryteriów przy rekomendacji produktu."],
        "tags": ["opinions", "claims", "editorial-quality"],
    },
    {
        "factor_id": "task-priority-response-fit",
        "name_pl": "Task Priority Response Fit",
        "category": "content_quality",
        "definition_pl": "Dopasowanie kolejności odpowiedzi do priorytetu zadań użytkownika, ryzyka i pilności.",
        "mechanism_pl": "Systemy automatycznej priorytetyzacji zadań pokazują, że kolejność elementów może wynikać z ważności, nie tylko chronologii.",
        "how_to_satisfy_pl": "Dla troubleshooting zaczynaj od najbardziej prawdopodobnych i krytycznych diagnoz, a dopiero później przechodź do rzadkich wariantów.",
        "example_pl": "W poradniku o spadku indeksacji najpierw sprawdź robots/noindex/statusy, potem canonicale, renderowanie i jakość treści.",
        "audit_checks_pl": ["Czy pierwsze kroki odpowiadają największemu ryzyku?", "Czy użytkownik wie, co zrobić najpierw?", "Czy rzadkie warianty nie dominują wstępu?"],
        "measurement_inputs": ["content", "task_list", "risk_model"],
        "source_patents": [{"patent_id": "US6961720B1", "support_type": "supporting"}],
        "evidence_ids": ["US6961720B1-text-abstract", "US6961720B1-text-claims"],
        "evidence_summary_pl": "Lokalny dodatkowy patent dotyczy automatycznej priorytetyzacji zadań; SEO zastosowanie jest pomocniczą inferencją.",
        "seo_inference_level": "speculative",
        "confidence": "low",
        "anti_patterns_pl": ["Checklisty losowo posortowane.", "Sekcje teoretyczne przed krytyczną diagnozą."],
        "tags": ["tasks", "troubleshooting", "content-structure"],
    },
]


CATEGORY_LABELS = {
    "content_quality": "Jakość i struktura treści",
    "entity_graph": "Encje i graf wiedzy",
    "source_authority": "Autorytet źródła",
    "structured_data_alignment": "Zgodność danych strukturalnych",
    "behavioral_signals": "Sygnały behawioralne",
    "generative_ai": "AI, cytowalność i retrieval",
}


@dataclass
class PageText:
    page_number: int
    text: str
    method: str


def ensure_dirs() -> None:
    for path in [DATA_DIR, DOCS_DIR, FIGURES_DIR, TEXT_DIR, SKILL_REF_DIR, SKILL_SCRIPT_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact(text: str, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def normalize_patent_id(raw: str) -> str | None:
    if not raw:
        return None
    token = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    token = token.replace("US0", "US", 1) if token.startswith("US0") else token
    m = re.match(r"US0*(\d+)([A-Z]\d)$", token)
    if m:
        return f"US{int(m.group(1))}{m.group(2)}"
    m = re.match(r"US0*(\d{4})(\d{7})(A\d)$", token)
    if m:
        return f"US{m.group(1)}{m.group(2)}{m.group(3)}"
    m = re.match(r"US(\d{11}A\d)$", token)
    if m:
        return f"US{m.group(1)}"
    m = re.match(r"US(\d{7,8})(B\d)$", token)
    if m:
        return token
    return token if token.startswith("US") else None


def patent_id_from_text(text: str) -> str | None:
    patterns = [
        r"US\s*0*([0-9]{7,8})\s*(B[12])",
        r"US\s*([0-9]{4})[ /]?0*([0-9]{7})\s*(A[12])",
        r"US\s*([0-9]{11})\s*(A[12])",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        if len(m.groups()) == 2:
            if m.group(2).upper().startswith("B"):
                return f"US{int(m.group(1))}{m.group(2).upper()}"
            return f"US{m.group(1)}{m.group(2).upper()}"
        if len(m.groups()) == 3:
            return f"US{m.group(1)}{m.group(2)}{m.group(3).upper()}"
    return None


def local_pdfs() -> list[Path]:
    return sorted(
        p
        for p in ROOT.glob("*.pdf")
        if not p.name.startswith(".") and p.parent == ROOT
    )


def read_pdf_pages(pdf_path: Path) -> tuple[list[PageText], int, dict[str, str]]:
    reader = PdfReader(str(pdf_path))
    pages: list[PageText] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append(PageText(index, clean_text(text), "pypdf"))
    metadata = {}
    if reader.metadata:
        for key, value in reader.metadata.items():
            metadata[str(key).lstrip("/")] = str(value)
    return pages, len(reader.pages), metadata


def render_page(pdf_path: Path, page_number: int, out_path: Path, dpi: int = 220) -> bool:
    if not GS:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("XDG_CACHE_HOME", str(KB_DIR / ".cache"))
    cmd = [
        GS,
        "-q",
        "-dNOPAUSE",
        "-dBATCH",
        "-sDEVICE=pnggray",
        f"-r{dpi}",
        f"-dFirstPage={page_number}",
        f"-dLastPage={page_number}",
        f"-sOutputFile={out_path}",
        str(pdf_path),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    return result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


def tesseract_text(image_path: Path, psm: str = "6") -> str:
    if not TESSERACT:
        return ""
    cmd = [TESSERACT, str(image_path), "stdout", "-l", "eng", "--psm", psm]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        return ""
    return clean_text(result.stdout)


def ocr_pdf_pages(pdf_path: Path, page_count: int, patent_hint: str) -> list[PageText]:
    pages: list[PageText] = []
    with tempfile.TemporaryDirectory(dir="/private/tmp") as tmp:
        tmp_path = Path(tmp)
        for page_number in range(1, page_count + 1):
            image_path = tmp_path / f"{patent_hint}_p{page_number:03d}.png"
            if not render_page(pdf_path, page_number, image_path, dpi=220):
                pages.append(PageText(page_number, "", "ocr_failed"))
                continue
            pages.append(PageText(page_number, tesseract_text(image_path, psm="6"), "ocr"))
    return pages


def detect_title(text: str, known_title: str | None) -> str:
    if known_title and not known_title.startswith("Unknown locally"):
        return known_title
    m = re.search(r"\(54\)\s+(.{8,220}?)(?:\(\d{2}\)|Applicant:|References Cited|U\.S\. Patent|$)", text, re.S)
    if m:
        return compact(m.group(1), 160).replace("  ", " ").strip(" .")
    return known_title or "Unknown"


def detect_date(text: str) -> str | None:
    patterns = [
        r"Date of Patent\s*:?\s+\*?([A-Z][a-z]{2}\.?\s+\d{1,2},\s+\d{4})",
        r"Pub\. Date\s*:?\s+([A-Z][a-z]{2}\.?\s+\d{1,2},\s+\d{4})",
        r"Date of Patent\s*:?\s+\*?([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return None


def extract_section(text: str, starts: list[str], stops: list[str], limit: int = 1300) -> str:
    upper = text.upper()
    start_pos = None
    for start in starts:
        idx = upper.find(start.upper())
        if idx != -1:
            start_pos = idx + len(start)
            break
    if start_pos is None:
        return ""
    stop_pos = len(text)
    for stop in stops:
        idx = upper.find(stop.upper(), start_pos + 20)
        if idx != -1:
            stop_pos = min(stop_pos, idx)
    return compact(text[start_pos:stop_pos], limit=limit)


def first_claim(text: str) -> str:
    patterns = [
        r"What is claimed is:\s*(.*?)(?:\n\s*2\.|\n2\.|\Z)",
        r"CLAIMS\s*(?:\n| )1\.\s*(.*?)(?:\n\s*2\.|\n2\.|\Z)",
        r"1\.\s+(.{200,1800}?)(?:\n\s*2\.|\n2\.|\Z)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I | re.S)
        if m:
            return compact(m.group(1), 1300)
    return ""


def drawing_captions(text: str) -> dict[str, str]:
    captions: dict[str, str] = {}
    brief = extract_section(
        text,
        ["BRIEF DESCRIPTION OF THE DRAWINGS"],
        ["DETAILED DESCRIPTION", "DESCRIPTION OF EMBODIMENTS", "SUMMARY"],
        limit=6000,
    )
    for m in re.finditer(r"(FIG\.?\s*\d+[A-Z]?)\s+(?:is|illustrates|depicts|shows|are)\s+(.{20,300}?)(?=FIG\.?\s*\d+|$)", brief, flags=re.I | re.S):
        captions[m.group(1).upper().replace(" ", "")] = compact(m.group(2), 260)
    return captions


def is_figure_page(page: PageText) -> tuple[bool, str]:
    text = page.text or ""
    upper = text.upper()
    if re.search(r"\bSHEET\s+\d+\s+OF\s+\d+", upper):
        return True, "sheet_marker"
    if re.search(r"\bFIG\.?\s*\d+", upper) and len(text) < 3500 and "BRIEF DESCRIPTION OF THE DRAWINGS" not in upper[:700]:
        return True, "fig_marker_short_page"
    return False, ""


def visual_description_from_ocr(patent_id: str, page_number: int, ocr_text: str, captions: dict[str, str]) -> str:
    fig_refs = sorted(set(re.findall(r"FIG\.?\s*\d+[A-Z]?", ocr_text, flags=re.I)))
    caption_bits = []
    for ref in fig_refs[:4]:
        normalized = ref.upper().replace(" ", "")
        if normalized in captions:
            caption_bits.append(f"{ref}: {captions[normalized]}")
    labels = re.findall(r"\b(?:scoring engine|document system|index cluster|query|embedding|citation|source|entity|steps?|model|response|user|content|data)\b", ocr_text, flags=re.I)
    label_hint = ", ".join(sorted(set(x.lower() for x in labels))[:12])
    if caption_bits:
        base = "Strona figury zawiera: " + " ".join(caption_bits)
    else:
        base = "Strona figury lub diagramu patentowego wykryta automatycznie."
    if label_hint:
        base += f" OCR wskazuje etykiety/mechanizmy: {label_hint}."
    base += " Relacje strzałek i układ wymagają passu vision lub ręcznego przeglądu przed użyciem jako silny dowód."
    if patent_id == "US9679018B1" and page_number == 3:
        base = (
            "FIG. 1 pokazuje przepływ rankingowy: urządzenie klienta wysyła request dokumentów, system dokumentów zwraca odpowiedź, "
            "zainteresowania użytkownika i indeks dokumentów zasilają scoring engine, a scoring używa tabeli inverse document/entity frequencies. "
            "Diagram wzmacnia czynnik entity coverage i rarity, ale dowód wysokiego confidence powinien nadal opierać się na opisie/claimach."
        )
    return base


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def build_patent_inventory() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    expected_by_id = {item["patent_id"]: item for item in EXPECTED_PATENTS}
    patents: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    text_by_patent: dict[str, str] = {}

    for pdf_path in local_pdfs():
        if pdf_path.name.startswith(ARTICLE_PREFIX):
            reader = PdfReader(str(pdf_path))
            article_text = clean_text("\n".join(page.extract_text() or "" for page in reader.pages))
            text_by_patent[ARTICLE_ID] = article_text
            patents[ARTICLE_ID] = {
                "patent_id": ARTICLE_ID,
                "source_type": "article",
                "source_status": "local_article",
                "file_name": pdf_path.name,
                "title": "Jak pisać treść ekspercką, którą Google uznaje za autorytatywną? Mierzalne sygnały poza E-E-A-T według 202 patentów",
                "date": "2026-05-12",
                "page_count": len(reader.pages),
                "text_characters": len(article_text),
                "extraction_method": "pypdf",
                "ocr_status": "not_needed",
                "role": "hypothesis_map_not_final_evidence",
            }
            evidence.append(
                {
                    "evidence_id": f"{ARTICLE_ID}-map",
                    "patent_id": ARTICLE_ID,
                    "evidence_type": "description",
                    "page": None,
                    "source_file": pdf_path.name,
                    "text_pl": "Artykuł lokalny mapuje hipotezy i listę patentów. Nie jest używany jako źródło wysokiego confidence dla czynników.",
                    "quote_or_ocr": compact(article_text, 1200),
                    "support_role": "hypothesis_map",
                    "vision_status": "not_applicable",
                }
            )
            (TEXT_DIR / f"{ARTICLE_ID}.txt").write_text(article_text, encoding="utf-8")
            continue

        pages, page_count, metadata = read_pdf_pages(pdf_path)
        pypdf_text = clean_text("\n\n".join(page.text for page in pages))
        stem_id = STEM_TO_PATENT_ID.get(pdf_path.stem)
        patent_id = stem_id or patent_id_from_text(pypdf_text) or normalize_patent_id(pdf_path.stem) or pdf_path.stem
        extraction_method = "pypdf"
        ocr_status = "not_needed"

        if len(pypdf_text) < 1000:
            if not GS or not TESSERACT:
                ocr_status = "ocr_unavailable"
            else:
                pages = ocr_pdf_pages(pdf_path, page_count, patent_id)
                ocr_text = clean_text("\n\n".join(page.text for page in pages))
                if len(ocr_text) > len(pypdf_text):
                    pypdf_text = ocr_text
                    extraction_method = "ocr"
                    ocr_status = "ok" if ocr_text else "ocr_failed"
                    patent_id = stem_id or patent_id_from_text(ocr_text) or patent_id

        expected = expected_by_id.get(patent_id, {})
        title = detect_title(pypdf_text, expected.get("title"))
        date = detect_date(pypdf_text)
        text_by_patent[patent_id] = pypdf_text
        (TEXT_DIR / f"{patent_id}.txt").write_text(pypdf_text, encoding="utf-8")

        patents[patent_id] = {
            "patent_id": patent_id,
            "source_type": "patent",
            "source_status": "local_pdf",
            "file_name": pdf_path.name,
            "title": title,
            "date": date,
            "page_count": page_count,
            "text_characters": len(pypdf_text),
            "extraction_method": extraction_method,
            "ocr_status": ocr_status,
            "metadata": metadata,
        }

        abstract = extract_section(pypdf_text, ["(57) ABSTRACT", "ABSTRACT"], ["BACKGROUND", "SUMMARY", "BRIEF DESCRIPTION"], limit=1500)
        if abstract:
            evidence.append(
                {
                    "evidence_id": f"{patent_id}-text-abstract",
                    "patent_id": patent_id,
                    "evidence_type": "abstract",
                    "page": None,
                    "source_file": pdf_path.name,
                    "quote_or_ocr": abstract,
                    "text_pl": "Abstract patentu; używać jako podstawowy opis mechanizmu, nie jako samodzielny dowód aktualnego rankingu.",
                    "support_role": "textual_evidence",
                    "vision_status": "not_applicable",
                }
            )
        summary = extract_section(pypdf_text, ["SUMMARY"], ["BRIEF DESCRIPTION", "DETAILED DESCRIPTION", "DESCRIPTION"], limit=1500)
        if summary:
            evidence.append(
                {
                    "evidence_id": f"{patent_id}-text-summary",
                    "patent_id": patent_id,
                    "evidence_type": "summary",
                    "page": None,
                    "source_file": pdf_path.name,
                    "quote_or_ocr": summary,
                    "text_pl": "Sekcja summary/opis ogólny; dobra do interpretacji przepływu i intencji wynalazku.",
                    "support_role": "textual_evidence",
                    "vision_status": "not_applicable",
                }
            )
        claim = first_claim(pypdf_text)
        if claim:
            evidence.append(
                {
                    "evidence_id": f"{patent_id}-text-claims",
                    "patent_id": patent_id,
                    "evidence_type": "claim",
                    "page": None,
                    "source_file": pdf_path.name,
                    "quote_or_ocr": claim,
                    "text_pl": "Pierwszy wykryty claim; traktować jako silniejszy dowód zakresu patentu niż opis popularny.",
                    "support_role": "claim_evidence",
                    "vision_status": "not_applicable",
                }
            )
        if extraction_method == "ocr" and pypdf_text:
            evidence.append(
                {
                    "evidence_id": f"{patent_id}-ocr-overview",
                    "patent_id": patent_id,
                    "evidence_type": "ocr",
                    "page": None,
                    "source_file": pdf_path.name,
                    "quote_or_ocr": compact(pypdf_text, 1500),
                    "text_pl": "Tekst uzyskany OCR ze skanowanego PDF. Wymaga ostrożnego cytowania i kontroli błędów OCR.",
                    "support_role": "ocr_baseline",
                    "vision_status": "not_applicable",
                }
            )

        captions = drawing_captions(pypdf_text)
        for page in pages:
            is_fig, reason = is_figure_page(page)
            if not is_fig:
                continue
            figure_path = FIGURES_DIR / f"{patent_id}_p{page.page_number:03d}.png"
            if render_page(pdf_path, page.page_number, figure_path, dpi=220):
                figure_ocr = tesseract_text(figure_path, psm="11")
            else:
                figure_ocr = page.text
            evidence.append(
                {
                    "evidence_id": f"{patent_id}-fig-p{page.page_number:03d}",
                    "patent_id": patent_id,
                    "evidence_type": "figure",
                    "page": page.page_number,
                    "source_file": pdf_path.name,
                    "figure_file": str(figure_path.relative_to(KB_DIR)) if figure_path.exists() else None,
                    "detection_reason": reason,
                    "quote_or_ocr": compact(figure_ocr or page.text, 1500),
                    "text_pl": visual_description_from_ocr(patent_id, page.page_number, figure_ocr or page.text, captions),
                    "support_role": "figure_context",
                    "vision_status": "local_ocr_only_needs_vision_review",
                }
            )

    for expected in EXPECTED_PATENTS:
        patent_id = expected["patent_id"]
        if patent_id in patents:
            continue
        patents[patent_id] = {
            "patent_id": patent_id,
            "source_type": "patent",
            "source_status": "missing_source",
            "file_name": None,
            "title": expected["title"],
            "date": None,
            "page_count": None,
            "text_characters": 0,
            "extraction_method": "not_extracted",
            "ocr_status": "not_applicable",
            "download_note": "Referenced by local source article but no local PDF was present during build.",
        }

    return sorted(patents.values(), key=lambda x: x["patent_id"]), evidence, text_by_patent


def build_factors(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_evidence = {item["evidence_id"] for item in evidence}
    factors: list[dict[str, Any]] = []
    for seed in FACTOR_SEEDS:
        factor = dict(seed)
        requested = list(seed.get("evidence_ids", []))
        factor["evidence_ids"] = [item for item in requested if item in existing_evidence]
        missing = [item for item in requested if item not in existing_evidence]
        if missing:
            factor["missing_evidence_ids"] = missing
        factor["category_label_pl"] = CATEGORY_LABELS.get(factor["category"], factor["category"])
        factor["llm_usage_pl"] = (
            "Użyj tego rekordu jako reguły audytu lub briefu. Najpierw sprawdź evidence_ids i confidence; "
            "nie przedstawiaj inferencji patentowej jako potwierdzonego aktualnego czynnika rankingowego."
        )
        factors.append(factor)
    return sorted(factors, key=lambda x: (x["category"], x["factor_id"]))


def docs_header() -> str:
    return (
        "# Baza wiedzy SEO z patentów Google\n\n"
        "Ta baza jest evidence-first: patent opisuje mechanizm lub metodę, a nie automatycznie potwierdzony aktualny czynnik rankingowy. "
        "Dlatego każdy rekord ma `seo_inference_level`, `confidence` oraz powiązane `evidence_ids`.\n\n"
    )


def write_docs(factors: list[dict[str, Any]], patents: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> None:
    by_category: dict[str, list[dict[str, Any]]] = {}
    for factor in factors:
        by_category.setdefault(factor["category"], []).append(factor)

    lines = [docs_header(), "## Czynniki\n\n"]
    for category in sorted(by_category):
        lines.append(f"### {CATEGORY_LABELS.get(category, category)}\n\n")
        for factor in by_category[category]:
            lines.append(f"#### `{factor['factor_id']}` - {factor['name_pl']}\n\n")
            lines.append(f"**Definicja:** {factor['definition_pl']}\n\n")
            lines.append(f"**Mechanizm:** {factor['mechanism_pl']}\n\n")
            lines.append(f"**Jak zaspokoić:** {factor['how_to_satisfy_pl']}\n\n")
            lines.append(f"**Przykład:** {factor['example_pl']}\n\n")
            lines.append(f"**Confidence:** `{factor['confidence']}`; **inferencja:** `{factor['seo_inference_level']}`\n\n")
            evidence_ids = ", ".join(f"`{eid}`" for eid in factor.get("evidence_ids", [])) or "brak lokalnego dowodu"
            lines.append(f"**Evidence:** {evidence_ids}\n\n")
    (DOCS_DIR / "factors.md").write_text("".join(lines), encoding="utf-8")

    generation = [
        "# Workflow tworzenia treści\n\n",
        "1. Zdefiniuj query, intencję, główną encję i format odpowiedzi.\n",
        "2. Wybierz z `factors.jsonl` rekordy z kategoriami `content_quality`, `entity_graph`, `structured_data_alignment` i `generative_ai`.\n",
        "3. Zbuduj mapę top 10: encje, atrybuty, źródła, brakujące informacje i cytowalne fragmenty.\n",
        "4. Zaprojektuj outline tak, aby każdy H2/H3 odpowiadał na konkretną część intencji i zawierał topic sentence.\n",
        "5. Dla każdego mocnego claimu dodaj źródło pierwotne oraz wskaż, czy claim jest faktem, opinią czy rekomendacją.\n",
        "6. Po szkicu wykonaj pass: entity coverage, factual consistency, citable fragments, schema alignment i human-likeness.\n\n",
        "Minimalny wynik briefu: lista użytych `factor_id`, ryzyka confidence oraz konkretne zalecenia sekcja po sekcji.\n",
    ]
    (DOCS_DIR / "content-generation.md").write_text("".join(generation), encoding="utf-8")

    audit = [
        "# Workflow audytu treści\n\n",
        "1. Wyciągnij title, H1, H2/H3, schema, cytowania, dane liczbowe i główne encje.\n",
        "2. Oceń rekordy z `factors.jsonl`; nie używaj czynników `confidence=low` jako twardych zarzutów.\n",
        "3. Dla każdego problemu podaj: `factor_id`, obserwację, ryzyko, rekomendację i przykład poprawki.\n",
        "4. Oddziel błędy faktograficzne od szans optymalizacyjnych i od spekulatywnych hipotez patentowych.\n",
        "5. Zakończ checklistą priorytetów: high impact / medium impact / eksperyment.\n\n",
        "Minimalny wynik audytu: tabela czynników, status pass/fail/unknown oraz lista zmian gotowych do wdrożenia.\n",
    ]
    (DOCS_DIR / "content-audit.md").write_text("".join(audit), encoding="utf-8")

    coverage = {
        "patent_count": len([p for p in patents if p["source_type"] == "patent"]),
        "local_pdf_count": len([p for p in patents if p["source_status"] == "local_pdf"]),
        "missing_source_count": len([p for p in patents if p["source_status"] == "missing_source"]),
        "factor_count": len(factors),
        "evidence_count": len(evidence),
        "figure_evidence_count": len([e for e in evidence if e["evidence_type"] == "figure"]),
        "missing_patents": [p["patent_id"] for p in patents if p["source_status"] == "missing_source"],
    }
    (DATA_DIR / "coverage_report.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_skill(factors: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> None:
    SKILL_REF_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DATA_DIR / "factors.jsonl", SKILL_REF_DIR / "factors.jsonl")
    shutil.copy2(DATA_DIR / "evidence.jsonl", SKILL_REF_DIR / "evidence.jsonl")
    shutil.copy2(DOCS_DIR / "factors.md", SKILL_REF_DIR / "factors.md")

    search_script = SKILL_SCRIPT_DIR / "search_factors.py"
    search_script.write_text(
        """#!/usr/bin/env python3
import argparse, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FACTORS = ROOT / "references" / "factors.jsonl"

def load():
    with FACTORS.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)

def main():
    parser = argparse.ArgumentParser(description="Search Google patent SEO factors.")
    parser.add_argument("query", nargs="?", default="", help="Substring to search in id/name/tags/definition.")
    parser.add_argument("--category", default="", help="Filter by category.")
    parser.add_argument("--confidence", default="", help="Filter by confidence.")
    args = parser.parse_args()
    q = args.query.lower()
    for factor in load():
        haystack = " ".join([
            factor.get("factor_id", ""),
            factor.get("name_pl", ""),
            factor.get("definition_pl", ""),
            " ".join(factor.get("tags", [])),
        ]).lower()
        if q and q not in haystack:
            continue
        if args.category and factor.get("category") != args.category:
            continue
        if args.confidence and factor.get("confidence") != args.confidence:
            continue
        print(json.dumps(factor, ensure_ascii=False))

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    search_script.chmod(0o755)


def main() -> int:
    ensure_dirs()
    patents, evidence, _ = build_patent_inventory()
    factors = build_factors(evidence)

    (DATA_DIR / "patents.json").write_text(json.dumps(patents, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_jsonl(DATA_DIR / "evidence.jsonl", sorted(evidence, key=lambda x: x["evidence_id"]))
    write_jsonl(DATA_DIR / "factors.jsonl", factors)
    write_docs(factors, patents, evidence)
    sync_skill(factors, evidence)

    report = json.loads((DATA_DIR / "coverage_report.json").read_text(encoding="utf-8"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
