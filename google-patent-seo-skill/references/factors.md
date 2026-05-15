# Baza wiedzy SEO z patentów Google

Ta baza jest evidence-first: patent opisuje mechanizm lub metodę, a nie automatycznie potwierdzony aktualny czynnik rankingowy. Dlatego każdy rekord ma `seo_inference_level`, `confidence` oraz powiązane `evidence_ids`.

## Czynniki

### Sygnały behawioralne

#### `position-normalized-ctr` - Position-Normalized CTR

**Definicja:** CTR skorygowany o oczekiwaną klikalność danej pozycji w SERP.

**Mechanizm:** Porównanie rzeczywistego CTR z bazowym CTR pozycji pozwala ocenić, czy wynik jest wybierany częściej lub rzadziej niż oczekiwano.

**Jak zaspokoić:** Pisz title i description jak obietnicę odpowiedzi: konkretnie, odróżniająco i zgodnie z intencją, bez clickbaitowej niespójności.

**Przykład:** Dla pozycji 4 tytuł 'Checklist migracji SEO: 42 testy przed zmianą domeny' może wygrać z generycznym 'Migracja SEO - poradnik'.

**Confidence:** `medium`; **inferencja:** `moderate`

**Evidence:** `US8788477B1-text-abstract`

#### `query-specific-selection-share` - Query-Specific Selection Share

**Definicja:** Udział wyborów danego URL dla konkretnego zapytania po obejrzeniu SERP.

**Mechanizm:** System może uczyć się preferencji użytkowników na poziomie query, niezależnie od globalnej popularności strony.

**Jak zaspokoić:** Dopasuj snippet do mikrointencji query; testuj różne tytuły dla zapytań, w których masz wysokie impressions i słaby CTR.

**Przykład:** Dla query 'audyt schema product' tytuł powinien obiecywać audyt schema Product, nie ogólny poradnik structured data.

**Confidence:** `high`; **inferencja:** `direct`

**Evidence:** `US8788477B1-text-abstract`

#### `site-engagement-duration` - Site Engagement Duration

**Definicja:** Zagregowany czas lub jakość zaangażowania użytkowników z witryną, interpretowana jako długoterminowy sygnał satysfakcji.

**Mechanizm:** Warianty systemów rankingowych mogą wykorzystywać agregaty zachowania dla witryny, ale lokalny patent wymaga uzupełnienia przed mocnym wnioskiem.

**Jak zaspokoić:** Zadbaj o szybkie dojście do odpowiedzi, logiczne następne kroki, linkowanie wewnętrzne i elementy, które realnie pomagają kontynuować zadanie.

**Przykład:** Po definicji dodaj diagnostykę, przykłady i checklistę wdrożenia, zamiast kończyć tekst po ogólnym wstępie.

**Confidence:** `high`; **inferencja:** `direct`

**Evidence:** `US9195944B1-text-abstract`

### Jakość i struktura treści

#### `cross-document-factual-consistency` - Cross-Document Factual Consistency

**Definicja:** Stopień, w jakim kluczowe twierdzenia dokumentu są zgodne z niezależnymi, wiarygodnymi źródłami.

**Mechanizm:** System porównuje twierdzenia z innymi dokumentami i może wykrywać potwierdzenia, sprzeczności oraz liczbę niezależnych źródeł wspierających fakt.

**Jak zaspokoić:** Dla danych liczbowych, definicji i mocnych claimów dodawaj kilka niezależnych źródeł pierwotnych oraz unikaj niespójnych wartości między sekcjami.

**Przykład:** Claim o udziale kanału organicznego podeprzyj danymi GSC, raportem branżowym i dokumentacją platformy, zamiast jednym blogpostem agregującym.

**Confidence:** `high`; **inferencja:** `direct`

**Evidence:** `US8954412B1-text-abstract`, `US8954412B1-text-claims`, `US9619450B2-text-abstract`

