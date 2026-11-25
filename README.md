# Movie Finder by Mood and Description (Python / Flask)

Movie Finder is a Python 3 / Flask application using Jinja templates,
plain HTML forms, and a small browser-side JavaScript enhancement. Search
and ranking run on the server. The NLP stack combines local structured
parsing, BM25/TF-IDF lexical retrieval, fuzzy and title matching, weighted
scoring, and rule-based candidate assessment. Search requires no AI model or API key.

```
"I want something suspenseful but not horror, preferably a mystery under two hours."
```
```
Detected: Mood: Suspenseful · Genre: Mystery · Exclude: Horror · Runtime: <120 min

1. Arrival — 80% Match
   ✓ Mystery genre matched
   ✓ Suspenseful mood matched
   ✓ Runtime is 116 minutes (under your 120-minute limit)
   ✓ No horror tag detected
```

## Running it (VS Code)

1. Open the `movie-finder-py` folder in VS Code.
2. Open a terminal (Terminal → New Terminal) and create/activate a
   virtual environment (recommended but not required):
   ```
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   ```
3. Install the one dependency:
   ```
   pip install -r requirements.txt
   ```
4. Run the app:
   ```
   python app.py
   ```
5. Open the URL it prints (usually `http://127.0.0.1:5000`) in your browser.

## IMDb catalog

The repository can use IMDb's public bulk datasets instead of synthetic
fallback records. Download `title.basics.tsv.gz` and `title.ratings.tsv.gz`
from `https://datasets.imdbws.com/` into `data/imdb/`, then run:

```
python data/import_imdb.py
```

This writes `data/imdb_movies.json`. When that file exists, the app loads it
automatically and ignores the fallback catalog. The imported records include
real movie titles, original titles, release years, runtimes, genres, adult
flags, ratings, and vote counts. IMDb bulk data does not provide plot
synopses or posters, so those fields remain metadata-based or optional.

When the full catalog is not installed, the app uses the 65 hand-curated
records plus 1,000 real IMDb titles with the highest vote counts in the local
export. The compact `data/popular_movies.json` is tracked for deployment; to
rebuild it after importing an IMDb catalog, run:

```
python data/build_popular_movies.py
```

The Recognition filter selects movies with a curated major-award win label.
Popularity in the fallback catalog is based on IMDb vote counts.

### Dataset provenance and cleaning rules

Sources: IMDb public datasets (`title.basics`, `title.ratings`, optional
`title.akas`) for the 1,000 popular titles, 65 hand-written curated records, and
optional Wikidata/Wikimedia and TMDB enrichment. IMDb data is used under IMDb's
non-commercial dataset terms.

The shared import and normalization rules are documented in
[`data/CLEANING_RULES.md`](data/CLEANING_RULES.md). In brief, the importer logs
non-movie and adult-flagged exclusions, keeps missing years/genres as unknown,
stores missing or impossible runtimes as `None`, and normalizes genres and text.
It retains the existing 1990 lower year boundary. IMDb release regions come
from `title.akas` and are release markets, not production origin. IMDb does not
provide plots, so generated synopsis text is metadata, not a plot description.
The importer writes `data/imdb_import_audit.jsonl`.

`data/build_popular_movies.py` selects titles with at least 10,000 votes and
known title, year, and genres, removes matching curated records, and writes
exactly 1,000 distinct normalized title/year/runtime records (with the existing
pinned selections), or raises an error. Its transformations are logged to
`data/popular_build_audit.jsonl`.

Verify the current state with `python data/audit_catalog.py`. For the deployment
catalog it reports per-field coverage, duplicate IDs/IMDb IDs/title-year-runtime
identities, malformed records, and coverage of imported IMDb fields. Cleaning
events are written to `data/catalog_audit_log.jsonl`. The imported
`original_title` field was present for the 1,000 IMDb records but wasn't shown
by the app; cards now display it when it differs from the display title. Its
value comes directly from IMDb and is not rewritten.

### Current metadata limits and priorities

