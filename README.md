# Movie Finder by Mood and Description (Python / Flask)

Same project as the JS version, rebuilt in **pure Python**. The whole
NLP pipeline, scoring, and rendering happen server-side with Flask —
there is no client-side JavaScript anywhere in this app. Search is a
plain HTML form that submits `?q=...` as a GET request; Flask
re-renders the page with results computed fresh each time.

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

## Real IMDb catalog (2000-2026)

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
the command resumes from the progress file.

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

Current result: **22/23 passing**. The one documented failure (`V1`) is
a known limitation — see "Known limitations" below.

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
   runtime 12%, release year 6%) are **re-normalized to only the
   categories you actually mentioned** — so a query with no runtime
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

- **Retroactive negation** ("horror movies are not what I want") isn't
  caught — the negation module only scopes *forward* from a cue word,
  which covers the overwhelming majority of natural phrasing ("not
  horror", "no scary movies") but not sentences where the negated word
  comes *before* "not". See `tests/test_report.md`, case `V1`.
- **Tagalog-only negation cues** ("walang", "huwag") aren't recognized —
  only English negation cues are implemented.
- Runtime phrases with two numbers but only one stated unit (e.g. "at
  least 100 minutes but not longer than 150") only pick up the first
  fully-qualified constraint found.

These are documented rather than silently hidden, in line with the
testing structure the project asks for.
