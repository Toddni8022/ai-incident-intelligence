"""
AI Incident Intelligence — end-to-end CLI.

Runs the full pipeline (with optional **RAG** grounding and **ticket-outcome** refinement):

1. **Parse logs**
2. **Analyze incidents** (optional Chroma runbooks + optional post-ticket refinement)
3. **Generate report** (Markdown)
4. **Create support ticket** (structured fields + JSON)

Run from the project root::

    python main.py --log examples/sample_logs.txt

    # Ground with local runbooks (requires: pip install chromadb)
    python main.py --log examples/sample_logs.txt --runbook-dir examples/runbooks

    # Refine analysis using a resolved ticket (feedback loop)
    python main.py --log examples/sample_logs.txt --ticket-outcome examples/sample_ticket_outcome.json

    # Same pipeline via LangGraph (pip install -r requirements-langgraph.txt)
    python main.py --langgraph --log examples/sample_logs.txt

    # Serve the pipeline over HTTP (pip install -r requirements-api.txt)
    python main.py --serve --host 127.0.0.1 --port 8000

Offline demo::

    $env:AI_INCIDENT_USE_STUB="1"
    python main.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from pipeline_result import PipelineResult


def _read_log_path(path: Path) -> str:
    """Read UTF-8 text from *path*; exit with a clear message on failure."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise SystemExit(f"Cannot read log file {path}: {e}") from e


def _ensure_parent_dir(path: Path) -> None:
    """Create the parent directory of *path* if needed; clear ValueError on failure."""
    parent = path.expanduser().resolve().parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot create output directory {parent}: {e}") from e