The deployment catalog's synopsis coverage is not plot coverage: IMDb import
creates a transparent title/year/genre template, not a plot summary. The audit
distinguishes those templates from real plot descriptions. Cast, certification
and original language remain absent unless real enrichment records are bundled.
No plot, cast or content-descriptor values have been invented to fill those gaps.

TMDB enrichment is opt-in. Both `data/enrich_tmdb.py` and `data/enrich_cast.py`
return without reading or changing catalog/progress files when `TMDB_API_KEY` is
unset. With a key present, explicitly running either script activates its
documented enrichment; no code change is needed to enable it. The audit output
and JSON report show whether a key is configured at audit time.

The standalone relevance benchmark is a synthetic prioritization signal, not
real user feedback. Its 100 queries per split cover genre, decade, runtime and
a keyword derived from the source title; they do not test cast or content
descriptor requests. The latest run reported recall@20 of 0.770 on the tuning
split and 0.690 on held-out, with no genre, decade, or runtime violations.
Keyword misses were 2.2% and 2.4%, respectively; these are soft relevance
misses, not hard-filter violations. Earlier reports combined keyword misses
with hard-filter violations and should not be compared to the separated metric.
This supports prioritizing better descriptive metadata and lexical matching,
but cannot justify prioritizing cast or content descriptors, which its
templates never ask for. Run
`python tests/relevance_benchmark.py --split tune` for tuning metrics and
`python tests/relevance_benchmark.py` for held-out reporting; do not tune on
the held-out split.

The audit prints these limitations and priorities, and includes them in its
JSON report. Re-run the benchmark with `python tests/relevance_benchmark.py`
after code or catalog changes; do not treat these generated queries as validated
user behavior.

### Editable query rules

`data/query_rules.json` contains editable negation cues and degree words,
postposed-negation scope patterns, country/demonym aliases, runtime percentiles,
and the minimum vote count used by Bayesian rating sorts. Audience and occasion
mappings (for example, kids/family-night/date-night to genres and a maximum
certification) are hand-written product interpretations, not catalog-derived
facts. Certification limits are only enforced when a movie has a recognized
rating; the current catalog has no certification data, so audience searches
show that limitation instead of implying that every result is age-verified.

### Adding real plot descriptions

TMDB provides real plot overviews. Set a TMDB v3 API key in the terminal and
run the resumable enrichment script:

```powershell
$env:TMDB_API_KEY = "your_api_key"
python data/enrich_tmdb.py
```

The script matches records by IMDb ID, stores the TMDB overview in the
existing `synopsis` field, records `plot_source: "TMDB"`, preserves all IMDb
metadata, and writes progress every 25 records. It never invents a plot when
TMDB has no overview. Because the catalog is large and TMDB rate limits API
traffic, the complete enrichment can take a long time; stopping and rerunning
the command resumes from the progress file. If the key is absent, it reports
that enrichment was skipped and leaves all files unchanged.

### Cast and gender verification

The project also includes a conservative cast-verification pass for queries such
as "no woman", "all-male cast", or "female lead". This uses TMDB credits as
supporting evidence and never claims a movie has no women without enough billed-
cast coverage.

```powershell
$env:TMDB_API_KEY = "your_api_key"
$env:TMDB_CATALOG_PATH = "deployment"
python data/enrich_cast.py
# or:
npm run enrich-cast
```

The script:
- resolves TMDB ids via IMDb id when available;
- falls back to exact normalized title + year + runtime (within five minutes)
   when those fields are present, and flags ambiguous matches for review;
- caches raw TMDB payloads on disk so reruns are cheap;
- records ambiguous or unmatched movies in `data/tmdb_cast_review.csv`;
- stores enriched records in `data/movie_cast_enrichment.json`; completed records
   survive interrupted runs and are merged into the app catalog at startup;
- writes `data/cast_coverage_report.json` with database-wide status totals and
   five representative evidence records.

The classifier distinguishes between:
- strong: no women found in the available top-billed cast, high gender coverage,
  and no female cues;
- moderate: likely all-male but some genders are unknown;
- weak/unknown: insufficient credits or incomplete data;
- contradicted: any female cast or female-character cues appear in the billed cast
  or narrative metadata.

