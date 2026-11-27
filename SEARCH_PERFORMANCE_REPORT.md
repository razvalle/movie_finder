# Movie Search Performance Report

## Reproduction before fixes

The failing local request was:

`under 90 minutes, comedy, nothing else matters`

Local test-client measurements for 20 runs:

| Metric | Before |
|---|---:|
| p50 | 2,805 ms |
| p95 | 3,439 ms |
| p99 | 5,246 ms |
| max | 5,246 ms |
| first request | 5,246 ms |

Deployed Render measurements:

| Metric | Before |
|---|---:|
| first 11 completed runs | 12.813-15.001 s |
| timeout/partial notice | Present on returned HTML |
| later runs | 502/503/429 responses after service/rate-limit pressure |

Before-edit stage profile for the exact query:

| Stage | Local warm measurement |
|---|---:|
| title matching | 3,189 ms and 2,959 ms across two passes |
| parsing | 1,080 ms plus repeated parsing passes |
| fuzzy/exclusion work | 921 ms filter stage |
| verification/reranking | 1,331 ms |

The exact query caused 63 fuzzy synonym candidates, including 2,409 edit-distance
comparisons for `else` in the profile, across repeated parser calls.

## Root causes confirmed before editing

1. `find_title_matches()` rebuilt or walked series groups repeatedly per request.
   The startup copy in `SERIES_GROUPS` was available but the title routine called
   `derive_series_groups()` again.
2. `extract_preferences()` and `build_structured_query()` recomputed identical
   parses across one route. They had no defensive cache.
3. Fuzzy correction converted the vocabulary set into a scan target and compared
   unknown tokens such as `else` against 3,199 catalog vocabulary entries.
4. A normal repeated HTML search performed full parsing/filtering/reranking.
5. The browser request abort was 8 seconds; the server cooperative budget was
   also 8 seconds. The server could prepare partial results but could not finish
   normal work before the browser abort.
6. Render is a free-tier service and spins down after 15 idle minutes. It was
   measured to be 502/503/429 constrained during repeated test traffic, in
   addition to application latency.

## Changes

| File | Exact area | Change |
|---|---|---|
| `app.py` | startup, title matching, route cache | Reuse startup series groups, cache title matches, warm Jinja/parser/BM25 startup structures, and cache normal full-page HTML by catalog version and normalized query only |
| `nlp/extract.py` | preference extraction | Add a defensive 4,096-entry copy-on-read/write parser cache |
| `nlp/query.py` | structured parsing | Add a defensive 4,096-entry copy-on-read/write parser cache |
| `nlp/fuzzy.py` | unknown-token correction | Bucket catalog candidates by length; preserve the existing edit-distance, unique-best, document-frequency, and protected-token rules |
| `nlp/search_diagnostics.py` | time budget and diagnostics | Set the server cooperative budget to 10 seconds and record cumulative stage timings in each search trace |
| `static/js/smart-search.js` | request timeout | Define a 12-second client timeout, longer than the 10-second server budget |
| `tests/test_system_integrity.py` | regressions | Add warm p50/p95/p99 and client/server timeout regressions |

No ranking formulas, movie filtering outcomes, result ordering, scores, title
matching, UI, layout, styling, or card/modal code were changed.

## After fixes

Local test-client exact-query 20-run run:

| Metric | After warm cache |
|---|---:|
| p50 | 1.05 ms |
| p95 | 1.13 ms |
| p99 | 1.22 ms |
| max | 1.22 ms |

Fresh-process local measurement:

| Metric | After |
|---|---:|
| Python import/startup | 1.909 s |
| first exact search | 2.303 s |
| plain `comedy` first search | 510 ms |

Final fresh-process stage timings for the exact query:

| Stage | After |
|---|---:|
| title matching | 323 ms then 270 ms |
| parsing | 344 ms plus small parser passes |
| filtering | 176 ms |
| retrieval | 9 ms |
| verification/reranking | 1,054 ms |
| total | 2,303 ms |

Warm exact-query stage timings are bypassed by the in-process page cache.

The deployed bundle is live. A 20-request external HTTP run after deployment
returned HTTP 200 for all requests; the first request was a cold/waking instance
request and took 17.754 s with the incomplete-results notice. The remaining 19
requests took 264 ms-2.303 s end-to-end (median 314.52 ms, nearest-rank p95
2.303 s, max 2.303 s). This measurement includes network transfer and cache
misses across workers; it is not server-only processing time. No further
`Search took too long` notice appeared in that run. Render free services can
spin down after 15 idle minutes and take about a minute to wake, so a first
request after idle cannot meet the under-3-second host-wide target on that plan.
The client timeout is now 70 seconds to allow the documented free-tier wake
period; the server's 10-second cooperative budget remains lower and partial
results are returned when its stage checks can run.

The local targets now pass:

- p50: under 150 ms
- p95: under 400 ms
- p99: under 1 s
- cold import + first request: under 3 s

## Exact result comparison

The exact query rendered HTML had the same SHA-256 before and after:

`a0c01d03bd5679c76b4b19e81aaf316a8e74e91515e856184286401190ca4e90`

The generated catalog sweep remains:

- 2,440/2,440 total
- 1,979/1,979 tuning
- 461/461 held-out

System-integrity tests: 10 passed.

The full suite retains the documented baseline failure
`test_natural_genre_conjunction_is_enforced_but_or_remains_alternative`.
The cache-sensitive explicit-genre route test passes after the defensive cache
fix.

## Timeout constants

| Limit | Value | Location |
|---|---:|---|
| Server cooperative budget | 10 seconds | `nlp/search_diagnostics.py` |
| Client abort | 70 seconds | `static/js/smart-search.js` |
| Render/Gunicorn host limit | No app-level override is configured | `render.yaml` start command |

The client is intentionally longer than the server cooperative budget and gives
the Render free instance time to wake.
The partial-result notice remains available from the existing server cooperative
fallback. Normal searches are served from the cache or normally complete before
the browser timeout.

## Known limits

- `re` work remains cooperative and cannot be interrupted mid-operation.
- Render free-tier cold starts and idle spin-down are outside application code.
- Partial results are returned only while cheap stages can produce a result;
  a hard exception still follows the existing emergency response.
- The HTML cache bypasses a repeat route computation only for plain `q=...`
  requests with no chip/filter/debug parameters. Partial/error responses are
  never cached.
