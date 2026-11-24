"""Synthetic regressions for catalog-derived concept expansion."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nlp.concepts import expand_query, load_concept_table
from nlp.smart_search import (
    _normalize_query_concepts,
    local_understanding,
    retrieve_candidates,
    rerank_candidates,
)
from nlp.tfidf import build_tfidf_model
from tests.topic_search_benchmark import _catalog_index


def synthetic_catalog():
    return [
        {
            "id": "direct", "title": "Quiet Garden",
            "synopsis": "A gardener grows plants inside an orbital greenhouse.",
            "genres": ["drama"], "themes": ["orbital greenhouse"],
            "keywords": ["microgravity", "seed bank"], "mood_tags": [],
            "runtime": 95, "release_year": 2001, "average_rating": 7.5, "vote_count": 120,
        },
        {
            "id": "associated-a", "title": "Beyond Earth",
            "synopsis": "A botanist tends seedlings in microgravity using a seed bank.",
            "genres": ["drama"], "themes": ["space habitat"], "keywords": [],
            "mood_tags": [], "runtime": 98, "release_year": 2002,
            "average_rating": 8.0, "vote_count": 200,
        },
        {
            "id": "associated-b", "title": "Closed Habitat",
            "synopsis": "A research team tests microgravity and preserves a seed bank.",
            "genres": ["science fiction"], "themes": ["space habitat"], "keywords": [],
            "mood_tags": [], "runtime": 102, "release_year": 2003,
            "average_rating": 7.0, "vote_count": 90,
        },
        {
            "id": "single-associated", "title": "Gravity Trial",
            "synopsis": "A research team studies microgravity.",
            "genres": ["drama"], "themes": ["research"], "keywords": [],
            "mood_tags": [], "runtime": 100, "release_year": 2004,
            "average_rating": 7.2, "vote_count": 100,
        },
        {
            "id": "title-only", "title": "Orbital Greenhouse",
            "synopsis": "A detective investigates a missing painting.",
            "genres": ["crime"], "themes": ["investigation"], "keywords": ["Orbital Greenhouse"],
            "mood_tags": [], "runtime": 105, "release_year": 2005,
            "average_rating": 9.0, "vote_count": 500,
        },
    ]


def synthetic_concepts():
    return {
        "enabled": True,
        "aliases": {"orbital greenhouse": "orbital greenhouse"},
        "concepts": {
            "orbital greenhouse": {
                "document_support": 2,
                "aliases": [{
                    "term": "orbital greenhouse", "source": "hand-written", "category": "theme",
                }],
                "associations": [
                    {"term": "microgravity", "weight": 0.30, "source": "generated"},
                    {"term": "seed bank", "weight": 0.20, "source": "generated"},
                ],
            },
        },
    }


class ConceptExpansionTests(unittest.TestCase):
    def test_unclassified_surface_terms_still_form_direct_query_concepts(self):
        normalization = _normalize_query_concepts(
            "movies about orbital greenhouse",
            {
                "required_terms": [],
                "keywords": [],
                "soft_preferences": [],
                "genres": [],
                "mood_tone": [],
                "filters": {},
            },
            synthetic_catalog(),
        )
        self.assertIn(["orbital", "greenhouse"], normalization["concepts"])

    def test_oracle_ignores_metadata_tags_that_only_repeat_the_title(self):
        movies = [{
            "id": "copy", "title": "Orbital Greenhouse", "original_title": "",
            "synopsis": "IMDb rating 7.0/10 from 100 votes; released in 2000. Genres: drama.",
            "keywords": ["Orbital Greenhouse"], "themes": [], "genres": ["drama"],
        }]
        _, phrase_documents = _catalog_index(movies)
        self.assertNotIn("orbital", phrase_documents)
        self.assertNotIn("greenhouse", phrase_documents)

    def test_expansion_weight_is_capped_and_excluded_terms_are_not_reintroduced(self):
        table = synthetic_concepts()
        expansions = expand_query(
            [["orbital", "greenhouse"]],
            {"soft_preferences": []},
            table,
            excluded_terms={"microgravity"},
        )
        self.assertEqual([item["term"] for item in expansions], ["seed bank"])
        self.assertLessEqual(sum(item["weight"] for item in expansions), 0.30)
        self.assertEqual(expansions[0]["source"], "generated")

    def test_related_results_need_two_distinct_non_title_associations(self):
        movies = synthetic_catalog()
        model = build_tfidf_model(movies)
        understanding = local_understanding("orbital greenhouse movies")
        with patch("nlp.smart_search.CONCEPT_TABLE", synthetic_concepts()):
            candidates = retrieve_candidates(
                "orbital greenhouse movies", understanding, movies, model,
            )
            assessed = rerank_candidates(
                "orbital greenhouse movies", candidates, understanding,
            )
        by_id = {item["movie"]["id"]: item for item in assessed}
        self.assertIn("direct", by_id)
        self.assertIn("associated-a", by_id)
        self.assertIn("associated-b", by_id)
        self.assertNotIn("single-associated", by_id)
        self.assertLess(
            [item["movie"]["id"] for item in candidates].index("associated-a"),
            [item["movie"]["id"] for item in candidates].index("title-only"),
        )
        self.assertEqual(by_id["associated-a"]["match_tier"], "related")
        self.assertIn("synopsis", by_id["associated-a"]["match_reason"])
        title_evidence = by_id["title-only"]["query_evidence"]["details"]
        self.assertTrue(title_evidence)
        self.assertEqual(title_evidence[0]["source"], "title")
        self.assertFalse(title_evidence[0]["strong"])
        self.assertNotEqual(
            by_id["associated-a"]["query_evidence"]["associated_count"], 0,
        )

    def test_missing_or_stale_table_disables_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            self.assertFalse(load_concept_table([], missing)["enabled"])
            stale = Path(directory) / "stale.json"
            stale.write_text('{"metadata": {}, "concepts": {}, "aliases": {}}', encoding="utf-8")
            self.assertFalse(load_concept_table([], stale)["enabled"])


if __name__ == "__main__":
    unittest.main()
