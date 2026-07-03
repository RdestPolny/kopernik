# Review modelu scoringu Kopernika

Stan po kalibracji z 2026-07-03. Czynników łącznie: **140**.

## Jak liczony jest wynik główny

```
wynik_grupy   = suma(wartość_oceny × impact) / suma(impact) × 100   [w obrębie grupy]
wartość_oceny = {0: 0.0, 1: 0.30, 2: 1.0}   (SCORE_VALUE_MAP)
ważone_grupy  = suma(wynik_grupy × waga_grupy) / suma(wag)
wynik_główny  = round(0.85 × ważone_grupy + 0.15 × pokrycie_fan_out) − kary_krytyczne
```

Pokrycie fan-out = % z 12 realnych pytań użytkowników AI pokrytych treścią reprezentatywnej podstrony (covered=1, partial=0.35, missing=0).

## Wagi kategorii (UI_GROUP_WEIGHTS)

| Kategoria | Waga | Czynników | Charakter |
|---|---|---|---|
| Techniczne SEO (`technical`) | 20 | 40 | HTML/schema/domena — sprawdzane programowo + schema |
| Wydajność (`performance`) | 10 | 5 | PageSpeed (progi Lighthouse) |
| On-page (`onpage`) | 10 | 22 | treść — LLM |
| E-E-A-T (`eeat`) | 25 | 26 | treść — LLM |
| Patenty Google (`patents`) | 15 | 26 | treść — LLM (czynniki z patentów Google) |
| AI / AEO (`ai_aeo`) | 20 | 21 | treść — LLM (ekstraktowalność dla AI) |

## Kary za krytyczne braki (odejmowane od wyniku głównego)

| Czynnik | Kara |
|---|---|
| HTTPS włączone | −12 pkt |
| Plik llms.txt obecny | −8 pkt |
| GPTBot (OpenAI) niezablokowany | −7 pkt |
| ClaudeBot (Anthropic) niezablokowany | −6 pkt |
| PerplexityBot niezablokowany | −6 pkt |
| Dostępny plik robots.txt | −6 pkt |
| Google-Extended niezablokowany (AI Overviews) | −4 pkt |

Maksymalna łączna kara: −49 pkt.

## Wszystkie czynniki i ich wagi (impact)

Impact = waga czynnika wewnątrz grupy. 3 = kluczowy, 2 = standard, ≤1 = podstawa/higiena (celowo zdemotowana — obecność prawie nie podnosi wyniku). Effort = szacowany koszt naprawy (do priorytetyzacji akcji, nie do wyniku).


### Techniczne SEO — waga grupy 20/100, czynników 40

