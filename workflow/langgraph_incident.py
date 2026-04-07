"""
LangGraph agent workflow: ingest → LLM analyze → stub ticket poll → refine.

Install optional deps::

    pip install -r requirements-langgraph.txt

Run via CLI::

    python main.py --langgraph --log examples/sample_logs.txt \\
        --ticket-outcome examples/sample_ticket_outcome.json

The ``poll_ticket_stub`` node is a **placeholder** for Jira / ServiceNow / PagerDuty
webhooks; swap its implementation without changing graph topology.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

from pipeline_result import PipelineResult


class IncidentGraphState(TypedDict, total=False):
    """State passed between LangGraph nodes (in-process; not checkpoint-serialized)."""

    log_file: str
    max_context_lines: int
    max_report_log_lines: int
    runbook_dir: NotRequired[str]
    chroma_path: NotRequired[str]
    ticket_outcome_file: NotRequired[str]
    entries: Any
    grounding: str
    analysis: Any
    poll_note: str
    refined_analysis: Any


def _require_langgraph():
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as e:
        raise ImportError(
            "LangGraph is not installed. Run: pip install -r requirements-langgraph.txt"
        ) from e
    return END, START, StateGraph


def ingest_node(state: IncidentGraphState) -> dict[str, Any]:
    """Parse logs; optional Chroma runbook ingest + retrieval."""
    from ingestion.log_parser import parse_logs

    log_path = Path(state["log_file"])
    text = log_path.read_text(encoding="utf-8")
    entries = parse_logs(text)
    if not entries:
        raise ValueError("No log lines parsed.")

    grounding = ""
    rd = state.get("runbook_dir")
    if rd:
        from grounding.rag_store import (
            build_log_query_for_retrieval,
            ingest_runbook_directory,
            is_rag_available,
            retrieve_grounding_context,
        )

        if not is_rag_available():
            print(
                "Warning: runbook_dir set but chromadb missing; continuing without RAG.",
                file=sys.stderr,
            )
        else:
            persist = Path(state.get("chroma_path") or "data/chroma_incidents")
            try:
                n = ingest_runbook_directory(Path(rd), persist)
                if n:
                    print(
                        f"[graph] Ingested {n} runbook file(s) → {persist.resolve()}",
                        file=sys.stderr,
                    )
            except OSError as e:
                print(f"[graph] Runbook ingest failed: {e}", file=sys.stderr)
            q = build_log_query_for_retrieval(entries, max_lines=40)
            grounding = retrieve_grounding_context(q, persist, n_results=5)

    return {"entries": entries, "grounding": grounding}


def analyze_node(state: IncidentGraphState) -> dict[str, Any]:
    """First LLM pass: structured incident JSON."""
    from analysis.structured_incident_llm import analyze_parsed_logs

    entries = state["entries"]
    g = state.get("grounding") or None
    if isinstance(g, str) and not g.strip():
        g = None
    n = int(state.get("max_context_lines") or 200)
    analysis = analyze_parsed_logs(
        entries,
        max_context_lines=n,
        grounding_context=g,
    )
    return {"analysis": analysis}


def poll_ticket_stub_node(state: IncidentGraphState) -> dict[str, Any]:
    """
    Stub integration point: in production, poll or await webhook for ticket status.

    Here we only record intent; actual outcome is still read from *ticket_outcome_file*
    in the next node (file-based demo).
    """
    p = state.get("ticket_outcome_file")
    if p:
        note = (
            "Stub monitor: would poll ITSM for closure (Jira/ServiceNow). "
            f"Demo uses static outcome file: {p}"
        )
    else:
        note = (
            "Stub monitor: no ticket linked; next node will pass-through without refinement."
        )
    return {"poll_note": note}


def refine_node(state: IncidentGraphState) -> dict[str, Any]:
    """Second LLM pass when a ticket outcome JSON path is provided."""
    from ingestion.log_parser import entries_to_context
    from workflow.feedback_loop import load_ticket_outcome, refine_analysis_with_outcome

    analysis = state["analysis"]
    p = state.get("ticket_outcome_file")
    if not p:
        return {"refined_analysis": analysis}

    excerpt = entries_to_context(
        state["entries"],
        max_lines=int(state.get("max_context_lines") or 200),
    )
    outcome = load_ticket_outcome(Path(p))
    refined = refine_analysis_with_outcome(analysis, outcome, excerpt)
    return {"refined_analysis": refined}


def build_incident_graph():
    """Compile the LangGraph workflow."""
    END, START, StateGraph = _require_langgraph()

    g = StateGraph(IncidentGraphState)
    g.add_node("ingest", ingest_node)
    g.add_node("analyze", analyze_node)
    g.add_node("poll_ticket_stub", poll_ticket_stub_node)
    g.add_node("refine", refine_node)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "analyze")
    g.add_edge("analyze", "poll_ticket_stub")
    g.add_edge("poll_ticket_stub", "refine")
    g.add_edge("refine", END)
    return g.compile()


def run_langgraph_incident(
    log_path: Path,
    *,
    max_context_lines: int = 200,
    max_report_log_lines: int = 80,
    runbook_dir: Path | None = None,
    chroma_path: Path | None = None,
    ticket_outcome_path: Path | None = None,
) -> PipelineResult:
    """
    Execute the LangGraph pipeline and return the same shape as :func:`main.run_pipeline`.

    Parameters
    ----------
    log_path
        Path to the log file.
    max_context_lines, max_report_log_lines
        Same semantics as the linear CLI pipeline.
    runbook_dir, chroma_path, ticket_outcome_path
        Optional RAG and ticket-outcome refinement.
    """
    from reporting.structured_incident_markdown import format_structured_incident_markdown
    from tickets.incident_report_ticket import incident_analysis_to_structured_ticket

    graph = build_incident_graph()
    init: IncidentGraphState = cast(
        IncidentGraphState,
        {
            "log_file": str(log_path.resolve()),
            "max_context_lines": max_context_lines,
            "max_report_log_lines": max_report_log_lines,
            "grounding": "",
        },
    )
    if runbook_dir is not None:
        init["runbook_dir"] = str(runbook_dir.resolve())
    if chroma_path is not None:
        init["chroma_path"] = str(chroma_path.resolve())
    if ticket_outcome_path is not None:
        init["ticket_outcome_file"] = str(ticket_outcome_path.resolve())

    final = graph.invoke(init)
    analysis = final.get("refined_analysis") or final.get("analysis")
    if analysis is None:
        raise RuntimeError("LangGraph finished without analysis")

    source = str(log_path.resolve())
    report_md = format_structured_incident_markdown(
        analysis,
        source=source,
        log_entries=final["entries"],
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


def main_cli() -> int:
    """``python -m workflow.langgraph_incident`` entry (minimal args)."""
    import argparse

    parser = argparse.ArgumentParser(description="LangGraph incident pipeline (demo).")
    parser.add_argument("--log", "-l", type=Path, required=True)
    parser.add_argument("--max-llm-lines", type=int, default=200)
    parser.add_argument("--max-report-log-lines", type=int, default=80)
    parser.add_argument("--runbook-dir", type=Path, default=None)
    parser.add_argument("--chroma-path", type=Path, default=None)
    parser.add_argument("--ticket-outcome", type=Path, default=None)
    args = parser.parse_args()
    r = run_langgraph_incident(
        args.log,
        max_context_lines=args.max_llm_lines,
        max_report_log_lines=args.max_report_log_lines,
        runbook_dir=args.runbook_dir,
        chroma_path=args.chroma_path,
        ticket_outcome_path=args.ticket_outcome,
    )
    print(r.report_markdown)
    print("\n" + "=" * 60 + "\n")
    print(r.ticket.format_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
