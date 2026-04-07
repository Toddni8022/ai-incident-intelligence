"""
Feedback loop: incorporate ticket resolution / closure into refined analysis.

This is a deliberate **integration seam** for PagerDuty, Jira, ServiceNow, or Slack:
poll or webhook ticket status, then call :func:`refine_analysis_with_outcome`.

A full LangGraph / CrewAI graph can wrap these same functions as nodes; the logic
here stays dependency-light and testable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

from analysis.structured_incident_llm import (
    StructuredIncidentAnalysis,
    completion_json,
    structured_incident_from_llm_dict,
)


@dataclass
class TicketOutcome:
    """Structured ticket lifecycle signal (file, webhook payload, or API response)."""

    status: str
    ticket_id: str = ""
    resolution_notes: str = ""
    actual_root_cause: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "status": self.status,
            "resolution_notes": self.resolution_notes,
            "actual_root_cause": self.actual_root_cause,
        }


def load_ticket_outcome(path: Union[str, Path]) -> TicketOutcome:
    """Load :class:`TicketOutcome` from a JSON file."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    return TicketOutcome(
        ticket_id=str(data.get("ticket_id") or ""),
        status=str(data.get("status") or "unknown"),
        resolution_notes=str(data.get("resolution_notes") or ""),
        actual_root_cause=str(data.get("actual_root_cause") or ""),
    )


_REFINE_SYSTEM = (
    "You are an SRE doing post-incident calibration. Given the prior JSON analysis, "
    "the original log excerpt, and the ticket outcome, return ONE new JSON object "
    "with the SAME keys as the initial incident schema: "
    "incident_summary, possible_root_cause, severity_level, recommended_actions, "
    "confidence_score, action_tier, lessons_learned. "
    "Update fields to reflect what was learned from resolution (align possible_root_cause "
    "with actual_root_cause when credible). "
    "lessons_learned must be a concise bullet-style paragraph (non-empty) describing "
    "what to watch for next time. "
    "Adjust confidence_score upward if the outcome confirms the hypothesis, downward if contradicted."
)


def refine_analysis_with_outcome(
    prior: StructuredIncidentAnalysis,
    outcome: TicketOutcome,
    log_excerpt: str,
) -> StructuredIncidentAnalysis:
    """
    Second-pass LLM: merge ticket closure data into an updated structured analysis.

    Parameters
    ----------
    prior
        Result of :func:`analysis.structured_incident_llm.analyze_parsed_logs`.
    outcome
        Ticket fields from ITSM or a saved JSON file.
    log_excerpt
        Compact log lines (same style as sent to the first model pass).

    Environment
    -----------
    AI_INCIDENT_USE_STUB
        If enabled, returns a deterministic refined analysis without API calls.
    OPENAI_API_KEY
        Required for live refinement when stub is off.
    """
    stub = os.environ.get("AI_INCIDENT_USE_STUB", "").lower() in ("1", "true", "yes")
    if stub:
        return StructuredIncidentAnalysis(
            incident_summary=prior.incident_summary,
            possible_root_cause=(
                outcome.actual_root_cause.strip()
                if outcome.actual_root_cause.strip()
                else prior.possible_root_cause
            ),
            severity_level=prior.severity_level,
            recommended_actions=list(prior.recommended_actions),
            confidence_score=min(0.95, prior.confidence_score + 0.07),
            action_tier="P3" if outcome.status.lower() in ("resolved", "closed") else prior.action_tier,
            lessons_learned=(
                "After closure: capture pool sizing and dependency SLOs in runbooks; "
                "add an alert when connection wait time exceeds baseline. "
                f"Ticket {outcome.ticket_id or 'N/A'} status={outcome.status}."
            ),
            raw_model_response=json.dumps({"stub_refine": True}),
        )

    user = (
        "Prior analysis JSON:\n"
        f"{json.dumps(prior.to_dict(), indent=2)}\n\n"
        "Ticket outcome:\n"
        f"{json.dumps(outcome.to_dict(), indent=2)}\n\n"
        "Log excerpt:\n"
        f"{log_excerpt}"
    )
    data = completion_json(_REFINE_SYSTEM, user)
    return structured_incident_from_llm_dict(data)
