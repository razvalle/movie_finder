"""Synthetic regressions for compound normalization and evidence scoring."""

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module
from nlp.query import build_structured_query
from nlp.scoring import is_excluded, rank_movies
from nlp.smart_search import (
    EXACT_MATCH_THRESHOLD,
    _cached_text_tokens,
    _fuzzy_terms,
    local_understanding,
    _normalize_query_concepts,
    _query_evidence,
    rerank_candidates,
    retrieve_candidates,
    _text_tokens,
)
from nlp.tfidf import build_tfidf_model


def synthetic_catalog():
    return [
        {
            "id": "topic", "title": "Quiet Winter", "synopsis": "A caretaker tends a lunar orchard.",
            "genres": ["drama"], "release_year": 2001, "runtime": 95,
            "themes": ["lunar orchard"], "mood_tags": [],
            "keywords": ["orchard keeper"], "content_descriptors": [],
            "average_rating": 7.5, "vote_count": 120,
        },
        {
            "id": "title", "title": "Lunar Orchard", "synopsis": "A traveler crosses a desert.",
            "genres": ["adventure"], "release_year": 2004, "runtime": 101,
            "themes": ["journey"], "mood_tags": [],
            "keywords": ["desert travel"], "content_descriptors": [],
            "average_rating": 8.0, "vote_count": 200,
        },
        {
            "id": "head", "title": "Quiet Grove", "synopsis": "A botanist tends an orchard under stars.",
            "genres": ["drama"], "release_year": 2005, "runtime": 99,
            "themes": ["botany"], "mood_tags": [],
            "keywords": ["night garden"], "content_descriptors": [],
            "average_rating": 7.0, "vote_count": 90,
        },
        {
            "id": "modifier", "title": "Lunar Passage", "synopsis": "A traveler crosses barren plains.",
            "genres": ["adventure"], "release_year": 2006, "runtime": 103,
            "themes": ["journey"], "mood_tags": [],
            "keywords": ["desert travel"], "content_descriptors": [],
            "average_rating": 7.2, "vote_count": 100,
        },
    ]


def rendered_cards(html):
    cards = []
    for markup in re.findall(r'<article class="movie-card".*?</article>', html, re.S):
        title = re.search(r'<h3 class="movie-card__title">(.*?)</h3>', markup, re.S)
        percent = re.search(r'class="movie-card__percent"[^>]*>(\d+)% match', markup, re.S)
        badge = re.search(r'class="match-badge match-badge--(full|partial)"', markup)
        reason = re.search(r'<p class="movie-card__match-reason">(.*?)</p>', markup, re.S)
        cards.append({
            "title": title.group(1).strip() if title else "",
            "percent": int(percent.group(1)) if percent else 0,
            "level": badge.group(1) if badge else "",
            "reason": reason.group(1).strip() if reason else "",
        })
    return cards


class CompoundEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.movies = synthetic_catalog()
        self.model = build_tfidf_model(self.movies)

    def search(self, query):
        understanding = local_understanding(query)
        candidates = retrieve_candidates(query, understanding, self.movies, self.model)
        assessed = rerank_candidates(query, candidates, understanding)
        preferences = build_structured_query(query)["preferences"]
        normalized_terms = next((
            item.get("normalized_terms") for item in candidates
            if item.get("normalized_terms")
        ), None)
        if normalized_terms:
            preferences["free_text_keywords"] = normalized_terms
        evidence = {str(item["movie"]["id"]): item["query_evidence"] for item in candidates}
        relevance = {str(item["movie"]["id"]): item["relevance_score"] for item in candidates}
        ranking = rank_movies(
            [item["movie"] for item in assessed],
            preferences,
            self.model,
            evidence_by_id=evidence,
            relevance_by_id=relevance,
            apply_filters=False,
        )
        return candidates, assessed, ranking

    def test_spacing_hyphen_and_joined_forms_share_candidates_and_notice(self):
        queries = (
            "lunar orchard movies",
            "lunarorchard movies",
            "lunar-orchard movies",
            "Lunar Orchard Films",
        )
        ids_by_query = []
        with (
            patch.object(app_module, "MOVIES", self.movies),
            patch.object(app_module, "TFIDF_MODEL", self.model),
            patch.object(app_module, "allow_search", return_value=True),
        ):
            client = app_module.app.test_client()
            for query in queries:
                candidates, _, _ = self.search(query)
                ids_by_query.append({item["movie"]["id"] for item in candidates})
                response = client.get("/", query_string={"q": query, "per_page": 12})
                self.assertEqual(response.status_code, 200)
                cards = rendered_cards(response.get_data(as_text=True))
                self.assertTrue(cards)
                self.assertTrue(all(
                    card["level"] != "full" or card["percent"] >= EXACT_MATCH_THRESHOLD
                    for card in cards
                ))
                self.assertEqual(
                    build_structured_query(query)["unsupported_filters"]["languages"], [],
                )
                if query.startswith("lunarorchard"):
                    self.assertIn(b"Showing results for", response.data)
                elif query.startswith("lunar-orchard"):
                    self.assertNotIn(b"Showing results for", response.data)
                    self.assertIn("Lunar Orchard", [card["title"] for card in cards])
        self.assertTrue(set.intersection(*ids_by_query))
        self.assertLessEqual(max(map(len, ids_by_query)) - min(map(len, ids_by_query)), 1)

    def test_phrase_evidence_outranks_title_only_and_full_has_threshold(self):
        candidates, assessed, ranking = self.search("lunar orchard movies")
        candidate_ids = [item["movie"]["id"] for item in candidates]
        self.assertEqual(candidate_ids[:2], ["topic", "head"])
        self.assertIn("title", candidate_ids)
        self.assertNotIn("modifier", candidate_ids)
        scores = {str(item["movie"]["id"]): item["percent"] for item in ranking["results"]}
        self.assertGreater(scores["topic"], scores["title"])
        self.assertGreater(scores["head"], scores["title"])
        levels = {str(item["movie"]["id"]): item["match_level"] for item in assessed}
        self.assertEqual(levels["title"], "partial")
        self.assertEqual(levels["head"], "partial")
        for item in assessed:
            if item["match_level"] == "full":
                self.assertGreaterEqual(item["estimated_percent"], EXACT_MATCH_THRESHOLD)
        self.assertIn("theme tag", assessed[0]["match_reason"])
        head_reason = next(
            item["match_reason"] for item in assessed if item["movie"]["id"] == "head"
        )
        self.assertIn("orchard", head_reason)
        self.assertNotIn("strong evidence for orchard", head_reason)

    def test_a_language_name_must_not_be_inferred_from_a_catalog_phrase(self):
        self.assertEqual(
            build_structured_query("Lunar Orchard Films")["unsupported_filters"]["languages"],
            [],
        )
        self.assertEqual(
            build_structured_query("Hindi films")["unsupported_filters"]["languages"],
            ["hindi"],
        )

    def test_accent_folded_exclusion_matches_synthetic_catalog_text(self):
        movie = {
            "id": "accented",
            "title": "Café Nocturne",
            "synopsis": "",
            "genres": [],
            "mood_tags": [],
            "themes": [],
            "keywords": [],
            "content_descriptors": [],
            "runtime": 90,
            "release_year": 2000,
        }
        self.assertTrue(is_excluded(movie, {"excluded_keywords": ["cafe"]}))
        preferences = build_structured_query("movies without cafe")["preferences"]
        ranking = rank_movies(
            [movie], preferences, build_tfidf_model([movie]),
        )
        self.assertEqual(ranking["results"], [])
        self.assertEqual(ranking["excluded_count"], 1)

    def test_short_fuzzy_match_cannot_verify_a_strong_keyword(self):
        self.assertEqual(_fuzzy_terms(["tos"], ["toys"]), {"tos", "toys"})
        evidence = _query_evidence({
            "id": "toys", "title": "Toys",
            "synopsis": "A story about toys.",
            "genres": [], "themes": [], "mood_tags": [],
            "keywords": ["toys"], "content_descriptors": [],
        }, [["tos"]])
        self.assertFalse(evidence["strong"])

    def test_catalog_token_cache_preserves_fresh_list_semantics(self):
        _cached_text_tokens.cache_clear()

        first = _text_tokens("Lunar Orchard")
        second = _text_tokens("Lunar Orchard")

        self.assertEqual(first, ["lunar", "orchard"])
        self.assertIsNot(first, second)
        self.assertEqual(_cached_text_tokens.cache_info().hits, 1)

    def test_phrase_normalization_keeps_a_generic_word_when_catalog_supports_compound(self):
        movie = {
            "id": "ending", "title": "Soft Landing",
            "synopsis": "A lunar ending changes a traveler.",
            "genres": ["drama"], "release_year": 2010, "runtime": 90,
            "themes": [], "mood_tags": [], "keywords": ["lunar ending"],
            "content_descriptors": [], "average_rating": 7.0, "vote_count": 10,
        }
        understanding = local_understanding("lunar endings")

        normalized = _normalize_query_concepts(
            "lunar endings", understanding, [movie],
        )

        self.assertNotIn("bending", normalized["terms"])


if __name__ == "__main__":
    unittest.main()
