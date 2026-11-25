# Search hardening and catalog cleaning report

## Outcome

The query-understanding sweep passes **2,440/2,440** generated checks. Its
deterministic split passes **1,979/1,979 tuning cases (100%)** and
**461/461 held-out cases (100%)**. On the clean baseline at `41a5447`, the
same sweep passed **693/1,979 tuning cases (35.0%)** and
**157/461 held-out cases (34.1%)**, or 850/2,440 overall. The sweep generates
queries from catalog titles, including vocabulary collisions, guarded short
titles, every movie as a similar-title source, negation, runtime forms,
audience phrases, countries, and superlatives. Expected interpretation checks
are independent of the ranking code.

The active catalog audit reports **1,065 records, 0 removals, 0 merges,
0 normalized records, 0 unverified core records, 0 malformed records**, and no
duplicate IDs, IMDb IDs, or normalized title/year/runtime identities. Its
cleaning JSONL log is empty because this catalog required no cleaning actions.

## Root causes and fixes

- **Substring matching and unsafe correction:** token matching could treat a
  vocabulary word embedded in another word as a hit, and fuzzy correction could
  rewrite valid catalog/synonym/protected tokens. Matching and correction now
  use whole-token evidence and protect known vocabulary, titles, and syntax.
  See [`nlp/fuzzy.py`](nlp/fuzzy.py), [`nlp/extract.py`](nlp/extract.py), and
  [`nlp/smart_search.py`](nlp/smart_search.py).
- **Title/filter ambiguity:** filter interpretation could consume title words,
  numeric titles, or short/stopword titles without sufficient evidence.
  Catalog-derived title spans and title-first parsing now preserve exact-title
  intent and expose alternatives via existing removable chips. See
  [`nlp/catalog_matching.py`](nlp/catalog_matching.py), [`app.py`](app.py),
  and [`tests/test_query_understanding.py`](tests/test_query_understanding.py).
- **Ambiguity, negation, and missing values:** ambiguous words could become
  hard filters without a second signal; hedged negation and negation scope
  needed explicit handling; unknown runtime/year/tag data could behave like a
  successful filter. Rules are data-driven, ambiguous interpretations remain
  soft unless resolved, and constraints distinguish satisfied, violated, and
  unverifiable. See [`data/query_rules.json`](data/query_rules.json),
  [`nlp/negation.py`](nlp/negation.py), [`nlp/scoring.py`](nlp/scoring.py),
  and [`data/movies.py`](data/movies.py).
- **Import cleaning was inconsistent and silent:** the IMDb importer encoded a
  missing runtime as `0`, import filters/deduplication did not consistently
  retain an audit trail, and popular-catalog generation could silently skip
  duplicates. A shared cleaner now normalizes data, filters explicit
  non-movies/adult flags, deduplicates with field merging, and emits JSONL
  events. See [`data/audit_catalog.py`](data/audit_catalog.py),
  [`data/import_imdb.py`](data/import_imdb.py), and
  [`data/build_popular_movies.py`](data/build_popular_movies.py).

## Changed implementation points