#### `headline-summary-fit` - Headline Summary Fit

**Definicja:** Zgodność nagłówka z najważniejszą treścią dokumentu oraz zdolność nagłówka do streszczenia relewantnego sensu.

**Mechanizm:** Automatyczne generowanie nagłówków wymaga identyfikacji istotnych fragmentów i skrócenia ich bez utraty sensu.

**Jak zaspokoić:** Tytuł i H1 powinny precyzyjnie streszczać główną odpowiedź, a nie tylko zawierać keyword.

**Przykład:** Lepsze: 'Canonical nie rozwiązuje duplikacji, gdy Google wybiera inny URL' niż 'Canonical SEO - poradnik'.

**Confidence:** `high`; **inferencja:** `direct`

**Evidence:** `US9619450B2-text-abstract`, `US9619450B2-text-claims`

#### `how-to-step-consensus` - How-To Step Consensus

**Definicja:** Zgodność i kompletność kroków instrukcji względem wielu źródeł oraz intencji użytkownika how-to.

**Mechanizm:** System może identyfikować zapytania how-to, grupować kroki z wielu źródeł i prezentować zestaw kroków odpowiadający zadaniu.

**Jak zaspokoić:** Twórz kroki atomowe, w prawidłowej kolejności, z warunkami wejścia/wyjścia i źródłem, jeśli krok wynika z dokumentacji.

**Przykład:** Poradnik 'jak zmienić domenę bez utraty SEO' powinien mieć pre-migration, redirect map, staging, launch, monitoring i rollback.

**Confidence:** `high`; **inferencja:** `direct`

**Evidence:** `US10585927B1-text-abstract`

#### `information-gain-score` - Information Gain Score

**Definicja:** Miara tego, czy dokument wnosi nowe, relewantne atrybuty lub fakty ponad to, co jest już obecne w konkurencyjnych dokumentach dla tego samego zapytania.

**Mechanizm:** LLM lub system audytu porównuje pokrycie encji, atrybutów i twierdzeń z topowymi dokumentami, a następnie premiuje treści dodające brakujące, użyteczne informacje.

**Jak zaspokoić:** Przed pisaniem zbuduj macierz top 10: encje, atrybuty, pytania, dane i przykłady. Dodaj 3-5 unikalnych punktów, które są prawdziwe, istotne i łatwe do zacytowania.

**Przykład:** W artykule o indeksowaniu JS dodaj porównanie renderowania CSR, SSR i hydration errors z logami z GSC, jeśli konkurencja omawia tylko ogólne crawlowanie.

**Confidence:** `low`; **inferencja:** `moderate`

**Evidence:** brak lokalnego dowodu

#### `opinion-subjectivity-detection` - Opinion and Subjectivity Detection

**Definicja:** Rozróżnienie faktów, opinii i ocen subiektywnych w dokumencie.

**Mechanizm:** System ML może identyfikować opinie w dokumentach, co pomaga oddzielać twierdzenia wymagające dowodu od komentarza eksperckiego.

**Jak zaspokoić:** Oznaczaj opinie jako opinie, fakty jako fakty, a rekomendacje uzasadniaj danymi lub doświadczeniem autora.

**Przykład:** Napisz 'uważam, że ten test jest lepszy dla małych sklepów, bo...' zamiast przedstawiać preferencję jako uniwersalny fakt.

**Confidence:** `high`; **inferencja:** `direct`

**Evidence:** `US10832001B2-text-abstract`

#### `query-intent-classification-alignment` - Query Intent Classification Alignment

**Definicja:** Dopasowanie formatu, zakresu i głębokości treści do sklasyfikowanej intencji zapytania.

**Mechanizm:** Patent LLM intent classification wskazuje, że zapytania można klasyfikować modelami językowymi, a odpowiedź powinna pasować do rozpoznanej intencji.

**Jak zaspokoić:** Przed pisaniem oznacz intencję jako informacyjną, porównawczą, transakcyjną, lokalną, how-to lub troubleshooting i dobierz układ treści.

