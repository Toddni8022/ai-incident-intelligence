"""
Convert an incident report (Markdown and/or structured AI fields) into a support ticket.

Produces a fixed shape: title, description, severity, suggested actions — suitable
for JSON APIs and ITSM paste-in workflows.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from analysis.structured_incident_llm import StructuredIncidentAnalysis


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def _strip_md_bold(s: str) -> str:
    return (s or "").strip().strip("*").strip()


def _parse_canonical_incident_report_markdown(markdown: str) -> Dict[str, Any]:
    """
    Parse Markdown produced by :mod:`reporting.structured_incident_markdown`.

    Returns keys: ``incident_summary``, ``severity``, ``possible_root_cause``,
    ``recommended_actions`` (list of str), and optionally ``raw_body`` (full text).
    """
    result: Dict[str, Any] = {
        "incident_summary": "",
        "severity": "UNKNOWN",
        "possible_root_cause": "",
        "recommended_actions": [],
        "raw_body": markdown or "",
    }
    if not (markdown or "").strip():
        return result

    sections: Dict[str, List[str]] = {}
    current_key: Optional[str] = None

    for line in markdown.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            current_key = heading.lower().replace(" ", "_")
            sections.setdefault(current_key, [])
            continue
        if current_key is not None:
            sections[current_key].append(line)

    def _section_text(key: str) -> str:
        lines = sections.get(key, [])
        return "\n".join(lines).strip()

    summary = _section_text("incident_summary")
    if summary:
        result["incident_summary"] = summary

    sev_block = _section_text("severity")
    if sev_block:
        for ln in sev_block.splitlines():
            t = _strip_md_bold(ln)
            if t and t not in ("_",):
                result["severity"] = t.upper()
                break

    root = _section_text("possible_root_cause")
    if root:
        result["possible_root_cause"] = root

    actions_block = _section_text("recommended_actions")
    if actions_block:
        actions: List[str] = []
        for ln in actions_block.splitlines():
            m = re.match(r"^[\s]*[-*]\s+(.*)$", ln)
            if m:
                s = m.group(1).strip()
                if s and not s.startswith("_"):
                    actions.append(s)
        result["recommended_actions"] = actions

    return result


def _build_description_from_analysis(
    analysis: StructuredIncidentAnalysis,
    *,
    report_markdown: Optional[str],
) -> str:
    if report_markdown and report_markdown.strip():
        return report_markdown.strip()
    parts = [
        "## Incident summary",
        "",
        analysis.incident_summary.strip() or "_Not specified._",
        "",
        "## Possible root cause",
        "",
        analysis.possible_root_cause.strip() or "_Not specified._",
        "",
    ]
    return "\n".join(parts).strip()


def _build_title(severity: str, summary: str, *, max_len: int = 120) -> str:
    sev = (severity or "UNKNOWN").upper()
    core = _first_line(summary) or "Incident"
    if len(core) > max_len:
        core = core[: max_len - 1].rstrip() + "…"
    return f"[{sev}] {core}"


@dataclass
class StructuredSupportTicket:
    """Support ticket fields derived from an incident report."""

    title: str
    description: str
    severity: str
    suggested_actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable dict for APIs and tooling."""
        return {
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "suggested_actions": list(self.suggested_actions),
        }

    def to_json(self, *, indent: Optional[int] = 2, ensure_ascii: bool = False) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=ensure_ascii)

    def format_text(self) -> str:
        """Single block suitable for copy-paste into a ticket system."""
        actions = "\n".join(f"- {a}" for a in self.suggested_actions) or "- _None_"
        return (
            f"Title: {self.title}\n"
            f"Severity: {self.severity}\n"
            f"\n---\n\n"
            f"## Description\n\n{self.description}\n\n"
            f"## Suggested actions\n\n{actions}\n"
        )


