"""Shared pipeline output type (avoids circular imports between main and workflow)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analysis.structured_incident_llm import StructuredIncidentAnalysis
    from tickets.incident_report_ticket import StructuredSupportTicket


@dataclass
class PipelineResult:
    """Outputs from :func:`main.run_pipeline` or :func:`workflow.langgraph_incident.run_langgraph_incident`."""

    report_markdown: str
    ticket: "StructuredSupportTicket"
    analysis: "StructuredIncidentAnalysis"