def run_pipeline(
    log_path: Path,
    *,
    max_context_lines: int = 200,
    max_report_log_lines: int = 80,
    runbook_dir: Optional[Path] = None,
    chroma_path: Optional[Path] = None,
    ticket_outcome_path: Optional[Path] = None,
) -> PipelineResult:
    """
    Parse logs → analyze (optional RAG + optional ticket refinement) → report → ticket.

    Parameters
    ----------
    log_path
        Log file path (shown as report ``source``).
    max_context_lines
        Lines sent to the LLM and used in refinement excerpt.
    max_report_log_lines
        Lines embedded in the Markdown report excerpt.
    runbook_dir
        If set, ingest ``*.md`` / ``*.txt`` into Chroma and retrieve snippets for grounding.
    chroma_path
        Chroma persistence directory (default: ``data/chroma_incidents``).
    ticket_outcome_path
        If set, load JSON outcome and run a second LLM pass to calibrate analysis.

    Returns
    -------
    PipelineResult
        Report Markdown, structured ticket, and final :class:`StructuredIncidentAnalysis`.
    """
    from analysis.structured_incident_llm import analyze_parsed_logs
    from ingestion.log_parser import entries_to_context, parse_logs
    from reporting.structured_incident_markdown import format_structured_incident_markdown
    from tickets.incident_report_ticket import incident_analysis_to_structured_ticket

    raw = _read_log_path(log_path)
    if not raw.strip():
        raise ValueError(f"Log file {log_path} is empty or contains only whitespace.")
    entries = parse_logs(raw)
    if not entries:
        raise ValueError(f"No log lines could be parsed from {log_path}.")

    grounding = ""
    if runbook_dir is not None:
        from grounding.rag_store import (
            build_log_query_for_retrieval,
            ingest_runbook_directory,
            is_rag_available,
            retrieve_grounding_context,
        )

        if not is_rag_available():
            print(
                "Warning: --runbook-dir set but chromadb is not installed. "
                "Run: pip install chromadb",
                file=sys.stderr,
            )
        else:
            persist = chroma_path or Path("data/chroma_incidents")
            try:
                n = ingest_runbook_directory(runbook_dir, persist)
                if n:
                    print(
                        f"Ingested {n} runbook file(s) → {persist.resolve()}",
                        file=sys.stderr,
                    )
            except OSError as e:
                print(f"Warning: runbook ingest failed: {e}", file=sys.stderr)
            q = build_log_query_for_retrieval(entries, max_lines=40)
            grounding = retrieve_grounding_context(q, persist, n_results=5)

    analysis = analyze_parsed_logs(
        entries,
        max_context_lines=max_context_lines,
        grounding_context=grounding or None,
    )

    if ticket_outcome_path is not None:
        from workflow.feedback_loop import load_ticket_outcome, refine_analysis_with_outcome

        excerpt = entries_to_context(entries, max_lines=max_context_lines)
        outcome = load_ticket_outcome(ticket_outcome_path)
        analysis = refine_analysis_with_outcome(analysis, outcome, excerpt)

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
    return PipelineResult(
        report_markdown=report_md,
        ticket=ticket,
        analysis=analysis,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description=(
            "Full pipeline: parse logs → analyze (optional RAG / ticket feedback) → "
            "Markdown report → support ticket."
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
        "--runbook-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Directory of .md/.txt runbooks to ingest into Chroma (optional RAG).",
    )
    parser.add_argument(
        "--chroma-path",
        type=Path,
        default=None,
        metavar="DIR",
        help="Chroma persistence path (default: data/chroma_incidents).",
    )
    parser.add_argument(
        "--ticket-outcome",
        type=Path,
        default=None,
        metavar="JSON",
        help="Ticket resolution JSON for second-pass refinement (feedback loop).",
    )
    parser.add_argument(
        "--langgraph",
        action="store_true",
        help=(
            "Run the same stages through a LangGraph workflow (ingest → analyze → "
            "stub ticket poll → refine). Requires requirements-langgraph.txt."
        ),
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
        help="Write support ticket fields as JSON.",
    )
    parser.add_argument(
        "--out-analysis-json",
        type=Path,
        default=None,
        help="Write structured analysis JSON (severity, confidence, action_tier, …).",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Do not print report/ticket to stdout (only write --out-* files).",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run the FastAPI service wrapper (api.py) instead of a one-shot analysis.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        metavar="ADDR",
        help="Bind address for --serve (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        metavar="N",
        help="Bind port for --serve (default: 8000).",
    )
    args = parser.parse_args(argv)

    if args.serve:
        try:
            from api import serve

            serve(host=args.host, port=args.port)
        except ImportError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0

    log_path = args.log if args.log is not None else args.log_file

    out_paths = [
        p
        for p in (args.out_report, args.out_ticket, args.out_ticket_json, args.out_analysis_json)
        if p is not None
    ]

    try:
        for out_path in out_paths:
            _ensure_parent_dir(out_path)
        if args.langgraph:
            from workflow.langgraph_incident import run_langgraph_incident

            result = run_langgraph_incident(
                log_path,
                max_context_lines=args.max_llm_lines,
                max_report_log_lines=args.max_report_log_lines,
                runbook_dir=args.runbook_dir,
                chroma_path=args.chroma_path,
                ticket_outcome_path=args.ticket_outcome,
            )
        else:
            result = run_pipeline(
                log_path,
                max_context_lines=args.max_llm_lines,
                max_report_log_lines=args.max_report_log_lines,
                runbook_dir=args.runbook_dir,
                chroma_path=args.chroma_path,
                ticket_outcome_path=args.ticket_outcome,
            )

        if args.out_report:
            args.out_report.write_text(result.report_markdown, encoding="utf-8")
        if args.out_ticket:
            args.out_ticket.write_text(result.ticket.format_text(), encoding="utf-8")
        if args.out_ticket_json:
            args.out_ticket_json.write_text(result.ticket.to_json(), encoding="utf-8")
        if args.out_analysis_json:
            args.out_analysis_json.write_text(
                result.analysis.to_json(),
                encoding="utf-8",
            )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    except ImportError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:  # unexpected failure: keep the CLI message clean
        print(f"Unexpected error ({type(e).__name__}): {e}", file=sys.stderr)
        return 2

    if not args.quiet:
        print(result.report_markdown)
        print("\n" + "=" * 60 + "\n")
        print(result.ticket.format_text())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