The script also stores overview, tagline, TMDB keywords, runtime, original
language, release certification, poster path, and production-country codes when
TMDB supplies them. Set `TMDB_CATALOG_PATH=deployment` to enrich exactly the
65 curated and 1,000 popular records served by Render; this avoids accidentally
processing a large local-only IMDb export. The app merges matching rows from
`data/movie_cast_enrichment.json` at startup.

The app never claims the entire movie has no women: the current script stores at
most 15 top-billed credits, which cannot prove absence from the full cast or film.
Strong and moderate labels describe only the available credit evidence; weak and
unknown results remain explicitly unverified.

### Smart search outcomes

The rule-based parser extracts structured requirements, exclusions, filters,
and semantic terms. Retrieval uses local BM25/TF-IDF cosine similarity, fuzzy
term matching, and title hints; candidate assessment checks the retrieved movies
against available catalog metadata and explains satisfied, contradicted, and
unverifiable requirements. Weighted scoring ranks the candidates. Stopwords and
negated concepts are removed from retrieval terms. Search is deterministic and
does not make external AI or embedding calls. If no useful retrieval signal is
available, search falls back through direct lexical/fuzzy and title matching,
local TF-IDF similarity, and finally top-rated browsing within the already
filtered candidate pool. Fallback results are labeled closest, never Exact, and
hard filters are not relaxed. Results are labeled Exact, Close, or Related
according to the verified constraints and evidence tier. The query cache and
per-client search rate limit are local to each app worker.

Compound terms are normalized from catalog and synonym vocabulary. Phrase
evidence outranks weak evidence from a compound's final term; leading modifiers
are not counted alone. Title-only evidence is weaker than synopsis or tag
evidence. An "Exact match" requires verified constraints, strong evidence
for every parsed search term, and a displayed score of at least 80%; otherwise
the result remains a closest match.
Final score ties are broken by evidence strength, rating, vote count, normalized
title, then catalog ID for stable ordering.

### Search resilience and diagnostics

Search input is capped at 500 characters and page/filter parameters are bounded.
Each pipeline stage has a simpler fallback; filtering and result self-checks
fail closed so an error cannot bypass exclusions, runtime, year, genre, country,
recognition, or cast constraints. Search has a cooperative eight-second
per-request budget. A slow stage is replaced with a partial, lower-confidence
result path;
the budget cannot interrupt a single synchronous operation mid-call.

Each request appends one structured JSON record to
`data/search_quality.jsonl`. The local log rotates at 2 MiB with three backups.
It contains stage candidate counts, elapsed time, score/tier summaries, fallback
and zero-result flags, and sanitized query text. Common email, phone, IP, and
multi-word name patterns are redacted; request IPs are not recorded. Search
errors are summarized for manual review only:

```powershell
python search_report.py
```

The generated-query regression harness uses an independent catalog scan and
reports tuning and held-out results separately. Run the full harness or either
split with:

```powershell
python tests/search_trial_harness.py
python tests/search_trial_harness.py --split tuning
python tests/search_trial_harness.py --split held-out
```

It checks input boundaries, filter invariants, score/tier consistency,
determinism, pagination, fallback behavior, offline operation, and whether
topic results have direct phrase or catalog-term evidence. Topic seeds are
catalog keyword, theme, or genre phrases with direct evidence in at least
three movies; expected IDs are found by an independent catalog scan, not by
calling the search route. A failed trial is recorded for review; the quality
log is not used to learn or change search behavior.

Topic concepts are expanded from synopsis, keyword, theme, and genre
co-occurrence. Rebuild the versioned, reviewable table with:

```powershell
python data/build_concept_expansions.py
```

The builder records generated associations separately from hand-written
synonym aliases, requires support in at least two catalog records, and stores
catalog and synonym fingerprints. The app loads the table at startup; a missing
or stale table disables expansion and keeps lexical search available. Generated
terms together receive at most 0.30 total query weight, so they cannot outweigh
a direct phrase. Exact results require every parsed constraint to be verified,
strong phrase/theme/keyword evidence for each required term, and at least 80%.
Close results have partial direct evidence. Related results require at least
two distinct associated terms in non-title metadata; title-only evidence is
weaker and cannot qualify by a single title word. Exclusions and other hard
filters are applied before retrieval.