| Impact | Czynnik | Zakres | Kara | id |
|---|---|---|---|---|
| 3 | Dowolne schema.org | HTML per strona |  | `any_schema` |
| 3 | Odpowiedni schema dla typu treści | treść (LLM per strona) |  | `appropriate_schema_for_content_type` |
| 3 | Schema Article / BlogPosting | HTML per strona |  | `article_schema` |
| 3 | Schema BreadcrumbList | HTML per strona |  | `breadcrumb_schema` |
| 3 | Schema FAQPage (bonus) | HTML per strona |  | `faq_schema_bonus` |
| 3 | Schema ItemList | HTML per strona |  | `itemlist_schema` |
| 3 | Plik llms.txt obecny | domena | −8 | `llms_txt_present` |
| 3 | Schema LocalBusiness / Organization | HTML per strona |  | `localbusiness_or_organization_schema` |
| 3 | Schema Organization | HTML per strona |  | `organization_schema` |
| 3 | Schema Person (zespół) | HTML per strona |  | `person_schema_team` |
| 3 | Schema Product lub Service | HTML per strona |  | `product_or_service_schema` |
| 3 | Pole 'author' w schema | HTML per strona |  | `schema_author_field` |
| 3 | Daty w schema (published / modified) | HTML per strona |  | `schema_dates` |
| 3 | Schema WebSite | HTML per strona |  | `website_schema` |
| 2 | Poprawna hierarchia nagłówków | HTML per strona |  | `heading_hierarchy` |
| 1.0 | ClaudeBot (Anthropic) niezablokowany | domena | −6 | `claudebot_not_blocked` |
| 1.0 | Google-Extended niezablokowany (AI Overviews) | domena | −4 | `google_extended_not_blocked` |
| 1.0 | GPTBot (OpenAI) niezablokowany | domena | −7 | `gptbot_not_blocked` |
| 1.0 | PerplexityBot niezablokowany | domena | −6 | `perplexitybot_not_blocked` |
| 0.5 | Tag canonical | HTML per strona |  | `canonical_tag` |
| 0.5 | Formularz kontaktowy (<form>) | HTML per strona |  | `contact_form_present` |
| 0.5 | Pojedynczy nagłówek H1 | HTML per strona |  | `h1_single` |
| 0.5 | HTTPS włączone | domena | −12 | `https_enabled` |
| 0.5 | Klikalny e-mail (mailto:) | HTML per strona |  | `mailto_link_present` |
| 0.5 | Obecna meta description | HTML per strona |  | `meta_description` |
| 0.5 | Dostępny plik robots.txt | domena | −6 | `robots_txt_accessible` |
| 0.5 | Sitemap XML obecna | domena |  | `sitemap_present` |
| 0.5 | Klikalny numer telefonu (tel:) | HTML per strona |  | `tel_link_present` |
| 0.25 | Kompresja odpowiedzi (gzip/brotli) | domena |  | `compression_enabled` |
| 0.25 | Crawl-delay w normie | domena |  | `crawl_delay_ok` |
| 0.25 | Tagi hreflang | domena |  | `hreflang_used` |
| 0.25 | HSTS (Strict-Transport-Security) | domena |  | `hsts_enabled` |
| 0.25 | Pokrycie obrazów atrybutem alt | HTML per strona |  | `image_alt_coverage` |
| 0.25 | Atrybut lang w <html> | HTML per strona |  | `lang_attribute` |
| 0.25 | Obecny tag <title> | HTML per strona |  | `meta_title_present` |
| 0.25 | Tagi Open Graph | HTML per strona |  | `og_tags` |
| 0.25 | Rozmiar HTML w normie | HTML per strona |  | `response_size_ok` |
| 0.25 | Semantyczne tagi HTML5 | HTML per strona |  | `semantic_html5_tags` |
| 0.25 | Link do sitemap w robots.txt | domena |  | `sitemap_in_robots` |
| 0.25 | Meta viewport (mobile) | HTML per strona |  | `viewport_meta` |

### Wydajność — waga grupy 10/100, czynników 5

| Impact | Czynnik | Zakres | Kara | id |
|---|---|---|---|---|
| 3 | CLS – Cumulative Layout Shift (mobile) | PageSpeed per strona |  | `cls_mobile_ok` |
| 3 | LCP – Largest Contentful Paint (mobile) | PageSpeed per strona |  | `lcp_mobile_ok` |
| 3 | Lighthouse Performance (mobile) | PageSpeed per strona |  | `performance_score_mobile` |
| 2 | FCP – First Contentful Paint (mobile) | PageSpeed per strona |  | `fcp_mobile_ok` |
| 2 | TBT – Total Blocking Time (mobile) | PageSpeed per strona |  | `tbt_mobile_ok` |

### On-page — waga grupy 10/100, czynników 22

