"""
Build support-ticket fields (title, body, priority) suitable for ITSM tools.

Output is plain text / Markdown that can be pasted into Jira, ServiceNow, etc.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

from analysis.incident_analyzer import IncidentFinding


def _openai_api_key() -> str | None:
    raw = os.environ.get("OPENAI_API_KEY")
    if not raw:
        return None
    key = raw.strip()
    return key or None


@dataclass
class SupportTicket:
    """Fields for a generic support or service desk ticket."""

    title: str
    description: str
    priority: str
    category: str = "Incident"
    raw_model_response: str = ""

    def format_text(self) -> str:
        """Render ticket as a single copy-paste friendly block."""
        return (
            f"Title: {self.title}\n"
            f"Priority: {self.priority}\n"
            f"Category: {self.category}\n"
            f"\n---\n\n{self.description}\n"
        )


def _ticket_from_finding(finding: IncidentFinding, report_markdown: str) -> SupportTicket:
    """Heuristic ticket without LLM."""
    title = f"[{finding.severity}] {finding.summary[:120]}"
    if len(finding.summary) > 120:
        title = title.rstrip() + "…"
    sev = finding.severity.upper()
    prio = {"CRITICAL": "P1", "HIGH": "P2", "MEDIUM": "P3", "LOW": "P4"}.get(
        sev, "P3"
    )
    body = (
        "## Summary\n\n"
        f"{finding.summary}\n\n"
        "## Details from incident report\n\n"
        f"{report_markdown[:4000]}"
    )
    if len(report_markdown) > 4000:
        body += "\n\n_(report truncated for ticket)_"
    return SupportTicket(title=title, description=body, priority=prio)


def generate_support_ticket(
    finding: IncidentFinding,
    report_markdown: str,
    *,
    use_llm: bool = True,
) -> SupportTicket:
    """
    Create ticket title, description, and priority from analysis and report.

    Parameters
    ----------
    finding
        Structured incident analysis.
    report_markdown
        Full incident report text (Markdown).
    use_llm
        If ``True`` and API is available (and not stub), ask the model for
        concise title, P1–P4-style priority mapping, and a short description.

    Returns
    -------
    SupportTicket
        Ticket fields ready to paste into a tracker.

    Environment
    -----------
    ``OPENAI_API_KEY``, ``OPENAI_MODEL``, ``AI_INCIDENT_USE_STUB``.
    """
    stub = os.environ.get("AI_INCIDENT_USE_STUB", "").lower() in ("1", "true", "yes")
    if stub or not use_llm:
        return _ticket_from_finding(finding, report_markdown)

    if not _openai_api_key():
        return _ticket_from_finding(finding, report_markdown)

    from openai import OpenAI

    system = (
        "You output JSON only. Keys: title (short string), description (Markdown "
        "string for support staff), priority (P1|P2|P3|P4), category (string). "
        "Map CRITICAL/HIGH to P1 or P2, MEDIUM to P3, LOW to P4 when appropriate."
    )
    user = (
        "Incident analysis JSON:\n"
        f"{json.dumps(finding.to_dict(), indent=2)}\n\n"
        "Incident report (may be long):\n"
        f"{report_markdown[:6000]}"
    )
    client = OpenAI(api_key=_openai_api_key())
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
    data = json.loads(content)
    fallback = _ticket_from_finding(finding, report_markdown)
    desc = str(data.get("description") or "").strip()
    if not desc:
        desc = fallback.description
    title = str(data.get("title") or "").strip() or fallback.title
    return SupportTicket(
        title=title,
        description=desc,
        priority=str(data.get("priority") or fallback.priority),
        category=str(data.get("category") or fallback.category),
        raw_model_response=content,
    )