**Przykład:** Query 'best log file analyzer for SEO' wymaga porównania narzędzi i kryteriów wyboru, a nie definicji log files.

**Confidence:** `high`; **inferencja:** `direct`

**Evidence:** `US20240135187A1-text-abstract`, `US20240135187A1-fig-p003`

#### `semantic-coherence-score` - Semantic Coherence Score

**Definicja:** Spójność znaczeniowa między zapytaniem, intencją, nagłówkami, akapitami, terminologią i odpowiedzią końcową.

**Mechanizm:** Modele językowe i klasyfikatory intencji mogą oceniać, czy kolejne fragmenty tekstu zachowują jeden temat, konsekwentną terminologię i logiczny ciąg argumentacji.

**Jak zaspokoić:** Używaj jednego słownika pojęć, porządkuj nagłówki według intencji użytkownika i usuwaj dygresje, które nie wspierają odpowiedzi.

**Przykład:** Poradnik o migracji SEO powinien trzymać się kroków migracji, a nie mieszać ich z ogólną sprzedażą audytów SEO.

**Confidence:** `medium`; **inferencja:** `moderate`

**Evidence:** `US20240012999A1-text-abstract`, `US20220067309A1-text-abstract`, `US20240135187A1-text-abstract`

#### `task-priority-response-fit` - Task Priority Response Fit

**Definicja:** Dopasowanie kolejności odpowiedzi do priorytetu zadań użytkownika, ryzyka i pilności.

**Mechanizm:** Systemy automatycznej priorytetyzacji zadań pokazują, że kolejność elementów może wynikać z ważności, nie tylko chronologii.

**Jak zaspokoić:** Dla troubleshooting zaczynaj od najbardziej prawdopodobnych i krytycznych diagnoz, a dopiero później przechodź do rzadkich wariantów.

**Przykład:** W poradniku o spadku indeksacji najpierw sprawdź robots/noindex/statusy, potem canonicale, renderowanie i jakość treści.

**Confidence:** `low`; **inferencja:** `speculative`

**Evidence:** `US6961720B1-text-abstract`, `US6961720B1-text-claims`

### Encje i graf wiedzy

#### `content-entity-alignment` - Content-Entity Alignment

**Definicja:** Dopasowanie treści, nagłówków i przykładów do encji, dla których dokument ma budować rozpoznawalność i autorytet.

**Mechanizm:** Rozpoznanie encji i ich użycia w treści pozwala ocenić, czy dokument faktycznie odpowiada na temat przypisany do encji.

**Jak zaspokoić:** Zdefiniuj główne encje przed pisaniem i usuń sekcje, które nie wspierają ich relacji, atrybutów lub pytań użytkownika.

**Przykład:** Strona autora SEO powinna łączyć osobę z publikacjami, konferencjami, case studies i tematami, w których ma być rozpoznawana.

**Confidence:** `medium`; **inferencja:** `moderate`

**Evidence:** `US10102187B2-text-abstract`, `US20190034530A1-text-abstract`

#### `entity-coverage-depth` - Entity Coverage Depth

**Definicja:** Głębokość pokrycia encji istotnych dla tematu, czyli liczba i jakość unikalnych encji dziedzinowych obsłużonych w dokumencie.

**Mechanizm:** System może wykorzystywać rozpoznane encje i ich częstotliwość w korpusie do rankingu lub scoringu dokumentów względem zainteresowań i tematu.

**Jak zaspokoić:** Zbuduj mapę encji dla klastra tematycznego i upewnij się, że tekst pokrywa encje główne, podrzędne, narzędzia, metody, metryki i znane byty.

**Przykład:** Tekst o topical authority powinien obejmować m.in. Knowledge Graph, entity salience, PageRank, anchor context, topical clusters i internal linking.

**Confidence:** `high`; **inferencja:** `direct`

