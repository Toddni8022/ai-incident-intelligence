"""
AI Incident Intelligence — end-to-end CLI.

Runs the full pipeline:

1. **Parse logs** — structured :class:`~ingestion.log_parser.LogEntry` rows
2. **Analyze incidents** — LLM → :class:`~analysis.structured_incident_llm.StructuredIncidentAnalysis`
3. **Generate report** — Markdown via :mod:`reporting.structured_incident_markdown`
4. **Create support ticket** — :class:`~tickets.incident_report_ticket.StructuredSupportTicket`

Run from the project root::

    python main.py examples/sample_logs.txt
    python main.py --log examples/sample_logs.txt

Offline demo (no API key)::

    $env:AI_INCIDENT_USE_STUB="1"   # PowerShell
    python main.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _read_log_path(path: Path) -> str:
    """Read UTF-8 text from *path*; exit with a clear message on failure."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise SystemExit(f"Cannot read log file {path}: {e}") from e


def run_pipeline(
    log_path: Path,
    *,
    max_context_lines: int = 200,
    max_report_log_lines: int = 80,
) -> tuple[str, "StructuredSupportTicket"]:
    """
    Execute parse → analyze → Markdown report → structured ticket.

    Parameters
    ----------
    log_path
        Path to the log file (used as ``source`` metadata in the report).
    max_context_lines
        Max log lines sent to the incident LLM.
    max_report_log_lines
        Max log lines embedded in the report excerpt.

    Returns
    -------
    tuple[str, StructuredSupportTicket]
        ``(report_markdown, ticket)``.
    """
    from analysis.structured_incident_llm import analyze_parsed_logs
    from ingestion.log_parser import parse_logs
    from reporting.structured_incident_markdown import format_structured_incident_markdown
    from tickets.incident_report_ticket import incident_analysis_to_structured_ticket

    raw = _read_log_path(log_path)
    entries = parse_logs(raw)
    if not entries:
        raise ValueError("No log lines parsed.")

    analysis = analyze_parsed_logs(entries, max_context_lines=max_context_lines)
    source = str(log_path.resolve())
    report_md = format_structured_incident_markdown(
        analysis,
        source=source,
        log_entries=entries,
        max_log_lines=max_report_log_lines,
    )
    ticket = incident_analysis_to_structured_ticket(
        analysis,
        report_markdown=report_md,
    )
    return report_md, ticket


def main(argv: list[str] | None = None) -> int:
    """
    Parse CLI arguments, run the full pipeline, print report and ticket to stdout.

    Parameters
    ----------
    argv
        Optional argument list (defaults to ``sys.argv[1:]``).

    Returns
    -------
    int
        Process exit code (0 on success).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Full pipeline: parse logs → analyze incidents → Markdown report → "
            "support ticket."
        ),
    )
    parser.add_argument(
        "log_file",
        nargs="?",
        type=Path,
        default=Path("examples/sample_logs.txt"),
        help="Path to a log file (default: examples/sample_logs.txt). Ignored if --log is set.",
    )
    parser.add_argument(
        "--log",
        "-l",
        type=Path,
        default=None,
        metavar="PATH",
        help="Log file path (overrides positional log_file when provided).",
    )
    parser.add_argument(
        "--max-llm-lines",
        type=int,
        default=200,
        metavar="N",
        help="Max log lines to include in the LLM analysis prompt (default: 200).",
    )
    parser.add_argument(
        "--max-report-log-lines",
        type=int,
        default=80,
        metavar="N",
        help="Max log lines in the report excerpt (default: 80).",
    )
    parser.add_argument(
        "--out-report",
        type=Path,
        default=None,
        help="Write incident report Markdown to this file.",
    )
    parser.add_argument(
        "--out-ticket",
        type=Path,
        default=None,
        help="Write support ticket text to this file.",
    )
    parser.add_argument(
        "--out-ticket-json",
        type=Path,
        default=None,
        help="Write support ticket fields as JSON to this file.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Do not print report/ticket to stdout (only write --out-* files).",
    )
    args = parser.parse_args(argv)
    log_path = args.log if args.log is not None else args.log_file

    try:
        report_md, ticket = run_pipeline(
            log_path,
            max_context_lines=args.max_llm_lines,
            max_report_log_lines=args.max_report_log_lines,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.out_report:
        args.out_report.write_text(report_md, encoding="utf-8")
    if args.out_ticket:
        args.out_ticket.write_text(ticket.format_text(), encoding="utf-8")
    if args.out_ticket_json:
        args.out_ticket_json.write_text(ticket.to_json(), encoding="utf-8")

    if not args.quiet:
        print(report_md)
        print("\n" + "=" * 60 + "\n")
        print(ticket.format_text())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
