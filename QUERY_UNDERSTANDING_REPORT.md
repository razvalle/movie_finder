# Query Understanding Change Report

## Root causes and fixes

1. **Title and intent collisions:** Title lookup was not a single normalized, data-derived index, so filter parsing could consume title words and aliases. The app now indexes display/original/alternate/subtitle titles, detects title spans before filter parsing, guards short/stopword/digit titles, derives risky words from catalog vocabularies, and keeps title-first exact matches. Embedded title readings are offered as removable chips. The alternative chips are reattached after result rendering rebuilds the ordinary chip list.
2. **“Movies like X”:** The previous query path did not reliably resolve a source title or rank from its catalog metadata. Source titles now resolve through the title index, fuzzy suggestions are returned for unresolved sources, TF-IDF uses synopsis/genres/themes/keywords, and the source title is excluded. Series members are shown separately.
3. **Negation scope:** Cues and degree words were incomplete and largely implicit in code. Rules now live in `data/query_rules.json`; hard and postposed scopes, Tagalog/Taglish cues, “-free”/“non-” forms, soft exclusions, and unclear-scope chips are handled explicitly.
4. **Conflicts:** Synonyms were not canonicalized when comparing included and excluded concepts, and incompatible range bounds could be lost. Conflicts now retain both bounds, identify synonym conflicts, explain that exclusions win, and expose removable chips.
5. **Runtime and year parsing:** Runtime notation and range forms were limited; vague durations were not catalog-relative. Runtime parsing now handles the requested notations and mixed-unit ranges, and vague terms use catalog percentiles displayed as actual limits. A bare “from YYYY” keeps the existing exact-year meaning; “after YYYY” remains an exclusive lower bound.
6. **Ranking and editable interpretations:** Superlatives now use a Bayesian-adjusted rating with a configurable vote minimum. Audience/occasion and country/demonym mappings are editable; audience interpretations are explicitly hand-written.
7. **Missing entity metadata:** Entity requests now use indexes where data exists and coverage notices where it does not, rather than silently treating a person’s name as free text. Country normalization now handles punctuation and repeated whitespace in official labels.

## Files and relevant line locations

