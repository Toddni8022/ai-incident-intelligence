"""
Generate a human-readable incident report (Markdown) from analysis results.

Uses the OpenAI API when configured; stub mode mirrors structured finding text.
"""

from __future__ import annotations

import os
from typing import List

from analysis.incident_analyzer import IncidentFinding
from ingestion.log_parser import LogEntry
from ingestion.log_parser import entries_to_context


def _openai_api_key() -> str | None:
    raw = os.environ.get("OPENAI_API_KEY")
    if not raw:
        return None
    key = raw.strip()
    return key or None


def _report_from_finding(finding: IncidentFinding, log_excerpt: str) -> str:
    """Build a Markdown report without calling an LLM."""
    lines = [
        "# Incident Report",
        "",
        "## Executive summary",
        finding.summary,
        "",
        "## Severity",
        finding.severity,
        "",
        "## Affected components",
    ]
    if finding.affected_components:
        for c in finding.affected_components:
            lines.append(f"- {c}")
    else:
        lines.append("- _None identified_")
    lines.extend(
        [
            "",
            "## Timeline",
            finding.timeline or "_Not specified_",
            "",
            "## Likely causes",
        ]
    )
    if finding.likely_causes:
        for x in finding.likely_causes:
            lines.append(f"- {x}")
    else:
        lines.append("- _None listed_")
    lines.extend(["", "## Recommended actions"])
    if finding.recommended_actions:
        for x in finding.recommended_actions:
            lines.append(f"- {x}")
    else:
        lines.append("- _None listed_")
    lines.extend(["", "## Log excerpt (truncated)", "", "```", log_excerpt, "```", ""])
    return "\n".join(lines)


def generate_incident_report(
    finding: IncidentFinding,
    entries: List[LogEntry],
    *,
    max_context_lines: int = 80,
    use_llm_polish: bool = True,
) -> str:
    """
    Produce a Markdown incident report from a finding and original logs.

    Parameters
    ----------
    finding
        Output of :func:`analysis.incident_analyzer.analyze_logs`.
    entries
        Parsed log entries (for an excerpt appendix).
    max_context_lines
        Max lines of logs to embed in the report appendix.
    use_llm_polish
        If ``True`` and API key is available (and not stub), ask the model to
        rewrite the report for clarity while preserving facts.

    Returns
    -------
    str
        Markdown document body.

    Environment
    -----------
    Same as analysis module: ``OPENAI_API_KEY``, ``OPENAI_MODEL``,
    ``AI_INCIDENT_USE_STUB``.
    """
    excerpt = entries_to_context(entries, max_lines=max_context_lines)
    base = _report_from_finding(finding, excerpt)

    stub = os.environ.get("AI_INCIDENT_USE_STUB", "").lower() in ("1", "true", "yes")
    if stub or not use_llm_polish:
        return base

    key = _openai_api_key()
    if not key:
        return base

    from openai import OpenAI

    client = OpenAI(api_key=key)
    system = (
        "You are an incident communications writer. Rewrite the following draft "
        "incident report in clear Markdown. Keep all factual claims consistent; "
        "do not invent new components or times. Improve headings and flow."
    )
    resp = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": base},
        ],
        temperature=0.3,
    )
    polished = (resp.choices[0].message.content or "").strip()
    return polished if polished else base
