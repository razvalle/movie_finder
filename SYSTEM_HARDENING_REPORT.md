# Search System Hardening Report

## Root causes and fixes

| Area | Confirmed root cause | Change |
|---|---|---|
| Index/catalog integrity | The runtime TF-IDF cache used an implementation fingerprint but did not store an explicit whole-catalog count/hash version. The deployed builder wrote the legacy compressed artifact, while the app loads separate TF-IDF caches. Concept metadata had a record count and narrower semantic fingerprint, but not the same complete catalog version. | Added a stable catalog version; stored and checked it in both TF-IDF caches and the concept table; rebuilt stale indexes within a 5,000-record/3-second startup limit; otherwise marked health degraded and forced keyword fallback. Render now builds and validates both indexes before deployment. |
| Health | There was no `/health` route. | Added `/health` with catalog/index counts, versions, concept count, and degraded/fallback state; Render now probes it. |
| XSS defenses | Templates autoescaped user/catalog values, and no confirmed exploitable XSS was found, but client rendering still had five `innerHTML` assignments (escaped dynamic strings and fixed markup) and no CSP header. | Replaced the dynamic HTML assignments with DOM nodes and `textContent`, retained the existing parameter-key allow-list for generated chip links, and added CSP plus `X-Content-Type-Options`. |
| Regex/per-request cost | The route already caps parsed input at 500 characters and uses an 8-second cooperative request budget. The warmed adversarial parser measurements did not show catastrophic behavior, and reviewed patterns had no nested unbounded quantifiers. | Kept existing parsing semantics and limits; added adversarial route and parser latency regressions. The time budget remains cooperative: Python's `re` cannot interrupt a single in-progress match. |
| Worker determinism | The “Surprise me” selection used `Math.random()`, search caches omitted the catalog version, several ranking sorts lacked final title/ID tie-breaks, and set iteration could affect index construction order. | Seeded the surprise choice; keyed query/re-ranking caches by normalized query, parsed filters, and catalog version; made result ties and index-building iteration deterministic. |

## Changed files and locations

| File | Changed lines | Purpose |
|---|---:|---|
| `.gitignore` | 12 | Ignore generated like-search cache. |
| `app.py` | 98-212, 1657-1659, 2597-2661, 3011 | Catalog version, CSP/security headers, bounded index startup and `/health`, fallback notice, deterministic ordering. |
| `data/catalog_version.py` | 7-21 | Stable record-count and SHA-256 version helper. |
| `data/build_concept_expansions.py` | 163-223 | Version concept output and sort index inputs. |
| `data/build_embeddings.py` | 43-82 | Build and validate required versioned deployment indexes. |
| `data/concept_expansions.json` | 115073-115080 | Generated concept-table version metadata. |
| `nlp/catalog_matching.py` | 338-353 | Deterministic series-member ordering. |
| `nlp/concepts.py` | 124-219 | Validate and boundedly rebuild the versioned concept table. |
| `nlp/smart_search.py` | 447-457, 820-834, 1580-1623 | Versioned cache keys and deterministic BM25/reranking. |
| `nlp/tfidf.py` | 128-205 | Versioned cache validation and bounded index rebuild. |
| `render.yaml` | 6-8 | Required build step and `/health` probe. |
| `static/js/home-discovery.js` | 24-41 | Seeded “Surprise me” choice. |
| `static/js/query-insights.js` | 44-70 | Safe synopsis highlighting with DOM nodes/text. |
| `static/js/smart-search.js` | 44-70, 237-263, 312-332, 372-444 | Safe rendering for recent searches, suggestions, highlights, and related-title results. |
| `tests/test_system_integrity.py` | 59-233 | Version, CSP/XSS, health, adversarial-input, allow-list, and worker-determinism regressions. |

## Before/after measurements

| Check | Before | After |
|---|---:|---:|
| `/health` | 404 | 200; 1,065 catalog rows and 1,065 indexed rows |
| Index version | No explicit full catalog version in TF-IDF cache | Count 1,065; SHA-256 `3b28579b3180cf4abce7d14470392d218d8a22736c2df547c2e77abc720f6f4f` matches the loaded catalog |
| CSP header | Missing | Present; `script-src 'self'`, `object-src 'none'` |
| Client HTML-string sinks | 5 `innerHTML` assignments | 0 in application JavaScript; no `|safe` in templates |
| Generated query sweep | 1,979/1,979 tuning; 461/461 held-out (previous checkpoint) | 1,979/1,979 tuning; 461/461 held-out |
| Hash-seeded workers | Not covered | Identical result output with `PYTHONHASHSEED=17` and `91` across eight catalog-derived genre queries |