- [app.py](./app.py#L491): synonym-aware filter conflicts and Bayesian rating order (`:491-555`); entity indexes/coverage (`:618-673`); title lookup/exactness (`:702-1084`); removable preference tags (`:1248-1381`); title-first route, similar-title handling, and alternatives (`:1546-1738`); unsupported-field notices and filtering (`:1910-1965`, `:2498-2522`).
- [nlp/catalog_matching.py](./nlp/catalog_matching.py#L69): title aliases/index/spans/risk vocabulary (`:69-198`), similar-title resolution (`:203-237`), and series grouping (`:272-`).
- [nlp/query.py](./nlp/query.py#L79): normalized country labels and matching (`:79-129`); superlative, audience, and entity parsing (`:130-324`).
- [nlp/negation.py](./nlp/negation.py#L29): data-configured cues, scope rules, and soft scopes (`:29-150`); [nlp/extract.py](./nlp/extract.py#L67): soft/unclear exclusions and runtime delegation (`:67-198`); [nlp/normalize.py](./nlp/normalize.py#L56): hyphenated negation normalization.
- [nlp/runtime.py](./nlp/runtime.py#L78): percentile bounds and expanded runtime parser (`:78-`); [nlp/release_year.py](./nlp/release_year.py#L21): exact years and preserved contradictory bounds.
- [nlp/scoring.py](./nlp/scoring.py#L241): soft-exclusion ranking; [nlp/smart_search.py](./nlp/smart_search.py#L954) and [nlp/tfidf.py](./nlp/tfidf.py#L73): similar-title retrieval and metadata document text.
- [data/query_rules.json](./data/query_rules.json#L2): negation and hedge rules; audience/country mappings (`:19-110`); percentile and Bayesian-vote settings (`:111-117`).
- [templates/index.html](./templates/index.html#L172): conflict messages and removable chips; title suggestions and series group (`:297-311`); preference-chip rendering (`:214-240`).
- [README.md](./README.md#L139): editable rules and hand-written audience mapping documentation.
- Tests: [tests/query_understanding_sweep.py](./tests/query_understanding_sweep.py#L77), [tests/test_query_understanding.py](./tests/test_query_understanding.py#L17), [tests/test_runtime.py](./tests/test_runtime.py), [tests/test_app_routes.py](./tests/test_app_routes.py), and [tests/test_search_compound_evidence.py](./tests/test_search_compound_evidence.py).

## Catalog-generated pass rates

The same deterministic 2,440 query cases were run against clean baseline `41a5447` and the updated worktree. Tuning and held-out are reported separately; `—` means that split contains no cases.

| Category | Tuning: before → after | Held-out: before → after |
|---|---:|---:|
| Audience/occasion | 0/6 (0%) → 6/6 (100%) | 0/3 (0%) → 3/3 (100%) |
| Country names in catalog | 34/36 (94.4%) → 36/36 (100%) | 8/8 (100%) → 8/8 (100%) |
| Digit titles | 71/71 (100%) → 71/71 (100%) | 15/15 (100%) → 15/15 (100%) |
| Guarded title contexts | 14/42 (33.3%) → 42/42 (100%) | 5/15 (33.3%) → 15/15 (100%) |
| “Like X” source resolution | 0/867 (0%) → 867/867 (100%) | 0/198 (0%) → 198/198 (100%) |
| Similarity excludes source | 0/1 (0%) → 1/1 (100%) | — → — |
| Fuzzy source suggestions | 0/1 (0%) → 1/1 (100%) | — → — |
| Negation scope/forms | 239/400 (59.8%) → 400/400 (100%) | 55/103 (53.4%) → 103/103 (100%) |
| Runtime notation | 3/7 (42.9%) → 7/7 (100%) | 0/2 (0%) → 2/2 (100%) |
| Runtime percentiles | 0/2 (0%) → 2/2 (100%) | 0/2 (0%) → 2/2 (100%) |
| Short/numeric title exact matches | 16/16 (100%) → 16/16 (100%) | 2/2 (100%) → 2/2 (100%) |
| Soft negation | 0/211 (0%) → 211/211 (100%) | 0/41 (0%) → 41/41 (100%) |
| Superlatives | 3/5 (60%) → 5/5 (100%) | 1/1 (100%) → 1/1 (100%) |
| Risky-title collisions | 313/313 (100%) → 313/313 (100%) | 71/71 (100%) → 71/71 (100%) |
| Unclear negation | 0/1 (0%) → 1/1 (100%) | — → — |
| **Overall** | **693/1,979 (35.0%) → 1,979/1,979 (100%)** | **157/461 (34.1%) → 461/461 (100%)** |

Overall: **850/2,440 (34.8%) before; 2,440/2,440 (100%) after.**

## Validation and known limits

- The final catalog sweep passed **2,440/2,440** cases. Country-name and focused query-understanding tests passed after the final country normalization fix; focused title/compound/year regressions also passed.
- The last full `unittest` run completed 175 tests and reported two failures. The country-label failure was fixed and its test passed in a targeted rerun. The remaining natural-language genre-conjunction failure also reproduces on clean baseline `41a5447`; it is pre-existing and outside this request. The full suite was not rerun after the country-only fix.
- The held-out split is a deterministic 20% query-level hash split from the same generated templates/catalog, not an independently authored evaluation corpus. The source-exclusion and fuzzy-suggestion categories each have one tuning case and no held-out case; pass rates for those should not be overgeneralized.
- The catalog has no cast, director, writer, language, or certification data and sparse awards data (36 records); the app reports these limits rather than fabricating matches. Audience/certification interpretations remain hand-written and cannot be verified against missing certification metadata.
- Recommendation quality remains bounded by catalog metadata completeness and TF-IDF similarity; generated parser pass rates do not establish human relevance.
