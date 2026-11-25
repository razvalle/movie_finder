# Catalog cleaning rules

The app cleans records from the curated catalog, the compact IMDb catalog, and
the optional full IMDb import with the same deterministic rules in
`data/audit_catalog.py`.

1. Keep records identified as movies. Remove episodes, series, shorts, and any
   other explicit non-movie `title_type`; remove records with an adult flag.
2. Normalize text with Unicode NFC, repair common UTF-8/Windows mojibake, trim
   surrounding whitespace, and collapse repeated whitespace. Empty strings and
   IMDb's `\N` null marker become unknown (`None`).
3. Keep missing or impossible values as unknown rather than inventing defaults.
   Runtime must be an integer from 1 through 600 minutes. Release year must be
   an integer from 1880 through the current year. Invalid values become `None`.
4. Normalize genres against the canonical names and aliases in
   `data/synonyms.py`; remove empty genre tags and duplicate canonical tags.
5. Deduplicate by IMDb ID, then stable catalog ID. When neither ID is available,
   use normalized title + valid release year + valid runtime. Merge missing
   scalar fields and unique list values into the retained record.
6. Log each removal, merge, or field normalization as JSONL. The active catalog
   writes `data/catalog_audit_log.jsonl`; the IMDb importer writes
   `data/imdb_import_audit.jsonl`; and the popular-catalog builder writes
   `data/popular_build_audit.jsonl`. Import exclusions include the identifying
   source fields and an explicit reason.

The IMDb import intentionally keeps its existing 1990 lower year boundary.
Missing release years and genres are retained as unknown data, while future
years are normalized to unknown. The popular fallback selection requires a known year and genre and at least
10,000 votes. Its builder writes exactly 1,000 records or raises an error.
Such selection criteria are not catalog-cleaning removals.

The audit counts a record as unverified when runtime, release year, or genres
are missing. Search constraints must not treat those unknown values as passes.
Run `python data/audit_catalog.py --catalog local` for the active local catalog
or omit `--catalog local` to audit the deployment catalog.