**Evidence:** `US9679018B1-text-abstract`, `US9679018B1-text-summary`, `US9679018B1-fig-p003`

#### `entity-disambiguation-strength` - Entity Disambiguation Strength

**Definicja:** Siła sygnałów pozwalających odróżnić właściwą encję od encji o podobnej nazwie.

**Mechanizm:** Atrybuty o wysokiej entropii i powiązane encje pomagają normalizować obiekty oraz zmniejszać ryzyko błędnego przypisania.

**Jak zaspokoić:** Dodawaj unikalne identyfikatory: lokalizacja, rola, branża, produkt, daty, profile, identyfikatory prawne i powiązane encje.

**Przykład:** Dla autora 'Jan Kowalski' podaj firmę, specjalizację, URL profilu, publikacje i schema sameAs, zamiast samego imienia i nazwiska.

**Confidence:** `high`; **inferencja:** `direct`

**Evidence:** `US8244689B2-text-abstract`, `US12197525B2-text-abstract`

#### `entity-group-rarity` - Entity Group Rarity

**Definicja:** Waga rzadkich lub specjalistycznych grup encji, które odróżniają dokument od ogólnego omówienia tematu.

**Mechanizm:** Rzadkość encji lub grupy encji w korpusie działa podobnie do sygnału IDF: mniej powszechne, ale tematycznie relewantne grupy mogą lepiej sygnalizować specjalizację.

**Jak zaspokoić:** Dodawaj specjalistyczne encje i ich relacje tylko wtedy, gdy realnie wspierają intencję; nie upychaj nazw bez kontekstu.

**Przykład:** W treści o patentach SEO użycie Reasonable Surfer, Hilltop, NavBoost i entity frequency jest silniejsze niż samo powtarzanie 'ranking Google'.

**Confidence:** `high`; **inferencja:** `direct`

**Evidence:** `US9679018B1-text-summary`, `US8244689B2-text-abstract`

#### `entity-salience` - Entity Salience

**Definicja:** Centralność encji w dokumencie: czy encja jest głównym tematem, czy tylko poboczną wzmianką.

**Mechanizm:** Systemy selekcji i prezentacji treści mogą wykorzystywać relację między treścią a encjami, analizując pozycję, częstość, współwystępowanie i kontekst encji.

**Jak zaspokoić:** Umieść główną encję w title, H1, wstępie i sekcjach decyzyjnych; pokazuj jej relacje z encjami sąsiednimi.

**Przykład:** Artykuł o 'schema Product' powinien omawiać Product, Offer, AggregateRating i Merchant Center jako rdzeń, nie jako poboczną listę.

**Confidence:** `medium`; **inferencja:** `moderate`

**Evidence:** `US20190034530A1-text-abstract`, `US10102187B2-text-abstract`

#### `verified-entity-status` - Verified Entity Status

**Definicja:** Stopień, w jakim osoba, organizacja, produkt lub źródło jest jednoznacznie identyfikowalne i potwierdzone przez zewnętrzne profile lub dane.

**Mechanizm:** Prezentowanie graficznych wyników i powiązań encji wymaga rozpoznania, do jakiej encji odnosi się treść oraz czy istnieją wiarygodne powiązania.

**Jak zaspokoić:** Dodawaj strony autorów, Organization/Person schema, sameAs, spójny NAP, profile branżowe i historię publikacji powiązaną z tematem.

**Przykład:** Ekspert medyczny ma stronę autora z numerem prawa wykonywania zawodu, publikacjami, ORCID i tym samym profilem w schema Person.

**Confidence:** `medium`; **inferencja:** `moderate`

**Evidence:** `US12197525B2-text-abstract`, `US12197525B2-fig-p003`

### AI, cytowalność i retrieval

#### `citable-fragment-density` - Citable Fragment Density

**Definicja:** Gęstość krótkich, samodzielnych fragmentów, które można bezpiecznie zacytować w odpowiedzi generatywnej lub featured answer.