| Impact | Czynnik | Zakres | Kara | id |
|---|---|---|---|---|
| 2 | Wyrażone wprost korzyści (nie tylko cechy) | treść (LLM per strona) |  | `benefits_stated_explicitly_not_just_features` |
| 2 | Spójna i unikalna tożsamość marki | treść (LLM per strona) |  | `brand_identity_consistent_and_unique` |
| 2 | Jasna definicja oferty/usługi | treść (LLM per strona) |  | `clear_offer_or_service_definition` |
| 2 | Jasno określony cel strony | treść (LLM per strona) |  | `clear_page_purpose_stated` |
| 2 | Prezentacja klientów / projektów | treść (LLM per strona) |  | `clients_or_projects_showcased` |
| 2 | Historia firmy / misja / historia powstania | treść (LLM per strona) |  | `company_history_mission_or_founding_story` |
| 2 | Czytelny formularz kontaktowy | treść (LLM per strona) |  | `contact_form_present_and_clear` |
| 2 | Kontakty per dział / rola | treść (LLM per strona) |  | `department_or_role_specific_contacts` |
| 2 | Głębia i kompleksowość ujęcia tematu | treść (LLM per strona) |  | `depth_comprehensive_treatment_of_topic` |
| 2 | Poprawna hierarchia nagłówków | treść (LLM per strona) |  | `heading_hierarchy_correct` |
| 2 | Linki wewnętrzne do kontekstowych treści | treść (LLM per strona) |  | `internal_links_to_contextual_content` |
| 2 | Linki wewnętrzne do elementów z kontekstem | treść (LLM per strona) |  | `internal_links_to_items_with_context` |
| 2 | Linki wewnętrzne do powiązanych treści | treść (LLM per strona) |  | `internal_links_to_related_content` |
| 2 | Linki wewnętrzne do usług/produktów | treść (LLM per strona) |  | `internal_links_to_services_or_products` |
| 2 | Widoczna data ostatniej aktualizacji | treść (LLM per strona) |  | `last_updated_date_visible` |
| 2 | Czytelna nawigacja do kluczowych sekcji | treść (LLM per strona) |  | `navigation_to_key_sections_clear` |
| 2 | Powiązane kategorie z linkami | treść (LLM per strona) |  | `related_categories_linked` |
| 2 | Widoczne linki do podkategorii | treść (LLM per strona) |  | `subcategory_links_exposed` |
| 2 | Scenariusze użycia / profil klienta | treść (LLM per strona) |  | `use_cases_or_target_customer_defined` |
| 2 | Wartości i realne wyróżniki | treść (LLM per strona) |  | `values_or_real_differentiators` |
| 0.5 | Filtry / fasety (jeśli zasadne) | treść (LLM per strona) |  | `filters_or_facets_if_applicable` |
| 0.5 | Widoczne godziny otwarcia | treść (LLM per strona) |  | `opening_hours_visible` |

### E-E-A-T — waga grupy 25/100, czynników 26