`tests/topic_search_benchmark.py` independently scans catalog fields to build
strong-evidence sets; its templated spacing, plural, typo, filler, and word-
order variants are split by topic into tuning and held-out sets. Run
`python tests/topic_search_benchmark.py --split tune` for tuning metrics and
`python tests/topic_search_benchmark.py --split heldout` for held-out metrics.
The benchmark does not use app results as its oracle; do not tune on held-out
queries.

The catalog used by this workspace's offline trials contains 1,065 records; the
full IMDb import (`data/imdb_movies.json`) is not installed here. The compact
catalog has title, year, genre, runtime, rating, and vote metadata, but no
bundled TMDB cast or certification enrichment. IMDb bulk data does not provide
plot or cast metadata. Therefore requests that depend on absent or partial
fields (for example, proving there are no women or no violence) can only be
partial or unverified until enrichment is bundled, and some negative claims
remain impossible to prove even with top-billed cast enrichment.

### Offline generated-search trial harness

Run the end-to-end harness from the project root:

```powershell
python tests/search_trial_harness.py --output tests/.search-trials-final.jsonl
```

It generates catalog-derived title, description, filter, negation, synonym,
typo, language-mix, number, similarity, and surprising-input trials; computes
expected matches by direct catalog scans; and exercises the real Flask route
with the rule-based pipeline. It removes `OPENAI_API_KEY` and blocks socket
connections during each request. The JSONL output records each query,
preferences, stage counts, results, explanations, latency, and failures. The
deterministic split is preserved when parameters change; ambiguous generated
title fragments that conflict with an exact title after removing a leading
article are excluded from the fragment set.

Latest run: 5,705 trials over the 1,065-record compact catalog. All 4,618
tuning trials and all 1,087 held-out trials passed; the command exited
successfully. Compared with the immediately preceding run, the three tuning
failures were resolved: two came from the oracle conflating a noun with a
different verb form, and one exposed a false exact label when a generic word
was dropped from a supported phrase. The held-out baseline guard passed:
recall@48 improved from 93.90% to 100%, and hard-constraint violations fell
from 15.73% to 0%. The independent relevance benchmark remains unchanged:
recall@20 is 77% on tuning and 69% on held-out, with no hard-constraint
violations.

| Metric | Tuning | Held-out | Overall |
| --- | ---: | ---: | ---: |
| Recall@48 | 100.00% | 100.00% | 100.00% |
| Hard-constraint violation rate | 0.00% | 0.00% | 0.00% |
| Empty-result rate | 0.41% | 0.37% | 0.40% |
| False-exact rate | 0.00% | 0.00% | 0.00% |
| Crashes / HTTP 5xx | 0 / 0 | 0 / 0 | 0 / 0 |
| Latency p95 / p99 | 0.611 / 0.832 s | 0.592 / 0.743 s | 0.607 / 0.808 s |

For the independent strong-evidence topic oracle, the tuning split had 742
topic trials and the held-out split had 172. Strong-evidence recall@48 was
7.02% / 7.03%, precision@48 was 85.36% / 81.67%, and the identical-score tie
rate was 72.81% / 71.86%, respectively. Neither split had an empty topic
result. These topic metrics are separate from source-title recall above: the
oracle counts all catalog records with direct phrase or associated-term
evidence, so broad topics can have many expected records beyond the first page.

Trial counts by category (all categories passed):

| Category | Trials | Category | Trials |
| --- | ---: | --- | ---: |
| Title | 3,188 | Combined | 296 |
| Synonym | 241 | Negation | 241 |
| Typo | 209 | Filler | 186 |
| Negation placement | 182 | Runtime number forms | 110 |
| Similar-to | 100 | Title fragment | 94 |
| Topic/filter combination | 94 | Spacing compound | 93 |
| Spacing hyphen | 93 | Casing | 93 |
| Plural | 93 | Filter combination | 80 |
| Single year | 69 | Description | 65 |
| Negation keyword | 60 | Country | 44 |
| Single genre | 20 | Surprising input | 16 |
| Word order | 12 | Single runtime | 12 |
| Language mix | 5 | Recognition chip | 4 |
| Decade number forms | 3 | Unsupported cast | 1 |
| Unsupported certification | 1 | | |

