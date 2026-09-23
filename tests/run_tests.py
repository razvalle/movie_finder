"""
tests/run_tests.py
-----------------------------------------------------------------------
TEST RUNNER
Runs every case in test_cases.py through the REAL pipeline (extract ->
rank), checks the `expect` assertions, and prints/saves a report with:
    user input | extracted preferences | expected | actual | pass/fail |
    reason for failure | improvement suggestion

Usage (from the project root):
    python tests/run_tests.py
-----------------------------------------------------------------------
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.movies import MOVIES
from nlp.extract import extract_preferences
from nlp.tfidf import build_tfidf_model
from nlp.scoring import rank_movies
from tests.test_cases import TEST_CASES

TFIDF_MODEL = build_tfidf_model(MOVIES)


def includes_all(actual_list, expected_list):
    return all(e in actual_list for e in expected_list)


def evaluate(expect, prefs, ranked):
    """Runs the `expect` block for a case against extracted preferences + ranked results. Returns (pass, failures[])."""
    failures = []

    if "moods_include" in expect:
        if not any(m in prefs["moods"] for m in expect["moods_include"]):
            failures.append(f"expected at least one mood of {expect['moods_include']}, got {prefs['moods']}")
    if "genres_include" in expect:
        if not any(g in prefs["genres"] for g in expect["genres_include"]):
            failures.append(f"expected at least one genre of {expect['genres_include']}, got {prefs['genres']}")
    if "themes_include" in expect:
        if not any(t in prefs["themes"] for t in expect["themes_include"]):
            failures.append(f"expected at least one theme of {expect['themes_include']}, got {prefs['themes']}")
    if "excluded_genres_include" in expect:
        if not includes_all(prefs["excluded_genres"], expect["excluded_genres_include"]):
            failures.append(f"expected excluded_genres to contain {expect['excluded_genres_include']}, got {prefs['excluded_genres']}")
    if "excluded_moods_include" in expect:
        if not includes_all(prefs["excluded_moods"], expect["excluded_moods_include"]):
            failures.append(f"expected excluded_moods to contain {expect['excluded_moods_include']}, got {prefs['excluded_moods']}")
    if "runtime_max" in expect and prefs["runtime"]["max"] != expect["runtime_max"]:
        failures.append(f"expected runtime.max={expect['runtime_max']}, got {prefs['runtime']['max']}")
    if "runtime_min" in expect and prefs["runtime"]["min"] != expect["runtime_min"]:
        failures.append(f"expected runtime.min={expect['runtime_min']}, got {prefs['runtime']['min']}")
    if "runtime_target" in expect and prefs["runtime"]["target"] != expect["runtime_target"]:
        failures.append(f"expected runtime.target={expect['runtime_target']}, got {prefs['runtime']['target']}")
    if expect.get("has_release_year") and not prefs["release_year"]:
        failures.append("expected a release year constraint, got none")
    if expect.get("free_text_not_empty") and not prefs["free_text_keywords"]:
        failures.append("expected non-empty free_text_keywords fallback, got []")

    # Sanity: ranked results should never include an excluded genre/mood/theme.
    leak = next(
        (r for r in ranked["results"] if any(g in r["movie"]["genres"] for g in prefs["excluded_genres"])),
        None,
    )
    if leak:
        failures.append(f'hard-filter leak: "{leak["movie"]["title"]}" has excluded genre but appeared in results')

    return len(failures) == 0, failures


def suggest_improvement(test_case, failures):
    if not failures:
        return ""
    if test_case["difficulty"] == "taglish" and any("excluded_genres" in f for f in failures):
        return "Add a Tagalog/Taglish negation cue list (e.g. 'walang', 'huwag') to negation.py NEGATION_CUES."
    if any("runtime" in f for f in failures):
        return "Review runtime.py regex ordering/precedence for this phrasing; consider adding an explicit pattern."
    if any(("mood" in f or "genre" in f or "theme" in f) for f in failures):
        return "Add missing surface-form synonym(s) to data/synonyms.py for the word(s) used in this input."
    return "Investigate extraction logic for this phrasing and add a regression test once fixed."


def main():
    rows = []
    pass_count = 0

    for test_case in TEST_CASES:
        prefs = extract_preferences(test_case["input"])
        ranked = rank_movies(MOVIES, prefs, TFIDF_MODEL)
        ok, failures = evaluate(test_case["expect"], prefs, ranked)

        if ok:
            pass_count += 1

        top_result = (
            f'{ranked["results"][0]["movie"]["title"]} ({ranked["results"][0]["percent"]}%)'
            if ranked["results"] else "(no results)"
        )

        rows.append({
            "id": test_case["id"],
            "difficulty": test_case["difficulty"],
            "input": test_case["input"],
            "extracted": {
                "moods": prefs["moods"], "genres": prefs["genres"], "themes": prefs["themes"],
                "excluded_genres": prefs["excluded_genres"], "excluded_moods": prefs["excluded_moods"],
                "runtime": prefs["runtime"], "release_year": prefs["release_year"],
                "free_text_keywords": prefs["free_text_keywords"],
            },
            "top_result": top_result,
            "result_count": len(ranked["results"]),
            "pass": ok,
            "failures": failures,
            "improvement": suggest_improvement(test_case, failures),
        })

    # ---- Console report ----
    print(f"\nMovie Finder NLP Test Report — {pass_count}/{len(TEST_CASES)} passed\n")
    for row in rows:
        status = "PASS" if row["pass"] else "FAIL"
        print(f'[{status}] {row["id"]} ({row["difficulty"]}): "{row["input"]}"')
        print(f'       extracted: {json.dumps(row["extracted"])}')
        print(f'       top result: {row["top_result"]} | total matches: {row["result_count"]}')
        if not row["pass"]:
            print(f'       reason(s): {"; ".join(row["failures"])}')
            print(f'       improvement: {row["improvement"]}')
        print("")

    # ---- Markdown report file ----
    lines = [
        "# Movie Finder — NLP Test Report", "",
        f"**Result: {pass_count}/{len(TEST_CASES)} passed**", "",
        "| ID | Difficulty | Input | Extracted (summary) | Top Result | Pass/Fail | Reason for Failure | Improvement |",
        "|----|-----------|-------|----------------------|------------|-----------|---------------------|--------------|",
    ]
    for r in rows:
        e = r["extracted"]
        summary_parts = []
        if e["genres"]:
            summary_parts.append(f'genres:{"/".join(e["genres"])}')
        if e["moods"]:
            summary_parts.append(f'moods:{"/".join(e["moods"])}')
        if e["excluded_genres"]:
            summary_parts.append(f'-genres:{"/".join(e["excluded_genres"])}')
        if e["excluded_moods"]:
            summary_parts.append(f'-moods:{"/".join(e["excluded_moods"])}')
        if e["runtime"]["max"] or e["runtime"]["min"] or e["runtime"]["target"]:
            summary_parts.append(f'runtime:{json.dumps(e["runtime"])}')
        summary = "; ".join(summary_parts) or "(none)"

        lines.append(
            f'| {r["id"]} | {r["difficulty"]} | {r["input"].replace("|", chr(92) + "|")} | {summary} | '
            f'{r["top_result"]} | {"PASS" if r["pass"] else "FAIL"} | '
            f'{"; ".join(r["failures"]) or "—"} | {r["improvement"] or "—"} |'
        )

    report_path = os.path.join(os.path.dirname(__file__), "test_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Full report written to {report_path}")

    return 0 if pass_count == len(TEST_CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
