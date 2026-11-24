"""Privacy-aware local request tracing for the rule-based search pipeline."""

import json
import logging
from logging.handlers import RotatingFileHandler
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT_DIR / "data" / "search_quality.jsonl"
MAX_LOG_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
SEARCH_BUDGET_SECONDS = 8.0
_LOGGER_LOCK = threading.Lock()
_LOGGERS = {}

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")
_NAME_RE = re.compile(r"\b[A-Z][a-z]{1,}(?:\s+[A-Z][a-z]{1,}){1,2}\b")


def redact_query(value, limit=500):
    """Keep useful query text while removing common contact and name forms."""
    text = str(value or "")[:limit]
    text = _EMAIL_RE.sub("[REDACTED]", text)
    text = _IP_RE.sub("[REDACTED]", text)
    text = _PHONE_RE.sub("[REDACTED]", text)
    return _NAME_RE.sub("[REDACTED]", text)


class SearchTrace:
    def __init__(self, query, budget_seconds=SEARCH_BUDGET_SECONDS):
        self.started = time.perf_counter()
        self.deadline = self.started + budget_seconds
        self.query_text = redact_query(query)
        self.stage = "request"
        self.candidate_counts = {}
        self.errors = []
        self.fallbacks = []
        self.match_levels = []
        self.result_scores = []
        self.top_score = None
        self.zero_result = False
        self._budget_reported = False

    @property
    def elapsed_seconds(self):
        return time.perf_counter() - self.started

    @property
    def budget_exceeded(self):
        return time.perf_counter() > self.deadline

    def count(self, stage, value):
        try:
            self.candidate_counts[stage] = max(0, int(value))
        except (TypeError, ValueError, OverflowError):
            self.candidate_counts[stage] = 0

    def error(self, stage, error):
        self.stage = stage
        message = redact_query(str(error), 240)
        self.errors.append({
            "stage": stage,
            "exception_type": type(error).__name__,
            "message": message,
        })

    def fallback(self, name):
        if name not in self.fallbacks:
            self.fallbacks.append(name)

    def report_budget_exceeded(self, stage):
        if self._budget_reported:
            return
        self._budget_reported = True
        self.error(stage, TimeoutError("Cooperative search time budget exceeded"))

    def finish(self, status_code, context=None):
        context = context or {}
        results = context.get("results") or []
        self.match_levels = [
            str(item.get("match_tier") or item.get("match_level") or "unknown")
            for item in results if isinstance(item, dict)
        ]
        self.result_scores = [
            {
                "match_level": str(item.get("match_level") or "unknown"),
                "match_tier": str(item.get("match_tier") or "unknown"),
                "percent": item.get("percent"),
            }
            for item in results if isinstance(item, dict)
        ]
        scores = [
            item.get("percent")
            for item in results
            if isinstance(item, dict)
            and isinstance(item.get("percent"), (int, float))
        ]
        self.top_score = max(scores) if scores else None
        self.zero_result = bool(context.get("searched") and not context.get("result_count"))
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "query_text": self.query_text,
            "stage": self.stage,
            "exception_type": self.errors[-1]["exception_type"] if self.errors else None,
            "errors": self.errors,
            "candidate_counts": self.candidate_counts,
            "top_score": self.top_score,
            "match_levels": self.match_levels,
            "result_scores": self.result_scores,
            "zero_result": self.zero_result,
            "low_confidence": self.zero_result or self.top_score is None or self.top_score < 40,
            "fallback_used": bool(self.fallbacks),
            "fallbacks": self.fallbacks,
            "duration_ms": round(self.elapsed_seconds * 1000, 3),
            "status_code": int(status_code),
        }


def _logger_for(path):
    resolved = Path(path).resolve()
    key = str(resolved)
    with _LOGGER_LOCK:
        logger = _LOGGERS.get(key)
        if logger is not None:
            return logger
        resolved.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(f"movie_finder.search_quality.{abs(hash(key))}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = RotatingFileHandler(
            resolved, maxBytes=MAX_LOG_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        _LOGGERS[key] = logger
        return logger


def write_search_record(record, path=DEFAULT_LOG_PATH):
    try:
        _logger_for(path).info(json.dumps(record, ensure_ascii=False, allow_nan=False))
    except (OSError, TypeError, ValueError):
        logging.getLogger("movie_finder.search_quality").exception(
            "Unable to write the local search-quality record."
        )


def close_search_log(path):
    """Close a configured log handler, primarily for orderly shutdown and tests."""
    key = str(Path(path).resolve())
    with _LOGGER_LOCK:
        logger = _LOGGERS.pop(key, None)
        if logger is None:
            return
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.flush()
            handler.close()
