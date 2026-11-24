import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nlp.search_diagnostics import (
    SearchTrace, close_search_log, redact_query, write_search_record,
)
from search_report import summarize


class SearchDiagnosticsTests(unittest.TestCase):
    def test_query_redaction_removes_contact_and_name_patterns(self):
        redacted = redact_query(
            "Jordan Smith jordan@example.com 555-123-4567 203.0.113.42",
        )
        self.assertNotIn("Jordan Smith", redacted)
        self.assertNotIn("jordan@example.com", redacted)
        self.assertNotIn("555-123-4567", redacted)
        self.assertNotIn("203.0.113.42", redacted)

    def test_trace_records_stage_counts_and_finite_quality_fields(self):
        trace = SearchTrace("space movies")
        trace.count("retrieved", 5)
        record = trace.finish(200, {
            "searched": True,
            "result_count": 1,
            "results": [{"percent": 82, "match_tier": "exact"}],
        })
        self.assertEqual(record["candidate_counts"]["retrieved"], 5)
        self.assertEqual(record["top_score"], 82)
        self.assertEqual(record["match_levels"], ["exact"])
        self.assertFalse(record["zero_result"])
        self.assertNotIn("remote_addr", record)

    def test_jsonl_writer_and_report_summary(self):
        record = {
            "query_text": "redacted query",
            "errors": [{"stage": "retrieve", "exception_type": "ValueError"}],
            "zero_result": True,
            "low_confidence": True,
            "top_score": 20,
            "match_levels": ["exact"],
            "duration_ms": 120,
            "stage": "retrieve",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quality.jsonl"
            try:
                write_search_record(record, path)
                saved = json.loads(path.read_text(encoding="utf-8").strip())
                summary = summarize([saved])
            finally:
                close_search_log(path)
        self.assertEqual(summary["failures_by_stage"], {"retrieve:ValueError": 1})
        self.assertEqual(summary["zero_result_count"], 1)
        self.assertEqual(summary["low_confidence_count"], 1)
        self.assertEqual(summary["label_score_contradiction_count"], 1)
        self.assertEqual(summary["slowest_queries"][0]["duration_ms"], 120)


if __name__ == "__main__":
    unittest.main()