**Mechanizm:** Systemy odpowiedzi generatywnych z cytowaniami preferują fragmenty, które są konkretne, źródłowe, jednoznaczne i dobrze osadzone w dokumencie.

**Jak zaspokoić:** Każdy H2/H3 zaczynaj od jednozdaniowej odpowiedzi, a statystyki zapisuj z atrybucją inline i źródłem pierwotnym.

**Przykład:** Sekcja 'Co to jest crawl budget?' zaczyna się definicją w 25 słowach, potem dopiero rozwija wyjątki i przykłady.

**Confidence:** `medium`; **inferencja:** `moderate`

**Evidence:** `US20250217626A1-text-abstract`, `US20250103640A1-text-abstract`

#### `human-likeness-score` - Human-Likeness Score

**Definicja:** Ocena, czy tekst generowany brzmi i zachowuje się jak naturalny tekst człowieka, z konkretem, zmiennością i osadzeniem w doświadczeniu.

**Mechanizm:** Modele oceny jakości NLG mogą porównywać wygenerowane wyjścia z cechami tekstu naturalnego, spójnością, pokryciem danych i jakością językową.

**Jak zaspokoić:** Dodawaj własne dane, konkretne nazwy narzędzi, daty, przypadki, wnioski autora i naturalną zmienność zdań; unikaj sterylnej symetrii LLM.

**Przykład:** Zamiast 'warto monitorować wyniki', napisz, jakie alerty ustawiono w GSC, kiedy i jaki błąd wykryły.

**Confidence:** `medium`; **inferencja:** `moderate`

**Evidence:** `US12073189B2-text-abstract`, `US20240012999A1-text-abstract`, `US12223273B2-text-abstract`

#### `non-syntheticity-index` - Non-Syntheticity Index

**Definicja:** Praktyczna ocena, czy tekst zawiera sygnały pochodzenia z realnej obserwacji, danych i procesu, a nie tylko syntetycznego uśrednienia.

**Mechanizm:** Ocena jakości generacji i porównania semantyczne mogą ujawniać teksty zbyt gładkie, zbyt ogólne lub słabo zakotwiczone w źródłach.

**Jak zaspokoić:** Wprowadzaj dane pierwotne, cytowalne obserwacje, konkretne ograniczenia, błędy i decyzje, których nie da się łatwo zgadnąć bez doświadczenia.

**Przykład:** W audycie contentu pokaż wynik crawl logów, liczbę URL-i dotkniętych problemem i decyzję, której alternatywy odrzucono.

**Confidence:** `medium`; **inferencja:** `moderate`

**Evidence:** `US12223273B2-text-abstract`, `US20220067309A1-text-abstract`

#### `query-embedding-source-match` - Query Embedding Source Match

**Definicja:** Dopasowanie źródłowych dokumentów do zapytania przez reprezentacje embeddingowe i semantyczne, nie tylko przez exact match.

**Mechanizm:** Systemy przetwarzania dokumentów mogą używać query embeddings do wyboru fragmentów i dokumentów w chmurze lub indeksie.

**Jak zaspokoić:** Pisz sekcje semantycznie pełne: synonimy, warianty zapytań, przykłady i terminy powiązane powinny naturalnie pokrywać przestrzeń intencji.

**Przykład:** Sekcja o 'renderowaniu JS' powinna obejmować hydration, CSR, SSR, dynamic rendering, crawl queue i indexing delay.

**Confidence:** `medium`; **inferencja:** `moderate`

**Evidence:** `US20250103826A1-text-abstract`

#### `source-authority-generative-context` - Source Authority for Generative Context

**Definicja:** Prawdopodobieństwo, że źródło zostanie uznane za wystarczająco wiarygodne do użycia w kontekście odpowiedzi generatywnej.

**Mechanizm:** Systemy LLM mogą uczyć się selektywnego przewidywania i dobierania kontekstu na podstawie jakości źródła, confidence i zgodności z pytaniem.

