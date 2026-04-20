import re

with open("main.py", "r", encoding="utf-8") as f:
    content = f.read()

client_explanations = """
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
    "hreflang_used": "Podajemy w kodzie do AI i Google języki, w których dostępna jest nasza firma."
}
"""

if "CLIENT_FACTOR_EXPLANATIONS =" not in content:
    content = content.replace("DOMAIN_TECH_META = {", client_explanations + "\nDOMAIN_TECH_META = {")

# also modify translate_for_client_mode call assignment
rep_1 = """        try:
            client_mode = translate_for_client_mode(page_audits, synth, scores_obj, fan_out, homepage_title)
        except Exception as e:
            client_mode = {"client_verdict": f"Tłumaczenie nieudane: {e}", "client_recommendations": [], "client_content_gaps": [], "client_next_step": ""}"""

rep_2 = """        try:
            client_mode = translate_for_client_mode(page_audits, synth, scores_obj, fan_out, homepage_title)
            client_mode["client_factor_explanations"] = CLIENT_FACTOR_EXPLANATIONS
        except Exception as e:
            client_mode = {"client_verdict": f"Tłumaczenie nieudane: {e}", "client_recommendations": [], "client_content_gaps": [], "client_next_step": "", "client_factor_explanations": CLIENT_FACTOR_EXPLANATIONS}"""

content = content.replace(rep_1, rep_2)

with open("main.py", "w", encoding="utf-8") as f:
    f.write(content)