| Impact | Czynnik | Zakres | Kara | id |
|---|---|---|---|---|
| 3 | Bio autora z imieniem i kwalifikacjami | treść (LLM per strona) |  | `author_bio_with_name_and_credentials` |
| 3 | Konkret zamiast waty | treść (LLM per strona) |  | `content_substance_over_fluff` |
| 3 | Certyfikaty i kwalifikacje | treść (LLM per strona) |  | `credentials_certifications_or_qualifications` |
| 3 | Wyróżnienie się od konkurencji | treść (LLM per strona) |  | `differentiation_vs_competition` |
| 3 | Cytowania autorytatywnych źródeł z linkami | treść (LLM per strona) |  | `external_authoritative_citations_with_links` |
| 3 | Dowody zewnętrzne (social media, prasa, nagrody) | treść (LLM per strona) |  | `external_proof_social_press_awards` |
| 3 | Zewnętrzne źródła / dowody gdzie zasadne | treść (LLM per strona) |  | `external_sources_or_proof_where_relevant` |
| 3 | Walidacja zewnętrzna (nagrody, partnerzy, media) | treść (LLM per strona) |  | `external_validation_awards_partners_media` |
| 3 | Doświadczenie z pierwszej ręki lub własne dane | treść (LLM per strona) |  | `firsthand_experience_or_original_data` |
| 3 | Profile założycieli/zespołu z imionami | treść (LLM per strona) |  | `founder_or_team_profiles_with_names` |
| 3 | Sensowny wstęp kategorii (nie thin content) | treść (LLM per strona) |  | `meaningful_category_intro_copy_not_thin` |
| 3 | Wiele kanałów kontaktu | treść (LLM per strona) |  | `multiple_contact_channels` |
| 3 | Pełny i widoczny NAP (nazwa/adres/telefon) | treść (LLM per strona) |  | `nap_name_address_phone_complete_and_visible` |
| 3 | Brak powielanego szablonowego contentu | treść (LLM per strona) |  | `no_boilerplate_content_duplicated` |
| 3 | Brak generycznego AI contentu | treść (LLM per strona) |  | `no_generic_ai_generated_content` |
| 3 | Brak ogólnikowej marketingowej waty | treść (LLM per strona) |  | `no_generic_marketing_fluff` |
| 3 | Lokalizacja biura / fizyczna obecność | treść (LLM per strona) |  | `office_location_or_physical_presence` |
| 3 | Jasno określona tożsamość firmy | treść (LLM per strona) |  | `organization_entity_clearly_stated` |
| 3 | Zdjęcie biura / dowód fizycznej obecności | treść (LLM per strona) |  | `physical_office_photo_or_proof` |
| 3 | Informacja o czasie odpowiedzi | treść (LLM per strona) |  | `response_time_expectation` |
| 3 | Ograniczenie ryzyka (gwarancja/test/przejrzysty proces) | treść (LLM per strona) |  | `risk_reversal_guarantee_trial_or_process_clarity` |
| 3 | Dowody społeczne (opinie, klienci, case study) | treść (LLM per strona) |  | `social_proof_testimonials_clients_case_studies` |
| 3 | Sygnały zaufania (logotypy klientów, opinie, liczby) | treść (LLM per strona) |  | `trust_signals_logos_reviews_numbers` |
| 3 | Unikalny H1 i title kategorii | treść (LLM per strona) |  | `unique_category_h1_and_title` |
| 3 | Unikalny punkt widzenia (nie powielanie cudzego) | treść (LLM per strona) |  | `unique_pov_not_generic_rehash` |
| 3 | Widoczna wartość dla użytkownika | treść (LLM per strona) |  | `value_for_user_evident` |

### Patenty Google — waga grupy 15/100, czynników 26