**Jak zaspokoić:** Łącz autorytet tematyczny, cytowalne fragmenty, aktualność, źródła pierwotne i dobrze opisane dane autora/wydawcy.

**Przykład:** Raport SEO z metodologią, tabelą danych i profilami autorów ma większą użyteczność jako źródło odpowiedzi niż krótki post opinii.

**Confidence:** `low`; **inferencja:** `speculative`

**Evidence:** `US20240428015A1-text-abstract`

### Autorytet źródła

#### `authoritative-content-exemption` - Authoritative Content Exemption

**Definicja:** Hipoteza, że treści o silnych zewnętrznych sygnałach autorytetu mogą być mniej podatne na krótkoterminowe słabe sygnały zachowania.

**Mechanizm:** Jeśli źródło jest silnie potwierdzone linkami, cytowaniami, wzmiankami i historią wyników, system może traktować chwilowe spadki inaczej niż przy słabym źródle.

**Jak zaspokoić:** Buduj autorytet tematyczny przez cytowania, profile autorów, dane pierwotne i stabilne klastry, zamiast polegać wyłącznie na optymalizacji snippetów.

**Przykład:** Raport branżowy cytowany przez media i linkowany przez dokumentacje może utrzymać widoczność mimo okresowo niższego CTR.

**Confidence:** `low`; **inferencja:** `speculative`

**Evidence:** brak lokalnego dowodu

#### `brand-mentions-authority-proxy` - Brand Mentions as Authority Proxy

**Definicja:** Wzmianki o marce, autorze lub źródle jako pomocniczy proxy autorytetu, szczególnie gdy współwystępują z encjami tematu.

**Mechanizm:** Systemy analizy opinii i endorsementów mogą wykrywać wzorce, w których marka jest wymieniana jako źródło, ekspert lub punkt odniesienia.

**Jak zaspokoić:** Twórz zasoby cytowalne: dane własne, benchmarki, definicje i narzędzia, które inni będą naturalnie wymieniać przy danym temacie.

**Przykład:** Raport 'SEO patents map' cytowany w newsletterach i podcastach buduje powiązanie autora z patent-based SEO.

**Confidence:** `medium`; **inferencja:** `moderate`

**Evidence:** `US10832001B2-text-abstract`, `US20130055089A1-text-abstract`

#### `branded-search-topic-affinity` - Branded Search Topic Affinity

**Definicja:** Współwystępowanie marki lub wydawcy z terminami tematycznymi w zapytaniach, treściach i wzmiankach.

**Mechanizm:** Wzorce endorsement, opinii i wzmianek mogą łączyć wydawcę lub markę z określonymi tematami i encjami.

**Jak zaspokoić:** Publikuj rozpoznawalne serie, raporty, narzędzia i case studies, które generują zapytania brand + topic oraz wzmianki bezlinkowe.

**Przykład:** Wzrost zapytań 'twoja marka schema audit' sygnalizuje powiązanie marki z konkretną usługą lub tematem.

**Confidence:** `medium`; **inferencja:** `moderate`

**Evidence:** `US20130055089A1-text-abstract`, `US10832001B2-text-abstract`

#### `citation-quality-source-verifiability` - Citation Quality and Source Verifiability

**Definicja:** Jakość cytowań rozumiana jako łatwość weryfikacji, źródło pierwotne, jasna atrybucja i przydatność cytatu dla odpowiedzi generatywnej.

**Mechanizm:** Systemy generatywne z cytowaniami mogą wybierać dokumenty i fragmenty, które dają się powiązać z konkretnym źródłem i potwierdzają odpowiedź.

**Jak zaspokoić:** Dodawaj inline attribution, link do źródła pierwotnego, konkretną liczbę lub fakt i krótkie zdanie możliwe do zacytowania.

