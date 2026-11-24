"""Tests for deterministic trial identity and split assignment."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from search_trial_harness import (
    _has_exact_article_alias,
    _oracle_term_forms,
    _phrase_catalog_ids,
    _token_forms,
    _trial_identity,
)


class TrialIdentityTests(unittest.TestCase):
    def test_filter_variants_have_distinct_ids_and_stable_splits(self):
        first = _trial_identity("filter-combination", "", {"genre": "music"})
        second = _trial_identity("filter-combination", "", {"genre": "drama"})

        self.assertNotEqual(first[0], second[0])
        self.assertEqual(first[1], second[1])

    def test_fragment_oracle_drops_article_alias_collisions(self):
        movies = [{"title": "The Lion King"}, {"title": "Lion"}]

        self.assertTrue(_has_exact_article_alias("The Lion", movies))
        self.assertFalse(_has_exact_article_alias("The Lion K", movies))

    def test_phrase_oracle_matches_singular_and_plural_catalog_terms(self):
        catalog = {
            "alien": {"singular"},
            "aliens": {"plural"},
            "secret": {"secret"},
            "secrets": {"secrets"},
        }

        self.assertEqual(
            _phrase_catalog_ids(catalog, "alien"),
            {"singular", "plural"},
        )
        self.assertEqual(
            _phrase_catalog_ids(catalog, "secrets"),
            {"secret", "secrets"},
        )

    def test_oracle_morphology_does_not_mutate_cached_token_forms(self):
        cached_forms = set(_token_forms("alien"))

        _oracle_term_forms("alien")

        self.assertEqual(_token_forms("alien"), cached_forms)

    def test_loss_does_not_match_the_verb_loses(self):
        self.assertNotIn("loses", _oracle_term_forms("loss"))
        self.assertIn("losses", _oracle_term_forms("loss"))


if __name__ == "__main__":
    unittest.main()
