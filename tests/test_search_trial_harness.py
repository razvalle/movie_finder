"""Tests for deterministic trial identity and split assignment."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from search_trial_harness import _has_exact_article_alias, _trial_identity


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


if __name__ == "__main__":
    unittest.main()