**Przykład:** Zamiast 'badania pokazują', napisz: 'Według dokumentacji Google z marca 2025, parametr X wpływa na Y' i podaj URL.

**Confidence:** `medium`; **inferencja:** `moderate`

**Evidence:** `US20250103640A1-text-abstract`, `US20250217626A1-text-abstract`

#### `multi-source-consensus` - Multi-Source Consensus

**Definicja:** Wzmocnienie twierdzenia lub kroku przez zgodność wielu niezależnych, wiarygodnych źródeł.

**Mechanizm:** Gdy kilka źródeł niezależnie wskazuje ten sam fakt lub krok, system ma mocniejszą podstawę do użycia go w odpowiedzi lub rankingu informacji.

**Jak zaspokoić:** Dla kluczowych porad i statystyk pokaż zgodność co najmniej 2-3 niezależnych źródeł, zwłaszcza przy YMYL i decyzjach zakupowych.

**Przykład:** Rekomendację dotyczącą canonicali poprzyj dokumentacją Google, testem własnym i case study technicznym.

**Confidence:** `high`; **inferencja:** `direct`

**Evidence:** `US10585927B1-text-abstract`, `US8954412B1-text-abstract`

#### `source-authority-for-entity-topic` - Source Authority for Entity/Topic

**Definicja:** Autorytet źródła liczony tematycznie dla konkretnej encji lub obszaru, a nie jako ogólna moc domeny.

**Mechanizm:** Źródło może być oceniane przez historię publikacji, powiązania z encją, potwierdzenia i profil linków w danej niszy.

**Jak zaspokoić:** Buduj zwarte klastry tematyczne, linkowanie wewnętrzne i zewnętrzne cytowania wokół jednej dziedziny, zamiast rozpraszać publikacje po wielu niszach.

**Przykład:** Domena z 200 tekstami o technicznym SEO może być silniejsza dla crawl budget niż ogólny portal marketingowy o większym DR.

**Confidence:** `medium`; **inferencja:** `moderate`

**Evidence:** `US8244689B2-text-abstract`, `US20170093934A1-text-abstract`

#### `source-confidence-score` - Source Confidence Score

**Definicja:** Ocena zaufania do źródeł użytych do wyprowadzenia odpowiedzi, instrukcji lub zestawu kroków.

**Mechanizm:** System wybierający kroki dla zapytania how-to może agregować informacje z wielu źródeł i oceniać ich wiarygodność oraz zgodność.

**Jak zaspokoić:** Dla poradników opieraj kroki na źródłach pierwotnych, dokumentacji producentów, danych własnych i zgodnych instrukcjach z autorytatywnych miejsc.

**Przykład:** Instrukcja migracji GA4 cytuje dokumentację Google, checklistę wdrożeniową i realny case, a nie tylko komentarz z forum.

**Confidence:** `high`; **inferencja:** `direct`

**Evidence:** `US10585927B1-text-abstract`, `US10585927B1-fig-p003`

### Zgodność danych strukturalnych

#### `content-data-alignment-score` - Content-Data Alignment Score

**Definicja:** Zgodność tekstu wygenerowanego lub opublikowanego na stronie z danymi strukturalnymi, tabelami, feedami i faktami wejściowymi.

**Mechanizm:** Model ocenia, czy tekst zachowuje wierność wobec danych bazowych; rozjazdy obniżają wiarygodność generowanego opisu.

**Jak zaspokoić:** Synchronizuj liczby, ceny, daty, oceny i parametry między body, tabelami oraz schema.org. Każdy fakt możliwy do ustrukturyzowania powinien mieć tę samą wartość w danych.

**Przykład:** Jeśli opis produktu mówi o cenie 1200 zł, schema Product i feed merchant nie mogą wskazywać 1450 zł.

**Confidence:** `high`; **inferencja:** `direct`

**Evidence:** `US12073187B2-text-abstract`, `US12073187B2-text-claims`, `US12073187B2-fig-p003`

