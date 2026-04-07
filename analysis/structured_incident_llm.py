"""
Send parsed log data to an LLM and return a structured incident assessment.

Output fields: incident summary, possible root cause, severity level, and
recommended actions — as a dataclass and as JSON-serializable dicts.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ingestion.log_parser import LogEntry, entries_to_context, parse_logs


def _openai_api_key() -> str | None:
    raw = os.environ.get("OPENAI_API_KEY")
    if not raw:
        return None
    key = raw.strip()
    return key or None


def _call_openai_json(system: str, user: str) -> Dict[str, Any]:
    from openai import OpenAI

    key = _openai_api_key()
    if not key:
        raise ValueError("OPENAI_API_KEY is missing for OpenAI client.")
    client = OpenAI(api_key=key)
    resp = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
    )
    content = resp.choices[0].message.content or "{}"
    return json.loads(content)


def _normalize_root_cause(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(x).strip() for x in value if str(x).strip())
    return str(value).strip()


def _parse_confidence(value: Any) -> float:
    """Normalize model confidence to ``0.0``–``1.0``."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        x = float(value)
        if x > 1.0:
            x = x / 100.0
        return max(0.0, min(1.0, x))
    s = str(value).strip().rstrip("%")
    try:
        x = float(s)
        if x > 1.0:
            x = x / 100.0
        return max(0.0, min(1.0, x))
    except ValueError:
        return 0.0


def _parse_action_tier(value: Any) -> str:
    t = str(value or "").strip().upper()
    if t in ("P1", "P2", "P3", "P4"):
        return t
    return "P3"


@dataclass
class StructuredIncidentAnalysis:
    """Structured LLM output for log-based incident triage."""

    incident_summary: str
    possible_root_cause: str
    severity_level: str
    recommended_actions: List[str] = field(default_factory=list)
    confidence_score: float = 0.0
    action_tier: str = "P3"
    lessons_learned: str = ""
    raw_model_response: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable mapping with stable keys for downstream SOAR / ITSM."""
        return {
            "incident_summary": self.incident_summary,
            "possible_root_cause": self.possible_root_cause,
            "severity_level": self.severity_level,
            "recommended_actions": list(self.recommended_actions),
            "confidence_score": round(self.confidence_score, 4),
            "action_tier": self.action_tier,
            "lessons_learned": self.lessons_learned,
        }

    def to_json(self, *, indent: Optional[int] = 2, ensure_ascii: bool = False) -> str:
        """Serialize :meth:`to_dict` to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=ensure_ascii)


def _stub_analysis() -> StructuredIncidentAnalysis:
    return StructuredIncidentAnalysis(
        incident_summary=(
            "Multiple API errors and database connection limits observed in the same "
            "time window; payments dependency timing out."
        ),
        possible_root_cause=(
            "Database connection pool exhaustion leading to API 503s and circuit "
            "breaker activity; payments slowness may be a contributing factor."
        ),
        severity_level="HIGH",
        recommended_actions=[
            "Inspect DB max connections and app pool settings; reduce connection leaks.",
            "Check payments service latency and error rates.",
            "Review recent deploys and scale or fail over if needed.",
        ],
        confidence_score=0.78,
        action_tier="P2",
        lessons_learned="",
        raw_model_response="",
    )


_SYSTEM_PROMPT = (
    "You are an SRE assistant. Given parsed system log lines (and any organization "
    "context provided), assess whether there is an incident or notable risk. "
    "Reply with one JSON object only, using these exact keys: "
    "incident_summary (string), "
    "possible_root_cause (string — single concise hypothesis; if multiple, combine briefly), "
    "severity_level (one of: LOW, MEDIUM, HIGH, CRITICAL), "
    "recommended_actions (array of short actionable strings), "
    "confidence_score (number from 0 to 1 — your confidence in severity and root cause), "
    "action_tier (P1|P2|P3|P4 — P1 immediate all-hands / exec risk, P2 urgent engineering, "
    "P3 standard queue, P4 informational follow-up), "
    "lessons_learned (string; empty \"\" if none yet). "
    "If logs appear healthy, set severity_level to LOW, confidence_score high for that "
    "assessment, action_tier P4, and explain briefly in incident_summary. "
    "Ground claims in the logs and context; do not invent timestamps or systems not present."
)


def _entries_to_log_context(entries: List[LogEntry], max_lines: int) -> str:
    return entries_to_context(entries, max_lines=max_lines)