Pagination passed: pages were disjoint, covered the complete result set, and
invalid or out-of-range page numbers fell back safely. Empty responses had a
visible explanation or next step. All generated queries stayed offline, and
the runtime deployment-parity checks confirmed the TF-IDF cache and concept
table loaded from correctly cased paths without an API key or network access.
The transient JSONL output records expected and returned IDs, preferences,
stage counts, explanations, latency, and failures.

Root causes fixed on the tuning set:

- Parser context had treated descriptive country mentions and some ranking,
  person, or negated phrases as active filters; country/ranking/person
  interpretation and numeric/fuzzy negation handling were tightened in
  `nlp/query.py` and `nlp/extract.py`.
- Exclusions could be checked against concatenated metadata or miss normalized
  and synonym forms. Verification is now field-local and Unicode-normalized in
  `nlp/scoring.py`; excluded/runtime syntax is removed from retrieval terms in
  `nlp/smart_search.py`.
- Hyphenated metadata terms and stale tokenizer caches caused lexical recall
  gaps. The tokenizer now indexes word components and includes its version in
  the TF-IDF cache fingerprint in `nlp/tfidf.py`.
- Leading-article and title-plus-filter queries could resolve to a prefix or
  sequel rather than the exact catalog title. Exact title checks now precede
  prefixes; structured suffixes are recognized while preserving hard filters.
  The ten tuning title/title-plus-runtime failures fell to zero.
- BM25 cache entries keyed only by Python list IDs could reuse a stale index
  after list identity reuse. The cache now validates and retains the source
  list; a focused regression reproduces the collision.
- Filter-combination trials with the same blank query reused one trial ID,
  making stability comparisons invalid. IDs now include request parameters
  while the split remains unchanged; the repeated-request determinism check
  passes.
- A fuzzy plural could normalize to a generic stop word and then be dropped,
  reducing a supported multiword phrase to one requirement. Normalization now
  retains that word only when catalog data supports the adjacent compound, so
  the phrase is verified as a phrase rather than as one strong token.
- The legacy examples treated “under N minutes” as inclusive. Their expected
  maximums now reflect the strict `< N` runtime rule; the legacy runner passes
  51/51 without changing runtime behavior.

Trial counts by category: title 3,188; combined constraints 296; descriptions
292; negation 241; synonyms 241; typos 141; runtime-number forms 110; title
fragments 94; filter combinations 80; negation-keyword 60; country 44; single
year 69; single genre 20; surprising inputs 16; single runtime 12; similar-to
100; decade-number forms 3; language-mix 5; recognition-chip 4; unsupported
cast 1; unsupported certification 1. Counts are deterministic for the current
catalog and harness version.

The pass rate is not a guarantee for every possible query. This harness repeats
selected requests in one process; it does not certify cross-worker consistency
or the deployed Render build. The compact catalog lacks cast and certification
coverage, and its synopsis/keyword fields cannot answer every plot-specific or
negative-content request. Such limitations must be explained in the response
rather than presented as verified matches.

### Environment and deployment

Set these values in the Render dashboard under **Environment**, never in the
template, browser JavaScript, or committed source:

| Variable | Required for | Default |
| --- | --- | --- |
| `TMDB_API_KEY` | One-time catalog enrichment only | unset |
| `TMDB_CATALOG_PATH` | Choosing input for `data/enrich_cast.py`; `deployment` selects the Render catalog | first available local catalog |
| `REDIS_URL` | Shared search cache and rate limit across workers | unset (process-local state) |

Set `REDIS_URL` to a private Redis connection URL when running multiple
Gunicorn workers or app instances. Cache entries expire after ten minutes and
the per-client rate limiter uses a shared sliding 60-second window. If Redis is
not configured, the app retains its process-local cache and rate limiter; if a
configured Redis server is temporarily unavailable, it logs a warning and
falls back to process-local behavior until the shared service recovers.

For a compact Render data build, run from the project root before deploying:

