"""Redis adapter tests using a fake client, without requiring a Redis service."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nlp import smart_search


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expirations = {}
        self.eval_calls = []
        self.allowed = 1

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, seconds, value):
        self.values[key] = value
        self.expirations[key] = seconds

    def eval(self, script, num_keys, key, now_ms, window_ms, maximum, member):
        self.eval_calls.append((script, num_keys, key, now_ms, window_ms, maximum, member))
        return self.allowed


class SharedRedisTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()

    def test_cache_round_trip_uses_hashed_key_and_ttl(self):
        key = ("understand", "private query")
        value = {"keywords": ["harbor"], "genres": ["drama"]}
        with patch.object(smart_search, "_redis_client", return_value=self.redis):
            smart_search._cache_set(key, value)
            result = smart_search._cache_get(key)
        self.assertEqual(result, value)
        cache_key = next(iter(self.redis.values))
        self.assertNotIn("private query", cache_key)
        self.assertEqual(self.redis.expirations[cache_key], smart_search.CACHE_SECONDS)

    def test_rate_limit_uses_shared_sliding_window_script(self):
        with patch.object(smart_search, "_redis_client", return_value=self.redis):
            self.assertTrue(smart_search.allow_search("203.0.113.10"))
            self.redis.allowed = 0
            self.assertFalse(smart_search.allow_search("203.0.113.10"))
        script, num_keys, key, _, window_ms, maximum, member = self.redis.eval_calls[0]
        self.assertIn("ZREMRANGEBYSCORE", script)
        self.assertIn("ZADD", script)
        self.assertEqual(num_keys, 1)
        self.assertNotIn("203.0.113.10", key)
        self.assertEqual(window_ms, smart_search.RATE_LIMIT_WINDOW_SECONDS * 1000)
        self.assertEqual(maximum, smart_search.RATE_LIMIT_REQUESTS)
        self.assertIn(":", member)


if __name__ == "__main__":
    unittest.main()