- [`nlp/fuzzy.py:27`](nlp/fuzzy.py#L27): safe correction of unknown tokens;
  [`nlp/extract.py:90`](nlp/extract.py#L90) and
  [`nlp/extract.py:314`](nlp/extract.py#L314): data-derived ambiguity and
  soft interpretation.
- [`nlp/query.py:126`](nlp/query.py#L126): country/demonym resolution;
  [`nlp/scoring.py:231`](nlp/scoring.py#L231),
  [`nlp/scoring.py:248`](nlp/scoring.py#L248), and
  [`nlp/scoring.py:272`](nlp/scoring.py#L272): unknown-value and exclusion
  evaluation.
- [`app.py:496`](app.py#L496), [`app.py:628`](app.py#L628),
  [`app.py:703`](app.py#L703), and [`app.py:1418`](app.py#L1418): conflict,
  entity/title lookup, and removable alternative chips.
- [`data/movies.py:932`](data/movies.py#L932),
  [`data/import_imdb.py:67`](data/import_imdb.py#L67), and
  [`data/build_popular_movies.py:50`](data/build_popular_movies.py#L50):
  shared cleaning at load/import/build boundaries.
- Regression coverage: [`tests/test_matching_loopholes.py:66`](tests/test_matching_loopholes.py#L66)
  and [`tests/test_audit_catalog.py:65`](tests/test_audit_catalog.py#L65).

## Generated query rates

The clean-baseline column is the same catalog-generated sweep before the
query-understanding fixes. The final sweep accepts soft/ambiguous negation
interpretations when the term has no disambiguating signal; requiring every
negated ambiguous word to become a hard exclusion would contradict the
ambiguity rule.

| Category | Baseline tuning | Final tuning | Baseline held-out | Final held-out |
|---|---:|---:|---:|---:|
| Audience/occasion | 0/6 | 6/6 | 0/3 | 3/3 |
| Country | 34/36 | 36/36 | 8/8 | 8/8 |
| Digit title | 71/71 | 71/71 | 15/15 | 15/15 |
| Guarded title context | 14/42 | 42/42 | 5/15 | 15/15 |
| “Like X” source resolution | 0/867 | 867/867 | 0/198 | 198/198 |
| “Like X” exclusion | 0/1 | 1/1 | no cases | no cases |
| Fuzzy source suggestions | 0/1 | 1/1 | no cases | no cases |
| Negation scope | 239/400 | 400/400 | 55/103 | 103/103 |
| Runtime notation | 3/7 | 7/7 | 0/2 | 2/2 |
| Runtime percentile | 0/2 | 2/2 | 0/2 | 2/2 |
| Short/numeric title | 16/16 | 16/16 | 2/2 | 2/2 |
| Soft negation | 0/211 | 211/211 | 0/41 | 41/41 |
| Superlative | 3/5 | 5/5 | 1/1 | 1/1 |
| Title collision | 313/313 | 313/313 | 71/71 | 71/71 |
| Unclear negation | 0/1 | 1/1 | no cases | no cases |
| **Total** | **693/1,979 (35.0%)** | **1,979/1,979 (100%)** | **157/461 (34.1%)** | **461/461 (100%)** |

## Catalog audit details

The shared cleaner is in [`data/audit_catalog.py`](data/audit_catalog.py#L35):
text/genre normalization starts at lines 35 and 67; identity selection and
merging at lines 100 and 112; catalog cleaning at line 128; JSONL writing at
line 213. The current local catalog is already clean, so the active audit
log contains no events. Its measured field coverage includes cast 0/1,065,
certification 0/1,065, and original language 0/1,065; those searches must
continue to disclose the missing data.

The IMDb import now records source identifiers and values for excluded
non-movies, adult-flagged rows, and rows below its existing 1990 lower year
boundary. Missing year/genre/runtime values are retained as unknown where
possible, future years become unknown, and the importer applies the shared
cleaner. These paths were validated with a synthetic compressed IMDb TSV; the
full IMDb source files are not installed in this workspace. The popular
catalog build script likewise now applies the cleaner and keeps an audit log,
but could not be rebuilt here because `data/imdb_movies.json` is absent.

Cleaning rules and log locations are documented in
[`data/CLEANING_RULES.md`](data/CLEANING_RULES.md) and
[`README.md`](README.md#dataset-provenance-and-cleaning-rules). The existing
“About this data” panel text now reflects the latest active-catalog audit in
[`templates/index.html`](templates/index.html#L640); only that factual text was
updated, not layout or styling.

## Validation and known limits

- Focused synthetic cleaning/import tests: **11 passed**.
- Full unittest suite: **198 tests; 197 passed, 1 failed**. The remaining
  `test_natural_genre_conjunction_is_enforced_but_or_remains_alternative` is a
  known baseline failure, reproduced on clean baseline before this catalog
  work; it is unrelated to catalog cleaning.
- The larger 5,705-case route-trial harness was started but did not complete in
  the available run; its partial run reached 4,755 cases. The separate
  2,440-case query-understanding sweep completed fully with no failures.
- The audit is a deterministic normalization/cleaning layer, not a way to
  invent missing metadata. The catalog still lacks cast, certification, and
  original-language coverage. The synthetic IMDb importer test does not replace
  validating a newly downloaded full IMDb snapshot.
- No OpenAI/GPT, embedding service, external recommendation service, Git push,
  or Render deployment was used for this work.
