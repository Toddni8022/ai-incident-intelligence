from pathlib import Path

from analysis.structured_incident_llm import structured_incident_from_llm_dict
from ingestion.log_parser import entries_to_context, parse_log_line, parse_logs
from main import run_pipeline


def test_parser_preserves_timestamp_level_and_component():
    entry = parse_log_line(
        "2025-04-06T14:22:08Z [ERROR] api-gateway: request failed status=503"
    )
    assert entry is not None
    assert entry.timestamp == "2025-04-06T14:22:08Z"
    assert entry.level == "ERROR"
    assert entry.component == "api-gateway"
    assert "status=503" in entry.message


def test_context_keeps_most_recent_lines():
    entries = parse_logs("INFO first\nWARN second\nERROR third")
    context = entries_to_context(entries, max_lines=2)
    assert "first" not in context
    assert "second" in context
    assert "third" in context


def test_model_payload_is_normalized_and_bounded():
    result = structured_incident_from_llm_dict(
        {
            "summary": "Database pressure",
            "likely_causes": ["pool exhausted", "connection leak"],
            "severity": "high",
            "recommended_actions": "Inspect pool",
            "confidence_score": "125%",
            "action_tier": "invalid",
        }
    )
    assert result.severity_level == "HIGH"
    assert result.confidence_score == 1.0
    assert result.action_tier == "P3"
    assert result.recommended_actions == ["Inspect pool"]


def test_offline_pipeline_produces_report_and_ticket(monkeypatch):
    monkeypatch.setenv("AI_INCIDENT_USE_STUB", "1")
    result = run_pipeline(Path("examples/sample_logs.txt"))
    assert result.analysis.severity_level == "HIGH"
    assert "Incident Report" in result.report_markdown
    assert result.ticket.title
