# Workflow tworzenia treści

1. Zdefiniuj query, intencję, główną encję i format odpowiedzi.
2. Wybierz z `factors.jsonl` rekordy z kategoriami `content_quality`, `entity_graph`, `structured_data_alignment` i `generative_ai`.
3. Zbuduj mapę top 10: encje, atrybuty, źródła, brakujące informacje i cytowalne fragmenty.
4. Zaprojektuj outline tak, aby każdy H2/H3 odpowiadał na konkretną część intencji i zawierał topic sentence.
5. Dla każdego mocnego claimu dodaj źródło pierwotne oraz wskaż, czy claim jest faktem, opinią czy rekomendacją.
6. Po szkicu wykonaj pass: entity coverage, factual consistency, citable fragments, schema alignment i human-likeness.

Minimalny wynik briefu: lista użytych `factor_id`, ryzyka confidence oraz konkretne zalecenia sekcja po sekcji.
