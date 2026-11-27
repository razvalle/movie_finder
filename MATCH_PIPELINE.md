# Match scoring and result pipeline

## Request flow

Search requests use one final result policy after retrieval and hard filtering:

1. `nlp.smart_search` retrieves candidates and verifies their catalog evidence.
   Verification supplies candidate facts; it does not define the final card
   score, tier, reason, or ordering.
2. `nlp.scoring.rank_movies` computes weighted preference scores using the
   centralized weights in `nlp.match_policy`. Hard exclusions and runtime,
   year, genre, and metadata checks remain in the existing constraint layer.
3. `app.py` checks scored results against the filtered catalog IDs and the
   independent final exclusion gate.
4. `nlp.match_pipeline.finalize_results` assigns the displayed score and the
   `exact`, `close`, or `related` tier, validates evidence against the movie's
   own fields, builds the card reasons, and orders results. Exact title and
   series IDs are passed in explicitly.
5. The route paginates only after finalization. The tier count uses the same
   finalized entries. The browser sorter keeps tier order ahead of optional
   local sort controls.

Browse mode uses `order_browse_movies` from the same module. It ranks selected
genre matches first, then uses a Bayesian-adjusted rating, title, and stable ID.
The rating prior is computed from the current catalog; its minimum-vote count
comes from `data/query_rules.json`.

## Evidence and labels

Direct evidence is retained only when the cited phrase is present in the cited
movie field. Associated evidence is similarly checked against the movie's
metadata. Structured genre, mood, theme, runtime, and year matches are checked
from the movie record. A missing selected or requested genre is stated as
`Missing: <genre>` rather than described as matched.

Exact-title and series results are explicit exceptions with title-derived
evidence. Results without direct evidence are kept in the closest tier only
when the route intentionally supplied them as fallback candidates; they are
not labeled as related evidence. A “movies like X” candidate carries its
normalized metadata-similarity score and resolved source title into finalization;
it remains a close match and is capped below the exact threshold. Scores are
bounded to 0–100 and the final exclusion gate remains independent of score and
ranking.

## Shared policy

`nlp.match_policy` owns score weights, tier thresholds, and Bayesian rating
inputs. `nlp.scoring`, `nlp.smart_search`, and `nlp.match_pipeline` consume that
policy. Final result counts, labels, reasons, and ordering are produced by
`nlp.match_pipeline`; browse ordering uses its shared Bayesian rating helper.

Focused regression coverage is in `tests/test_match_pipeline.py`,
`tests/test_search_compound_evidence.py`, and `tests/test_app_routes.py`.