Adversarial parser timings below are 30 warmed samples per input, measuring `extract_preferences` plus `build_structured_query` on inputs capped at 500 characters:

| Generated input | p95 | p99 |
|---|---:|---:|
| Repeated `not ` | 110.86 ms | 113.51 ms |
| 500-digit run | 69.10 ms | 76.34 ms |
| Repeated `movie ` | 105.88 ms | 124.38 ms |
| 500-character run | 57.32 ms | 59.67 ms |

The route regression also sent 10,000-character repeats of negation, digits, a single word, and one character. All returned HTTP 200 with the existing “first 500 characters” notice, under the 8-second per-request budget. Earlier warmed measurements were noisier (up to 180 ms p99); no regex speedup is claimed because parser patterns were not changed.

Generated XSS regressions covered script tags, attribute breakouts, `javascript:` text, template-expression strings, and HTML in synthetic catalog titles/synopses. None became active elements or event handlers. A local browser check also found zero injected `img`/`script` nodes in the highlight layer and confirmed suggestions still render.

## Regex inventory

The parser/source audit found 180 Python regex call sites. The following line numbers enumerate every `re.compile`, `re.search`, `re.match`, `re.sub`, `re.findall`, `re.split`, or `re.fullmatch` call in `app.py` and `nlp/*.py` at the time of this report:

| File | Count | Call-site lines |
|---|---:|---|
| `app.py` | 34 | 121, 183-186, 457, 628, 797, 824, 834, 970, 977, 994, 1010-1011, 1017, 1021, 1029, 1037, 1047, 1082, 1118, 1131, 1155, 1172, 1183, 1357, 1735, 1925, 2147, 2149, 2151, 2172, 2352 |
| `nlp/cast_verification.py` | 1 | 63 |
| `nlp/catalog_matching.py` | 21 | 33, 48-52, 55, 60, 62-66, 80, 111, 142, 219, 249, 258, 261, 266-267 |
| `nlp/concepts.py` | 1 | 29 |
| `nlp/fuzzy.py` | 2 | 47, 61 |
| `nlp/normalize.py` | 38 | 15-40, 43-44, 58-67 |
| `nlp/query.py` | 29 | 122, 129, 132, 135, 141, 146, 149, 154, 183, 197, 199, 212, 216, 222, 248, 270, 283-284, 289, 293, 295, 297, 299, 302, 304, 320-321, 340, 351 |
| `nlp/release_year.py` | 8 | 22, 41, 53, 62, 66, 71, 76, 79 |
| `nlp/runtime.py` | 18 | 41, 44, 47, 83, 88-89, 94, 106, 110, 114, 120, 133, 141, 149, 155, 177, 181, 212 |
| `nlp/scoring.py` | 1 | 70 |
| `nlp/search_diagnostics.py` | 4 | 21-24 |
| `nlp/smart_search.py` | 22 | 105, 345, 351-353, 356, 358, 363, 369, 483, 560, 860, 879, 896, 974, 978, 992, 996, 1010, 1016, 1200, 1462 |
| `nlp/tfidf.py` | 1 | 28 |

No nested, overlapping unbounded quantifier pattern was identified in the user-query parser; user-controlled parsing remains capped at 500 characters. The bounded similar-title capture is limited to 100 characters.

## Validation and known limits

- Deployment index build completed for 1,065 records and validated both TF-IDF caches and the concept table.
- System-integrity regressions: 8 passed. Existing long-query regression: passed. Python compilation and `git diff --check`: passed.
- Full suite: 206 tests, 205 passed, 1 failed. The failure is the previously documented baseline `test_natural_genre_conjunction_is_enforced_but_or_remains_alternative` (`Comedy Only` incorrectly included); it is unrelated to these system-hardening changes.
- The full generated route harness was stopped after 345/5,705 cases because the remaining route-by-route run was taking several minutes per few hundred cases. Of the 345 recorded cases, 342 had no recorded failures; 3 failed: `The Grand` (source recall), `Schindler s List` (false hard-filter result), and `Kill Bill:` (false exact result). These are search-matching cases outside the system-hardening scope; no fix is claimed.
- Startup rebuild is deliberately bounded. Catalogs larger than 5,000 records, or indexes taking over 3 seconds to rebuild, enter degraded health and use keyword fallback until the deployment build step regenerates them.
- No CSS, layout, theme, color, or component styling was changed. The local browser rendered the existing interface and suggestion behavior under CSP. No Git push or Render deployment was performed.
