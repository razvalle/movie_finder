# Movie Finder Search and Genre Fix Report

## Root causes

- **A. Genre chips:** browse filtering used an OR `any(...)` check at the base filter and title/text ranking had no selected-genre tier. The fix keeps every partial genre match eligible, removes excluded genres, and sorts by all-selected count before evidence/rating/title.
- **B. Exact titles:** title candidates were scored by the normal weighted ranker, so a title match could receive less than 100. The fix marks exact title and detected series matches 100 in the same score path and downgrades no title match in the runtime self-check.
- **C. Series completeness:** `find_title_matches` used exact/prefix/substring paths but did not derive series members and the candidate limit could stop at a base title. The fix derives groups and adds every matched member before ranking/pagination.
- **D. Autosuggest:** suggestions used a separate normalized title list and phrase prefix ranking. It now derives the same catalog title aliases and series groups and orders exact, series, prefix, then contains matches.
- **E. UI state:** submission did not always close suggestions, blur/escape handling was incomplete, and stale fetch completion could leave loading/status state. Submission closes suggestions; a request counter/abort, timeout cleanup, and success/failure cleanup retain the existing loading behavior.
- **F. Topic labels:** title-only topic evidence could be labeled close while the count line only reported close cards. The count is now generated from actual card tiers and title-only evidence remains "Closest match"/"Unverified".

## Files changed

- `nlp/catalog_matching.py`
- `data/build_series_groups.py`
- `data/series_groups.json`
- `app.py`
- `templates/index.html`
- `static/js/smart-search.js`
- `SEARCH_FIX_REPORT.md`

## Verification

- Focused search suites: **39/39 passed**.
- Route spot checks:
  - Biography + Drama: before 459 led by non-Biography titles; after 459, 58 all-genre then 401 partial.
  - Drama + Adventure: before 763 led by non-Adventure titles; after 763, 59 all-genre then 704 partial.
  - `how to train your dragon`: before only the first title; after both series members at 100 and Exact match.
  - `dragon movies`: 6 cards, 0 exact / 6 close / 0 related.
- App route suite: **34/41 passed**. The remaining 7 failures are legacy expectations that conflict with the requested remakes, exact-alternate-title, and series/partial-genre behavior.

## Series review

The generated `data/series_groups.json` contains 33 detected groups. Groups were derived automatically with three-word-or-longer prefixes and sequel markers. False groups can be excluded only by a future reviewed override file; no search-code special cases or typed title lists were used.

## Known limits

- A sequel that shares no name words or structural continuation with its predecessor cannot be linked by title-only rules.
- Duplicate catalog records with the same exact title are returned as remakes/alternate exact-title entries and ordered by votes.
- The existing catalog may not contain all alternate or original titles, although `original_title` and alternate title fields are read when present.

## Styling confirmation

No theme, accent, layout, chip, card, pagination, preview, language control, or detail-modal styling was changed.
