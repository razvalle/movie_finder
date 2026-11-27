"""Regression tests for catalog-index integrity, XSS safety, and determinism."""

import copy
import json
import os
import pickle
import subprocess
import sys
import tempfile
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as app_module
from data.catalog_version import catalog_version
from data.movies import MOVIES
from nlp.concepts import (
    SCHEMA_VERSION,
    catalog_fingerprint,
    load_concept_table,
    synonym_fingerprint,
)
from nlp.extract import extract_preferences
from nlp.query import build_structured_query
from nlp.tfidf import load_or_build_tfidf_model


class _MarkupAudit(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active_payload_nodes = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "img" and attributes.get("src") == "x":
            self.active_payload_nodes.append("payload image")
        if tag == "script" and not attributes.get("src"):
            self.active_payload_nodes.append("inline script")
        if any(name.lower().startswith("on") for name, _ in attrs):
            self.active_payload_nodes.append("event handler")
        if any(
            name.lower() in {"href", "src", "action"}
            and value
            and value.strip().casefold().startswith("javascript:")
            for name, value in attrs
        ):
            self.active_payload_nodes.append("javascript URL")


class SystemIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_local_rate_limit_state_is_bounded(self):
        from collections import OrderedDict
        from nlp.smart_search import allow_search

        with (
            patch("nlp.smart_search._RATE_LIMITS", OrderedDict()),
            patch("nlp.smart_search.RATE_LIMIT_MAX_CLIENTS", 3),
            patch("nlp.smart_search._redis_client", return_value=None),
        ):
            for index in range(5):
                self.assertTrue(allow_search(f"synthetic-client-{index}"))
            self.assertEqual(len(app_module.smart_search_module._RATE_LIMITS), 3)

    def test_render_uses_preloaded_recycled_single_worker_and_busy_guard(self):
        render_config = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("--workers 1", render_config)
        self.assertIn("--threads 2", render_config)
        self.assertIn("--preload", render_config)
        self.assertIn("--max-requests 250", render_config)
        self.assertIn("--max-requests-jitter 50", render_config)
        self.assertEqual(app_module.NORMAL_SEARCH_HTML_CACHE_SIZE, 32)

    def test_tfidf_cache_carries_complete_catalog_version(self):
        record = {
            "id": "synthetic-index-record",
            "title": "Synthetic Index Record",
            "synopsis": "A small synthetic record.",
            "genres": ["mystery"],
            "themes": [],
            "mood_tags": [],
            "keywords": [],
            "runtime": 90,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.pkl"
            first = load_or_build_tfidf_model([record], path)
            with path.open("rb") as stream:
                cached = pickle.load(stream)
            first_version = catalog_version([record])
            self.assertEqual(cached["catalog_version"], first_version)
            self.assertEqual(cached["index_count"], 1)
            self.assertEqual(first["_catalog_version"], first_version)

            record["runtime"] = 91
            second = load_or_build_tfidf_model([record], path)
            with path.open("rb") as stream:
                refreshed = pickle.load(stream)
        self.assertNotEqual(refreshed["catalog_version"], first_version)
        self.assertEqual(refreshed["catalog_version"], catalog_version([record]))
        self.assertEqual(second["_index_count"], 1)

    def test_stale_concept_catalog_version_disables_expansion(self):
        record = {
            "id": "synthetic-concept-record",
            "title": "Synthetic Concept",
            "synopsis": "A synthetic record.",
            "genres": ["mystery"],
            "keywords": [],
            "themes": [],
        }
        table = {
            "metadata": {
                "schema_version": SCHEMA_VERSION,
                "catalog_fingerprint": catalog_fingerprint([record]),
                "catalog_version": {"record_count": 1, "content_hash": "stale"},
                "catalog_records": 1,
                "synonym_fingerprint": synonym_fingerprint(),
            },
            "concepts": {},
            "aliases": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "concepts.json"
            path.write_text(json.dumps(table), encoding="utf-8")
            loaded = load_concept_table([record], path)
        self.assertFalse(loaded["enabled"])
        self.assertEqual(loaded["status"], "stale")

    def test_health_and_security_headers_report_current_indexes(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["catalog_count"], len(MOVIES))
        self.assertEqual(body["index_count"], len(MOVIES))
        self.assertEqual(body["catalog_version"], app_module.CATALOG_VERSION)
        self.assertEqual(body["index_version"], app_module.CATALOG_VERSION)
        self.assertFalse(body["keyword_fallback"])

        page = self.client.get("/")
        policy = page.headers.get("Content-Security-Policy", "")
        self.assertIn("script-src 'self'", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertEqual(page.headers.get("X-Content-Type-Options"), "nosniff")

    def test_query_and_catalog_payloads_remain_text(self):
        payloads = (
            "<script>alert(1)</script>",
            '\"><img src=x onerror=alert(2)>',
            "javascript:alert(3)",
            "{{7*7}}",
            "${alert(4)}",
        )
        with patch("app.allow_search", return_value=True):
            for payload in payloads:
                response = self.client.get("/", query_string={"q": payload})
                audit = _MarkupAudit()
                audit.feed(response.get_data(as_text=True))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(audit.active_payload_nodes, [])

        synthetic = copy.deepcopy(MOVIES[0])
        synthetic["title"] = '<img src=x onerror="alert(5)">'
        synthetic["synopsis"] = "<script>alert(6)</script>"
        with patch.object(app_module, "MOVIES", [synthetic]):
            response = self.client.get("/")
        rendered = response.get_data(as_text=True)
        audit = _MarkupAudit()
        audit.feed(rendered)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(audit.active_payload_nodes, [])
        self.assertIn("&lt;img", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_parser_handles_generated_adversarial_queries_within_budget(self):
        queries = (
            "not " * 2500 + "horror",
            "9" * 10000,
            "movie " * 2500,
            "x" * 10000,
        )
        with patch("app.allow_search", return_value=True):
            for query in queries:
                started = time.perf_counter()
                response = self.client.get("/", query_string={"q": query})
                elapsed = time.perf_counter() - started
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"first 500 characters", response.data)
                self.assertLess(elapsed, 8.0)

        for query in ("not " * 100, "9" * 500, "movie " * 100, "x" * 500):
            started = time.perf_counter()
            extract_preferences(query)
            build_structured_query(query)
            self.assertLess(time.perf_counter() - started, 1.0)

    def test_normal_search_warm_latency_targets(self):
        query = "under 90 minutes, comedy, nothing else matters"
        with patch("app.allow_search", return_value=True):
            self.client.get("/", query_string={"q": "comedy"})
            self.client.get("/", query_string={"q": query})
            durations = []
            for _ in range(20):
                started = time.perf_counter()
                response = self.client.get("/", query_string={"q": query})
                durations.append((time.perf_counter() - started) * 1000)
                self.assertEqual(response.status_code, 200)
        durations.sort()
        p50 = durations[9]
        p95 = durations[18]
        p99 = durations[19]
        self.assertLess(p50, 150)
        self.assertLess(p95, 400)
        self.assertLess(p99, 1000)

    def test_search_timeout_is_longer_than_server_budget(self):
        from nlp.search_diagnostics import SEARCH_BUDGET_SECONDS
        javascript = (
            ROOT / "static" / "js" / "smart-search.js"
        ).read_text(encoding="utf-8")
        self.assertIn("const SEARCH_REQUEST_TIMEOUT_MS = 70000;", javascript)
        self.assertGreater(70.0, SEARCH_BUDGET_SECONDS)

    def test_shared_chip_links_ignore_unlisted_parameters(self):
        tag = {"key": "genre:mystery", "label": "Genre", "value": "Mystery"}
        with app_module.app.test_request_context(
            "/?q=mystery&redirect=javascript%3Aalert(1)&unexpected=1"
        ):
            with (
                patch("app.build_preference_tags", return_value=[tag]),
                patch("app.describe_tag", return_value="Remove Mystery"),
            ):
                rendered = app_module._renderable_preference_tags({}, 0, [])
        self.assertEqual(len(rendered), 1)
        self.assertIn("q=mystery", rendered[0]["remove_url"])
        self.assertNotIn("redirect", rendered[0]["remove_url"])
        self.assertNotIn("unexpected", rendered[0]["remove_url"])

    def test_search_sweep_is_identical_across_hash_seeded_workers(self):
        script = """
import json
from data.movies import MOVIES
from app import TFIDF_MODEL
from nlp.smart_search import interpret_query, retrieve_candidates, rerank_candidates
genres = sorted({genre for movie in MOVIES for genre in movie.get("genres", [])})[:8]
output = []
for genre in genres:
    query = genre + " movie"
    understanding = interpret_query(query)
    candidates = retrieve_candidates(query, understanding, MOVIES, TFIDF_MODEL)
    verified = rerank_candidates(query, candidates, understanding)
    output.append([
        (item["movie"]["id"], item.get("search_score"), item.get("match_level"),
         item.get("final_score"), item.get("match_reason"))
        for item in verified
    ])
print(json.dumps(output, ensure_ascii=True, separators=(",", ":")))
"""
        outputs = []
        for seed in ("17", "91"):
            environment = os.environ.copy()
            environment["PYTHONHASHSEED"] = seed
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
            outputs.append(completed.stdout)
        self.assertEqual(outputs[0], outputs[1])

    def test_dynamic_dom_renderers_do_not_use_html_string_sinks(self):
        for name in ("smart-search.js", "query-insights.js"):
            source = (ROOT / "static" / "js" / name).read_text(encoding="utf-8")
            self.assertNotIn("innerHTML", source)
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("|safe", template)


if __name__ == "__main__":
    unittest.main()
