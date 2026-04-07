"""
Format structured AI incident analysis into a Markdown incident report.

Primary input is :class:`analysis.structured_incident_llm.StructuredIncidentAnalysis`
or a compatible ``dict`` (e.g. from :meth:`StructuredIncidentAnalysis.to_dict`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from analysis.structured_incident_llm import StructuredIncidentAnalysis
from ingestion.log_parser import LogEntry, entries_to_context


def _md_paragraph(text: str) -> str:
    t = (text or "").strip()
    return t if t else "_Not provided._"


def _md_bullet_list(items: List[str]) -> List[str]:
    out: List[str] = []
    cleaned = [str(x).strip() for x in items if str(x).strip()]
    if not cleaned:
        out.append("- _None listed._")
    else:
        for x in cleaned:
            out.append(f"- {x}")
    return out


def format_structured_incident_markdown(
    analysis: Union[StructuredIncidentAnalysis, Dict[str, Any]],
    *,
    title: str = "Incident Report",
    subtitle: Optional[str] = None,
    source: Optional[str] = None,
    generated_at: Optional[str] = None,
    log_entries: Optional[List[LogEntry]] = None,
    log_excerpt: Optional[str] = None,
    max_log_lines: int = 80,
    include_metadata_section: bool = True,
    include_raw_model_response: bool = False,
) -> str:
    """
    Turn structured AI output into a Markdown incident report.

    Parameters
    ----------
    analysis
        :class:`StructuredIncidentAnalysis` or dict with keys
        ``incident_summary``, ``possible_root_cause``, ``severity_level``,
        ``recommended_actions`` (list).
    title
        Top-level document heading (H1).
    subtitle
        Optional line directly under the H1.
    source
        Optional log file path or system name for the metadata block.
    generated_at
        ISO timestamp string; default is current UTC time if ``None``.
    log_entries
        Optional parsed logs; if set, a truncated excerpt is appended unless
        ``log_excerpt`` is provided.
    log_excerpt
        Raw text block to append under **Log excerpt** (overrides *log_entries*
        when both are set).
    max_log_lines
        When using *log_entries*, cap lines included in the excerpt.
    include_metadata_section
        Whether to emit **Report metadata** (source, generated time).
    include_raw_model_response
        If the analysis object has ``raw_model_response`` text, append it in a
        collapsible-friendly code block (for audit).

    Returns
    -------
    str
        Full Markdown document.
    """
    if isinstance(analysis, dict):
        summary = str(analysis.get("incident_summary") or "")
        root = str(analysis.get("possible_root_cause") or "")
        severity = str(analysis.get("severity_level") or "UNKNOWN").upper()
        actions = analysis.get("recommended_actions") or []
        if not isinstance(actions, list):
            actions = [str(actions)]
        raw_resp = str(analysis.get("raw_model_response") or "")
    else:
        summary = analysis.incident_summary
        root = analysis.possible_root_cause
        severity = analysis.severity_level.upper()
        actions = list(analysis.recommended_actions)
        raw_resp = analysis.raw_model_response

    when = generated_at
    if when is None:
        when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    excerpt_text: Optional[str] = None
    if log_excerpt is not None:
        excerpt_text = log_excerpt.strip() or None
    elif log_entries is not None:
        excerpt_text = entries_to_context(log_entries, max_lines=max_log_lines)

    lines: List[str] = [f"# {title}", ""]
    if subtitle:
        lines.extend([subtitle.strip(), ""])

    if include_metadata_section:
        lines.extend(
            [
                "## Report metadata",
                "",
                f"- **Generated:** {when}",
            ]
        )
        if source:
            lines.append(f"- **Source:** {source}")
        lines.append("")

    lines.extend(
        [
            "## Incident summary",
            "",
            _md_paragraph(summary),
            "",
            "## Severity",
            "",
            f"**{severity}**",
            "",
            "## Possible root cause",
            "",
            _md_paragraph(root),
            "",
            "## Recommended actions",
            "",
        ]
    )
    lines.extend(_md_bullet_list(actions))
    lines.append("")

    if excerpt_text:
        lines.extend(
            [
                "## Log excerpt (truncated)",
                "",
                "```",
                excerpt_text,
                "```",
                "",
            ]
        )

    if include_raw_model_response and raw_resp.strip():
        lines.extend(
            [
                "## Raw AI structured response",
                "",
                "```json",
                raw_resp.strip(),
                "```",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def format_ai_output_to_markdown(
    analysis: Union[StructuredIncidentAnalysis, Dict[str, Any]],
    **kwargs: Any,
) -> str:
    """
    Alias for :func:`format_structured_incident_markdown` for callers that prefer
    "AI output" naming.
    """
    return format_structured_incident_markdown(analysis, **kwargs)