```powershell
$env:TMDB_API_KEY = "your_tmdb_key"
$env:TMDB_CATALOG_PATH = "deployment"
python data/enrich_cast.py
python data/build_embeddings.py
```

`data/build_embeddings.py` writes a compressed local TF-IDF index; it does not
require an AI key, and that optional artifact is not required by the app at
runtime. Re-run enrichment and the index builder when their source catalog data
changes. The Render service continues to use the existing `render.yaml`
build/start commands; TMDB is not called by the deployed request path.

Search performs query parsing, retrieval, assessment, and scoring locally. Its
response time depends on catalog size; query caching and rate limits are local
to each app worker.

**Next improvements:**
- Add curated violence/romance and character-presence annotations with provenance.
- Move query/verifier caches and rate limits to shared Redis for multi-worker Render.
- Add offline relevance and calibration evaluations with a labeled query set.

### Interface filters and preferences

Genre, Recognition, Per page, and the single header Language control submit
immediately; Enter submits the search without adding a newline. The header
language selector also filters by TMDB `original_language` when that field is
present. Its interface translations support English, Spanish, Tagalog, French,
German, Portuguese, Japanese, Korean, Chinese, and Hindi. Movie titles and plot
details remain in the catalog's original language.

Age ratings are display-only metadata on individual movie cards; there is no age
rating filter. The header moon/sun control stores the theme in browser local
storage. TMDB certification, poster paths, and production countries are stored
by `data/enrich_cast.py` when supplied by TMDB. Cards show a movie-specific TMDB
poster and production origin only when those fields exist; IMDb `origin_regions`
are release markets and are not presented as a film's production origin. Bundle
an updated `data/movie_cast_enrichment.json` with the Render deployment after
running enrichment. Without that file, cards omit unavailable posters, show
"Origin unavailable", and omit certification rather than substituting a shared
placeholder or a potentially misleading region.

### Keyless poster and origin fallback

When `TMDB_API_KEY` is unavailable, `data/enrich_public_metadata.py` enriches
the compact Render catalog from public Wikidata data. It joins IMDb-backed
records by IMDb ID and curated records by exact English title plus release year.
It records production countries from Wikidata property `P495` and uses only
Wikimedia Commons images whose filenames explicitly identify them as posters.
Stills and unrelated images are rejected; accepted images carry a per-card
Commons source link. The script is resumable and keeps failed public lookups
retryable instead of aborting the whole run.

The generated `data/movie_public_enrichment.json` bundle contains 1,054
production-country records and 4 explicitly identified Commons poster images.
It is bundled with the Render deployment. TMDB remains preferred when available
and takes precedence over these fallback fields.

### Supabase poster storage and feedback

The Render catalog posters can be uploaded to a public Supabase Storage bucket.
When `SUPABASE_URL` is configured, the app builds each movie record's public
`poster_url` from its ID and the default `movie-posters` bucket. Feedback uses
the same project's Postgres REST API when `SUPABASE_URL` and
`SUPABASE_SERVICE_ROLE_KEY` are configured. Without these variables, local
development stores feedback in `instance/feedback.sqlite3`.

1. Apply `supabase/migrations/001_storage.sql` in the Supabase SQL Editor.
2. Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in your local environment.
   Never commit the service key. Optionally set `SUPABASE_POSTER_BUCKET` to use
   a different bucket name.
3. Upload and publicly verify the bundled Render catalog posters locally:

   ```powershell
   python -m data.upload_posters_supabase
   ```

   Recheck public availability later without uploading again:

   ```powershell
   python -m data.upload_posters_supabase --verify-only
   ```

4. After verification passes for every poster, add `SUPABASE_URL` and
   `SUPABASE_SERVICE_ROLE_KEY` to Render's environment settings and deploy. Never
   expose the service key in client-side code or commit it to Git.
5. Remove `static/images/` from the repository only after the deployed poster
   URLs have been confirmed working. The existing ignore rule already prevents
   new untracked poster files from being added. A normal deletion commit leaves
   the old blobs in Git history; purging those blobs requires a coordinated
   history rewrite and force-push.