def incident_analysis_to_structured_ticket(
    analysis: Union[StructuredIncidentAnalysis, Dict[str, Any]],
    *,
    report_markdown: Optional[str] = None,
    title_max_len: int = 120,
) -> StructuredSupportTicket:
    """
    Build a structured ticket from :class:`StructuredIncidentAnalysis` (or its dict).

    Parameters
    ----------
    analysis
        Structured LLM output or ``dict`` from :meth:`StructuredIncidentAnalysis.to_dict`.
    report_markdown
        Optional full incident report (Markdown). When provided, it becomes the
        ticket **description**; otherwise a short description is built from the analysis.
    title_max_len
        Maximum length of the summary portion of the title.

    Returns
    -------
    StructuredSupportTicket
        Ticket with title, description, severity, and suggested actions.
    """
    if isinstance(analysis, dict):
        summary = str(analysis.get("incident_summary") or "")
        root = str(analysis.get("possible_root_cause") or "")
        severity = str(analysis.get("severity_level") or "UNKNOWN").upper()
        actions = analysis.get("recommended_actions") or []
        if not isinstance(actions, list):
            actions = [str(actions)]
        actions = [str(a).strip() for a in actions if str(a).strip()]
        obj = StructuredIncidentAnalysis(
            incident_summary=summary,
            possible_root_cause=root,
            severity_level=severity,
            recommended_actions=actions,
        )
    else:
        obj = analysis

    sev = obj.severity_level.upper()
    title = _build_title(sev, obj.incident_summary, max_len=title_max_len)
    description = _build_description_from_analysis(obj, report_markdown=report_markdown)
    return StructuredSupportTicket(
        title=title,
        description=description,
        severity=sev,
        suggested_actions=list(obj.recommended_actions),
    )


def incident_report_markdown_to_structured_ticket(
    report_markdown: str,
    *,
    title_max_len: int = 120,
) -> StructuredSupportTicket:
    """
    Parse a canonical incident report (Markdown) into a structured ticket.

    Expects headings such as ``## Incident summary``, ``## Severity``,
    ``## Possible root cause``, and ``## Recommended actions`` as produced by
    :func:`reporting.structured_incident_markdown.format_structured_incident_markdown`.
    """
    parsed = _parse_canonical_incident_report_markdown(report_markdown)
    summary = str(parsed.get("incident_summary") or "")
    severity = str(parsed.get("severity") or "UNKNOWN").upper()
    actions = list(parsed.get("recommended_actions") or [])
    if not isinstance(actions, list):
        actions = []

    title = _build_title(severity, summary, max_len=title_max_len)
    description = (report_markdown or "").strip() or "_Empty report._"

    return StructuredSupportTicket(
        title=title,
        description=description,
        severity=severity,
        suggested_actions=actions,
    )


def incident_report_to_structured_ticket(
    *,
    report_markdown: str,
    analysis: Optional[Union[StructuredIncidentAnalysis, Dict[str, Any]]] = None,
    title_max_len: int = 120,
) -> StructuredSupportTicket:
    """
    Convert an incident report to a ticket, optionally merging structured analysis.

    When *analysis* is provided, **severity** prefers the analysis value if it is
    not ``UNKNOWN``; **suggested_actions** prefer the analysis list when non-empty;
    the **title** is rebuilt from the analysis summary when present, else from the
    parsed report.
    """
    ticket = incident_report_markdown_to_structured_ticket(
        report_markdown,
        title_max_len=title_max_len,
    )
    if analysis is None:
        return ticket

    parsed = _parse_canonical_incident_report_markdown(report_markdown)
    parsed_summary = str(parsed.get("incident_summary") or "")

    if isinstance(analysis, dict):
        a_sev = str(analysis.get("severity_level") or "UNKNOWN").upper()
        acts = analysis.get("recommended_actions") or []
        if not isinstance(acts, list):
            acts = [str(acts)]
        acts = [str(a).strip() for a in acts if str(a).strip()]
        a_summary = str(analysis.get("incident_summary") or "")
    else:
        a_sev = analysis.severity_level.upper()
        acts = list(analysis.recommended_actions)
        a_summary = analysis.incident_summary

    final_sev = a_sev if a_sev != "UNKNOWN" else ticket.severity
    final_actions = acts if acts else ticket.suggested_actions
    summary_for_title = _first_line(a_summary) or _first_line(parsed_summary) or "Incident"

    return StructuredSupportTicket(
        title=_build_title(final_sev, summary_for_title, max_len=title_max_len),
        description=ticket.description,
        severity=final_sev,
        suggested_actions=final_actions,
    )