def _important_events_to_context(events: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for e in events:
        ts = e.get("timestamp") or ""
        lvl = (e.get("level") or "UNKNOWN").upper()
        comp = e.get("component") or ""
        msg = e.get("message") or e.get("raw_line") or ""
        parts = [p for p in (ts, f"[{lvl}]", f"{comp}:" if comp else "", msg) if p]
        lines.append(" ".join(parts).strip())
    return "\n".join(lines)


def _parse_llm_payload(data: Dict[str, Any]) -> StructuredIncidentAnalysis:
    raw = json.dumps(data)
    actions = data.get("recommended_actions")
    if not isinstance(actions, list):
        actions = [] if actions is None else [str(actions)]
    actions = [str(a).strip() for a in actions if str(a).strip()]
    sev = str(data.get("severity_level") or data.get("severity") or "UNKNOWN").upper()
    return StructuredIncidentAnalysis(
        incident_summary=str(data.get("incident_summary") or data.get("summary") or ""),
        possible_root_cause=_normalize_root_cause(
            data.get("possible_root_cause") or data.get("likely_causes")
        ),
        severity_level=sev,
        recommended_actions=actions,
        confidence_score=_parse_confidence(data.get("confidence_score")),
        action_tier=_parse_action_tier(data.get("action_tier")),
        lessons_learned=str(data.get("lessons_learned") or ""),
        raw_model_response=raw,
    )


def structured_incident_from_llm_dict(data: Dict[str, Any]) -> StructuredIncidentAnalysis:
    """Parse a model JSON object into :class:`StructuredIncidentAnalysis` (workflows, tests)."""
    return _parse_llm_payload(data)


def completion_json(system: str, user: str) -> Dict[str, Any]:
    """
    Run a single OpenAI chat completion with JSON object output.

    Used by workflow modules (e.g. feedback refinement). Requires ``OPENAI_API_KEY``.
    """
    return _call_openai_json(system, user)


def analyze_parsed_logs(
    entries: List[LogEntry],
    *,
    max_context_lines: int = 200,
    grounding_context: Optional[str] = None,
) -> StructuredIncidentAnalysis:
    """
    Send compact parsed log lines to the LLM and return structured fields.

    Parameters
    ----------
    entries
        Result of :func:`ingestion.log_parser.parse_logs`.
    max_context_lines
        Cap on lines sent in the prompt (keeps newest lines if truncated).
    grounding_context
        Optional runbook / retrieval text (RAG) to ground the model; kept separate
        from raw logs so callers can trace provenance.

    Returns
    -------
    StructuredIncidentAnalysis
        Summary, root cause hypothesis, severity, and actions.

    Environment
    -----------
    OPENAI_API_KEY
        Required unless ``AI_INCIDENT_USE_STUB`` is enabled.
    OPENAI_MODEL
        Optional override (default ``gpt-4o-mini``).
    AI_INCIDENT_USE_STUB
        If ``1`` / ``true`` / ``yes``, returns deterministic stub data (no API call).
    """
    stub = os.environ.get("AI_INCIDENT_USE_STUB", "").lower() in ("1", "true", "yes")
    if stub:
        return _stub_analysis()

    if not _openai_api_key():
        raise ValueError(
            "OPENAI_API_KEY is not set. Set it for live analysis, or "
            "AI_INCIDENT_USE_STUB=1 for offline output."
        )

    context = _entries_to_log_context(entries, max_context_lines)
    if grounding_context and grounding_context.strip():
        user = (
            "Organization knowledge (runbooks, past notes — use only to ground patterns; "
            "do not override facts visible in logs):\n\n"
            f"{grounding_context.strip()}\n\n"
            "---\n\n"
            f"Parsed log data:\n\n{context}"
        )
    else:
        user = f"Parsed log data:\n\n{context}"
    data = _call_openai_json(_SYSTEM_PROMPT, user)
    return _parse_llm_payload(data)


def analyze_log_file(
    path: str,
    *,
    encoding: str = "utf-8",
    max_context_lines: int = 200,
) -> StructuredIncidentAnalysis:
    """
    Read a log file, parse lines, and run :func:`analyze_parsed_logs`.
    """
    from pathlib import Path

    text = Path(path).read_text(encoding=encoding)
    return analyze_parsed_logs(parse_logs(text), max_context_lines=max_context_lines)


def analyze_event_extractor_payload(
    payload: Dict[str, Any],
    *,
    max_events: Optional[int] = None,
) -> StructuredIncidentAnalysis:
    """
    Analyze logs described by an event-extractor payload (e.g. from
    :func:`ingestion.event_extractor.build_events_payload`).

    Uses ``important_events`` if present; otherwise treats payload as a single-event
    list when it looks like one event dict.

    Parameters
    ----------
    payload
        Dict with key ``important_events`` (list of event dicts), or compatible shape.
    max_events
        If set, only the last *max_events* important events are sent (most recent tail).
    """
    events: List[Dict[str, Any]] = list(payload.get("important_events") or [])
    if not events and "level" in payload and (
        payload.get("message") is not None or payload.get("raw_line") is not None
    ):
        events = [payload]

    if max_events is not None and len(events) > max_events:
        events = events[-max_events:]

    stub = os.environ.get("AI_INCIDENT_USE_STUB", "").lower() in ("1", "true", "yes")
    if stub:
        return _stub_analysis()

    if not _openai_api_key():
        raise ValueError(
            "OPENAI_API_KEY is not set. Set it for live analysis, or "
            "AI_INCIDENT_USE_STUB=1 for offline output."
        )

    context = _important_events_to_context(events)
    meta = []
    if payload.get("source"):
        meta.append(f"source: {payload['source']}")
    if payload.get("total_entries") is not None:
        meta.append(f"total_entries: {payload['total_entries']}")
    header = "\n".join(meta) + "\n\n" if meta else ""
    user = f"{header}Important events:\n\n{context or '(no important events)'}"
    data = _call_openai_json(_SYSTEM_PROMPT, user)
    return _parse_llm_payload(data)


def analyze_parsed_logs_with_grounding(
    entries: List[LogEntry],
    grounding_context: str,
    **kwargs: Any,
) -> StructuredIncidentAnalysis:
    """Convenience wrapper: same as :func:`analyze_parsed_logs` with *grounding_context* set."""
    return analyze_parsed_logs(entries, grounding_context=grounding_context, **kwargs)