### TMDB attribution and deployment notes

The project uses TMDB as a metadata source for enrichment, not as a substitute
for local catalog ownership. Include this attribution wherever the enriched
metadata is presented: "This product uses the TMDB API but is not endorsed or
certified by TMDB." The app can be deployed to Render without a TMDB key when
pre-enriched data is bundled; set `TMDB_API_KEY` only when running enrichment.
The raw-response cache is optional and should not be committed by default.

### Known limitations

- TMDB credit data can be missing, incomplete, or inconsistent for smaller films.
- Some voice-only, animated, and documentary cases require human review.
- The verifier covers at most the top 15 billed cast records. It does not
   establish who appears in every scene or prove that a movie contains no women.
- Low coverage and ambiguous cast data are shown as "Unverified" rather than a
   strong claim.

VS Code will also detect `app.py` as a Flask entry point if you use the
Run and Debug panel (Run → Start Debugging → Python File).

## Running the test suite

```
python tests/run_tests.py
```

Runs every case in `tests/test_cases.py` through the real pipeline (no
mocking) and prints a pass/fail report, then writes the full table to
`tests/test_report.md` with: user input, extracted preferences, top
result, pass/fail, reason for failure, and a suggested improvement.

Current result: **51/51 passing**. These are hand-written example sentences, so
they are regression tests only and not evidence of search accuracy. Accuracy is
measured by `python tests/relevance_benchmark.py`, which uses generated queries
and a held-out split.

### Structured search regression tests

```powershell
python tests/test_structured_query.py
python tests/test_app_routes.py
```

The first command checks country aliases, ranking intent, genre aliases,
dates, negative filters, people/language extraction, and semantic-text
separation. The second checks Flask behavior for titles, stale UI filters,
country/genre conflicts, descriptions, typos, and unknown text.

## Structured query pipeline

The application interprets each query using this local rule-based pipeline:

```
raw query -> normalization -> country/ranking aliases -> title detection
-> explicit genre/mood/theme/date/negative filters -> semantic residual
-> database filtering -> TF-IDF similarity -> weighted ranking -> pagination
```

`nlp/query.py` owns canonical country aliases (including `PH`, `Pinoy`, and
`Pinas`), ranking words (`best`, `top`, `highest rated`, `popular`), genre
aliases, and debug output. Explicit constraints are removed before TF-IDF so
words such as `best`, country names, and `2020` cannot create keyword matches.
The server logs the parsed structure for each request.

The IMDb bulk catalog includes title, year, genre, runtime, ratings, votes,
and listed regions. It does not contain cast, crew, language, or real plots.
Those entities are parsed and logged but can only become hard filters after
TMDB enrichment supplies the corresponding metadata. The UI labels fallback
IMDb regions honestly; TMDB production-country data takes precedence when it
has been imported.

## Project structure

```
movie-finder-py/
├── app.py                     Flask entry point — the only file that touches HTTP/rendering
├── requirements.txt           One dependency: Flask
├── templates/
│   └── index.html             Jinja2 template (server-rendered, zero client JS)
├── static/
│   └── css/style.css          Visual design (all styling lives here)
├── data/
│   ├── movies.py               The movie dataset (edit/expand here)
│   └── synonyms.py             Editable synonym dictionary
├── nlp/
│   ├── normalize.py           Step 2: lowercase, expand contractions, strip punctuation
│   ├── tokenize.py            Step 3: word tokens, n-grams, stopword removal
│   ├── negation.py            Step 10: negation cue detection + scope ranges
│   ├── runtime.py             Step 7: "under two hours" -> {"max": 120}
│   ├── release_year.py        Optional: "from the 90s", "before 2000", etc.
│   ├── extract.py             Steps 4-9: orchestrates the above into a preference dict
│   ├── tfidf.py               Step 11: TF-IDF model over the movie corpus
│   ├── similarity.py          Step 12: cosine similarity
│   ├── scoring.py             Steps 13-14: hard filter + weighted scoring + ranking
│   └── explain.py             Step 15: turns a score breakdown into check/cross reasons
└── tests/
    ├── test_cases.py          23 test cases: easy/moderate/difficult/synonym/negation/Taglish
    └── run_tests.py           Runs the pipeline against test_cases.py, writes test_report.md
```

