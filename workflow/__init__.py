"""Agent-style workflows: ticket outcomes, refinement loop; see ``langgraph_incident`` for graphs."""

from workflow.feedback_loop import (
    TicketOutcome,
    load_ticket_outcome,
    refine_analysis_with_outcome,
)

__all__ = [
    "TicketOutcome",
    "load_ticket_outcome",
    "refine_analysis_with_outcome",
]