| Impact | Czynnik | Zakres | Kara | id |
|---|---|---|---|---|
| 3 | Content-Data Alignment Score | treść (LLM per strona) |  | `content-data-alignment-score` |
| 3 | Cross-Document Factual Consistency | treść (LLM per strona) |  | `cross-document-factual-consistency` |
| 3 | Entity Coverage Depth | treść (LLM per strona) |  | `entity-coverage-depth` |
| 3 | Entity Disambiguation Strength | treść (LLM per strona) |  | `entity-disambiguation-strength` |
| 3 | Entity Group Rarity | treść (LLM per strona) |  | `entity-group-rarity` |
| 3 | Headline Summary Fit | treść (LLM per strona) |  | `headline-summary-fit` |
| 3 | How-To Step Consensus | treść (LLM per strona) |  | `how-to-step-consensus` |
| 3 | Multi-Source Consensus | treść (LLM per strona) |  | `multi-source-consensus` |
| 3 | Opinion and Subjectivity Detection | treść (LLM per strona) |  | `opinion-subjectivity-detection` |
| 3 | Query Intent Classification Alignment | treść (LLM per strona) |  | `query-intent-classification-alignment` |
| 3 | Query-Specific Selection Share | treść (LLM per strona) |  | `query-specific-selection-share` |
| 3 | Site Engagement Duration | treść (LLM per strona) |  | `site-engagement-duration` |
| 3 | Source Confidence Score | treść (LLM per strona) |  | `source-confidence-score` |
| 2 | Brand Mentions as Authority Proxy | treść (LLM per strona) |  | `brand-mentions-authority-proxy` |
| 2 | Branded Search Topic Affinity | treść (LLM per strona) |  | `branded-search-topic-affinity` |
| 2 | Citable Fragment Density | treść (LLM per strona) |  | `citable-fragment-density` |
| 2 | Citation Quality and Source Verifiability | treść (LLM per strona) |  | `citation-quality-source-verifiability` |
| 2 | Content-Entity Alignment | treść (LLM per strona) |  | `content-entity-alignment` |
| 2 | Entity Salience | treść (LLM per strona) |  | `entity-salience` |
| 2 | Human-Likeness Score | treść (LLM per strona) |  | `human-likeness-score` |
| 2 | Non-Syntheticity Index | treść (LLM per strona) |  | `non-syntheticity-index` |
| 2 | Position-Normalized CTR | treść (LLM per strona) |  | `position-normalized-ctr` |
| 2 | Query Embedding Source Match | treść (LLM per strona) |  | `query-embedding-source-match` |
| 2 | Semantic Coherence Score | treść (LLM per strona) |  | `semantic-coherence-score` |
| 2 | Source Authority for Entity/Topic | treść (LLM per strona) |  | `source-authority-for-entity-topic` |
| 2 | Verified Entity Status | treść (LLM per strona) |  | `verified-entity-status` |

### AI / AEO — waga grupy 20/100, czynników 21

| Impact | Czynnik | Zakres | Kara | id |
|---|---|---|---|---|
| 3 | Bezpośrednia odpowiedź na początku treści | treść (LLM per strona) |  | `direct_answer_near_content_start` |
| 2 | Meta description dedykowana kategorii | treść (LLM per strona) |  | `category_specific_meta_description` |
| 2 | Jasny następny krok / CTA | treść (LLM per strona) |  | `clear_next_step_or_cta` |
| 2 | Wyraźne CTA do kontaktu/zakupu | treść (LLM per strona) |  | `clear_primary_cta_to_contact_or_buy` |
| 2 | Jasna propozycja wartości w pierwszym ekranie | treść (LLM per strona) |  | `clear_value_proposition_above_fold` |
| 2 | Dostęp do kontaktu ze strony głównej | treść (LLM per strona) |  | `contact_info_accessible_from_home` |
| 2 | Ścieżka do kontaktu z O nas | treść (LLM per strona) |  | `contact_pathway_from_about` |
| 2 | Sekcja FAQ odpowiadająca na obiekcje | treść (LLM per strona) |  | `faq_section_addressing_objections` |
| 2 | Linki do LinkedIn / profili zawodowych | treść (LLM per strona) |  | `links_to_linkedin_or_professional_profiles` |
| 2 | Opisowa i unikalna meta description | treść (LLM per strona) |  | `meta_description_descriptive_and_unique` |
| 2 | Cena lub przedział cenowy | treść (LLM per strona) |  | `pricing_or_price_range_indication` |
| 2 | Widoczne główne wezwanie do działania | treść (LLM per strona) |  | `primary_cta_visible` |
| 2 | Widoczna data publikacji | treść (LLM per strona) |  | `publication_date_visible_inline` |
| 2 | Prawdziwe zdjęcia (nie stockowe) | treść (LLM per strona) |  | `real_photos_not_stock_implied` |
| 2 | Skanowalna struktura (nagłówki, listy, tabele) | treść (LLM per strona) |  | `scannable_structure_headings_lists_tables` |
| 2 | Skanowalna struktura (listy / podtytuły) | treść (LLM per strona) |  | `scannable_structure_lists_or_subheadings` |
| 2 | Wizualna hierarchia ułatwiająca skanowanie | treść (LLM per strona) |  | `visual_hierarchy_for_scannability` |
| 0.5 | Klikalny e-mail | treść (LLM per strona) |  | `email_clickable_mailto` |
| 0.5 | Mapa lub osadzona lokalizacja | treść (LLM per strona) |  | `map_or_embedded_location` |
| 0.5 | Paginacja lub 'załaduj więcej' z sensem | treść (LLM per strona) |  | `pagination_or_load_more_sensible` |
| 0.5 | Klikalny numer telefonu | treść (LLM per strona) |  | `phone_clickable_tel_link` |