Every module is independently editable, per the project brief:

| Want to change...              | Edit only...                       |
|----------------------------------|-------------------------------------|
| The movies                       | `data/movies.py`                    |
| Synonyms / slang mapping         | `data/synonyms.py`                  |
| How text is cleaned up           | `nlp/normalize.py`                  |
| How negation is detected         | `nlp/negation.py`                   |
| How runtime phrases are parsed   | `nlp/runtime.py`                    |
| Scoring weights                  | `nlp/scoring.py` (`BASE_WEIGHTS`)   |
| What reasons get displayed       | `nlp/explain.py`                    |
| Look and feel                    | `static/css/style.css`              |
| Page layout / what's shown       | `templates/index.html`, `app.py`    |

### Adding movie posters

Place a JPEG in `static/images/` using the movie's numeric ID as the
filename, for example `static/images/1.jpg`. Search result cards load that
image automatically; movies without an image show a placeholder telling you
which filename to add. The image can be replaced at any time without editing
the movie data or template.

## How the pipeline works

1. **Normalize** — lowercase, expand contractions (`don't` → `do not`,
   important so negation cues aren't hidden inside a contraction),
   strip punctuation into clause-boundary markers.
2. **Tokenize** — split into words; build 1-3 word n-grams so multi-word
   synonym phrases ("edge of your seat") can be matched.
3. **Negation scoping** — find cue words (`not`, `no`, `without`,
   `except`, `don't want`, ...) and compute which token ranges they
   negate (up to a clause boundary, a contrastive conjunction like
   "but", or a 6-word cap).
4. **Synonym lookup** — every n-gram is checked, longest-phrase-first,
   against a reverse index built from `synonyms.py`. A hit inside a
   negation scope becomes an **exclusion**; otherwise it's a
   **preference**. Genre nouns (e.g. "horror") take precedence over
   mood adjectives when a word could be either, since that's almost
   always what the speaker means.
5. **Runtime / release-year extraction** — regex rules convert phrases
   like "under two hours" or "from the 90s" into numeric constraints.
6. **TF-IDF** — any leftover descriptive words that aren't in the
   synonym dictionary (e.g. "heist", "hotel", "detective") are vectorized
   against a TF-IDF model built from the movie corpus (synopsis + genres
   + themes + moods + keywords).
7. **Hard filter** — movies matching an excluded genre/mood/theme are
   removed before scoring, so an excluded category can never leak into
   results.
8. **Weighted scoring** — every remaining movie is scored 0-100%.
   Weights (genre 28%, mood 24%, theme 14%, keyword similarity 16%,
   runtime 12%, release year 6%, source-aware query evidence 68%, and
   lexical BM25/TF-IDF relevance 10%) are **re-normalized to only the
   evidence categories active for that query** — so a query with no runtime
   constraint doesn't get penalized for movies of any length.
9. **Explanation** — the score breakdown is converted into the
   check/cross reason list shown under each result.

No part of the ranking decision is made by a pretrained model; every
weight, rule, and threshold above lives in plain, readable Python.

## Expanding the dataset toward 500+ movies

`data/movies.py` is just a `MOVIES` list of plain dicts. To scale up:
1. Keep the same schema (see the docstring at the top of the file).
2. Add more dicts to the list, or split additional batches into new
   files (e.g. `data/movies_batch2.py`) and concatenate them in
   `movies.py`.
3. If you add a new canonical genre/mood/theme tag that doesn't exist
   yet, add a matching entry to `data/synonyms.py` so users can refer
   to it in natural language.

Nothing else needs to change — the NLP pipeline and scoring are
data-driven and don't hard-code any movie.

## Known limitations

- **Tagalog negation coverage** is limited to a small set of Taglish cues;
   other Tagalog-only phrasing may not be recognized.
- Runtime phrases with two numbers but only one stated unit (e.g. "at
  least 100 minutes but not longer than 150") only pick up the first
  fully-qualified constraint found.

These are documented rather than silently hidden, in line with the
testing structure the project asks for.
