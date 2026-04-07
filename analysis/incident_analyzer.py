"""
Analyze parsed logs with an LLM to surface incidents, severity, and actions.

Uses the OpenAI API when ``OPENAI_API_KEY`` is set; otherwise can use
``AI_INCIDENT_USE_STUB=1`` for deterministic demo output.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

from ingestion.log_parser import LogEntry
from ingestion.log_parser import entries_to_context


def _openai_api_key() -> str | None:
    """Return stripped ``OPENAI_API_KEY`` or ``None`` if unset."""
    raw = os.environ.get("OPENAI_API_KEY")
    if not raw:
        return None
    key = raw.strip()
    return key or None


@dataclass
class IncidentFinding:
    """Structured result of LLM log analysis."""

    summary: str
    severity: str
    affected_components: List[str] = field(default_factory=list)
    timeline: str = ""
    likely_causes: List[str] = field(default_factory=list)
    recommended_actions: List[str] = field(default_factory=list)
    raw_model_response: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-friendly dict."""
        return {
            "summary": self.summary,
            "severity": self.severity,
            "affected_components": self.affected_components,
            "timeline": self.timeline,
            "likely_causes": self.likely_causes,
            "recommended_actions": self.recommended_actions,
        }


def _stub_finding() -> IncidentFinding:
    """Return canned analysis for offline demos."""
    return IncidentFinding(
        summary=(
            "Elevated error rate in API and database layers; connection timeouts "
            "observed around the same window."
        ),
        severity="HIGH",
        affected_components=["api-gateway", "postgres-primary"],
        timeline="Errors cluster within a short window; recovery not visible in sample.",
        likely_causes=[
            "Database connection pool exhaustion or network blip",
            "Downstream dependency latency causing cascade failures",
        ],
        recommended_actions=[
            "Check DB connection counts and slow query log",
            "Review recent deploys and dependency health",
            "Scale or restart affected services if confirmed incident",
        ],
        raw_model_response="",
    )


def _call_openai_json(system: str, user: str) -> Dict[str, Any]:
    """Run a chat completion and parse JSON from the assistant message."""
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


def analyze_logs(
    entries: List[LogEntry],
    *,
    max_context_lines: int = 200,
) -> IncidentFinding:
    """
    Use an LLM to infer incidents, severity, and remediation hints from logs.

    Parameters
    ----------
    entries
        Output of :func:`ingestion.log_parser.parse_logs`.
    max_context_lines
        Truncate log context from the end if longer than this many lines.

    Returns
    -------
    IncidentFinding
        Structured analysis. Fields may be empty if the model returns partial data.

    Environment
    -----------
    OPENAI_API_KEY
        Required for live LLM calls (unless stub mode).
    OPENAI_MODEL
        Optional model name (default ``gpt-4o-mini``).
    AI_INCIDENT_USE_STUB
        If set to ``1`` or ``true``, returns :func:`_stub_finding` without API calls.
    """
    stub = os.environ.get("AI_INCIDENT_USE_STUB", "").lower() in ("1", "true", "yes")
    if stub:
        return _stub_finding()

    if not _openai_api_key():
        raise ValueError(
            "OPENAI_API_KEY is not set. Export it for live analysis, or set "
            "AI_INCIDENT_USE_STUB=1 for offline demo output."
        )

    context = entries_to_context(entries, max_lines=max_context_lines)
    system = (
        "You are an SRE assistant. Given system log lines, identify whether there "
        "is an incident or notable degradation. Respond with a single JSON object "
        "with keys: summary (string), severity (one of LOW, MEDIUM, HIGH, CRITICAL), "
        "affected_components (array of strings), timeline (string), "
        "likely_causes (array of strings), recommended_actions (array of strings). "
        "If logs look healthy, set severity to LOW and explain briefly in summary."
    )
    user = f"Log excerpt:\n\n{context}"

    data = _call_openai_json(system, user)
    raw = json.dumps(data)
    return IncidentFinding(
        summary=str(data.get("summary", "")),
        severity=str(data.get("severity", "UNKNOWN")).upper(),
        affected_components=list(data.get("affected_components") or []),
        timeline=str(data.get("timeline", "")),
        likely_causes=list(data.get("likely_causes") or []),
        recommended_actions=list(data.get("recommended_actions") or []),
        raw_model_response=raw,
    )
