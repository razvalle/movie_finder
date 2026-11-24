"""Summarize local search-quality JSONL logs for manual review only.

Run from the project root:
    python search_report.py
    python search_report.py --log data/search_quality.jsonl
"""

import argparse
from collections import Counter
import json
import math
from pathlib import Path

from nlp.search_diagnostics import DEFAULT_LOG_PATH


def read_records(paths):
    records = []
    malformed = 0
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    malformed += 1
                    continue
                if isinstance(value, dict):
                    records.append(value)
    return records, malformed


def summarize(records, malformed=0):
    failures = Counter()
    zero_results = []
    low_confidence = []
    contradictions = []
    for record in records:
        query = record.get("query_text", "")
        for error in record.get("errors", []):
            failures[(error.get("stage", "unknown"), error.get("exception_type", "unknown"))] += 1
        if record.get("zero_result"):
            zero_results.append(query)
        score = record.get("top_score")
        if record.get("low_confidence") or score is None or (
            isinstance(score, (int, float)) and score < 40
        ):
            low_confidence.append({"query_text": query, "top_score": score})
        levels = [str(level).casefold() for level in record.get("match_levels", [])]
        result_scores = record.get("result_scores") or []
        has_bad_exact = any(
            (
                str(item.get("match_level", "")).casefold() == "full"
                or str(item.get("match_tier", "")).casefold() == "exact"
            )
            and (
                not isinstance(item.get("percent"), (int, float))
                or not math.isfinite(item["percent"])
                or item["percent"] < 80
            )
            for item in result_scores
            if isinstance(item, dict)
        )
        if has_bad_exact or (
            any(level in {"full", "exact"} for level in levels)
            and (
                not isinstance(score, (int, float))
                or not math.isfinite(score)
                or score < 80
            )
        ):
            contradictions.append({
                "query_text": query,
                "top_score": score,
                "match_levels": levels,
            })
    slowest = sorted(
        records,
        key=lambda record: record.get("duration_ms", 0)
        if isinstance(record.get("duration_ms"), (int, float)) else 0,
        reverse=True,
    )[:10]
    return {
        "records": len(records),
        "malformed_lines": malformed,
        "failures_by_stage": {
            f"{stage}:{exception_type}": count
            for (stage, exception_type), count in failures.most_common()
        },
        "zero_result_count": len(zero_results),
        "zero_result_queries": zero_results[:50],
        "low_confidence_count": len(low_confidence),
        "low_confidence_queries": low_confidence[:50],
        "label_score_contradiction_count": len(contradictions),
        "label_score_contradictions": contradictions[:50],
        "slowest_queries": [
            {
                "query_text": record.get("query_text", ""),
                "duration_ms": record.get("duration_ms", 0),
                "stage": record.get("stage", ""),
                "zero_result": bool(record.get("zero_result")),
            }
            for record in slowest
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        action="append",
        help="JSONL log to read; may be supplied more than once. Rotated logs are included by default.",
    )
    args = parser.parse_args()
    paths = [Path(value) for value in args.log] if args.log else sorted(
        DEFAULT_LOG_PATH.parent.glob(DEFAULT_LOG_PATH.name + "*")
    )
    records, malformed = read_records(paths)
    print(json.dumps(summarize(records, malformed), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