## Diagnoza inflacji wyników (przed korektą 2026-07-03)

1. **Fan-out nie wchodził do wyniku głównego.** Pokrycie realnych pytań użytkowników AI (najtwardszy sygnał, zwykle 40–70) było liczone, ale trafiało wyłącznie do nieużywanej formuły legacy. Dashboard widział tylko wyniki grup czynników.
2. **Kary za krytyczne braki nie działały.** `CRITICAL_FACTOR_PENALTIES` odejmowane były od `legacy_overall`, nie od wyniku na dashboardzie.
3. **Podstawy miały najwyższą wagę.** Czynniki domenowe (sitemap, robots, HTTPS, niezablokowane boty) dostawały impact 3 — samo posiadanie sitemapy podnosiło wynik tak samo jak wzorcowe E-E-A-T.
4. **LLM oceniał zbyt hojnie.** Na raporcie flagowym 68/71 obserwacji technicznych i 12/12 AI/AEO miało maksymalne 2/2. Brak kalibracji surowości w promptach.
5. **Ocena częściowa zbyt opłacalna.** score=1 dawał 0.35 wartości.

## Wprowadzone korekty

- Wynik główny = **0.85 × ważone grupy + 0.15 × pokrycie fan-out − kary krytyczne** (`FAN_OUT_BLEND_WEIGHT`).
- Kary krytyczne realnie odejmowane od wyniku głównego (max −49).
- Podstawy zdemotowane w `LOW_IMPACT_FACTORS` (impact 0.25–1.0): sitemap, robots, HTTPS, HSTS, kompresja, crawl-delay, hreflang, canonical, H1, meta description, boty AI. `llms.txt` zostaje wyróżnikiem (impact 3).
- Kalibracja surowości w promptach oceny czynników: "2" tylko z twardym dowodem; typowa poprawna strona = większość ocen "1".
- `SCORE_VALUE_MAP`: ocena częściowa 0.35 → **0.30**.

## Symulacje nowego modelu

| Profil domeny | Grupy (t/p/o/e/pat/ai) | Fan-out | Kary | Wynik |
|---|---|---|---|---|
| Flagowa (strategiczni, stary wynik 91) | 92/100/89/76/91/100 | 67 | 0 | **87** |
| Dobra, zadbana | 85/80/75/65/75/80 | 65 | −8 (brak llms.txt) | **66** |
| Typowa przeciętna | 70/55/60/45/55/60 | 50 | −8 | **48** |
| Słaba (blokuje boty AI) | 45/40/50/35/45/45 | 35 | −27 | **14** |

Interpretacja zgodna z legendą w UI: **90+** zoptymalizowane (rzadkie), **75–89** wynik poprawny, **<75** wymaga optymalizacji (większość rynku).

## Co dalej / uwagi

- Wyniki sprzed korekty (zapisane raporty, fixed report strategiczni.pl = 91) są nieporównywalne z nowymi — przy udostępnianiu świeżych audytów obok starych zaznaczaj wersję modelu.
- Kalibracja promptu (pkt 4) wymaga walidacji na 2–3 realnych audytach po deployu — jeśli LLM dalej sypie dwójkami, następny krok to wymuszenie limitu dwójek w prompcie lub programowy "curve".
- `fixed_reports/strategiczni.pl.json` warto przeliczyć generatorem, żeby flagowy wynik nie odstawał od zaostrzonej skali.
