import json
from pathlib import Path

import pytest

from ingestion.log_parser import parse_log_line
from main import run_pipeline
from workflow.feedback_loop import load_ticket_outcome


def test_malformed_ticket_outcome_json_raises_valueerror(tmp_path):
    bad = tmp_path / "outcome.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_ticket_outcome(bad)


def test_ticket_outcome_non_object_raises_valueerror(tmp_path):
    bad = tmp_path / "outcome.json"
    bad.write_text('["resolved"]', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_ticket_outcome(bad)


def test_ticket_outcome_missing_keys_fall_back_to_defaults(tmp_path):
    sparse = tmp_path / "outcome.json"
    sparse.write_text("{}", encoding="utf-8")
    outcome = load_ticket_outcome(sparse)
    assert outcome.status == "unknown"
    assert outcome.ticket_id == ""


def test_empty_log_file_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_INCIDENT_USE_STUB", "1")
    empty = tmp_path / "empty.log"
    empty.write_text("   \n\n\t\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty or contains only whitespace"):
        run_pipeline(empty)


def test_parser_syslog_component_with_pid():
    entry = parse_log_line(
        '2025-04-06T14:22:15Z [ERROR] postgres-primary[18234]: FATAL: too many connections for role "app_rw"'
    )
    assert entry is not None
    assert entry.timestamp == "2025-04-06T14:22:15Z"
    assert entry.level == "ERROR"
    assert entry.component == "postgres-primary"
    assert "[18234]" not in entry.message
    assert entry.message.startswith("FATAL: too many connections")


def test_parser_syslog_component_with_pid_no_timestamp():
    entry = parse_log_line("worker-indexer[42]: batch processed size=500")
    assert entry is not None
    assert entry.timestamp is None
    assert entry.level == "UNKNOWN"
    assert entry.component == "worker-indexer"
    assert entry.message == "batch processed size=500"


def test_parser_plain_level_message_lines():
    error = parse_log_line("ERROR Database connection timeout")
    assert error is not None
    assert error.level == "ERROR"
    assert error.component == ""
    assert error.message == "Database connection timeout"

    warning = parse_log_line("WARNING Connection pool exhausted")
    assert warning is not None
    assert warning.level == "WARNING"
    assert warning.message == "Connection pool exhausted"


def test_ticket_json_has_required_keys_and_types(monkeypatch):
    monkeypatch.setenv("AI_INCIDENT_USE_STUB", "1")
    result = run_pipeline(Path("examples/sample_logs.txt"))
    ticket = json.loads(result.ticket.to_json())
    assert set(ticket) == {
        "title",
        "description",
        "severity",
        "suggested_actions",
        "confidence_score",
        "action_tier",
        "lessons_learned",
    }
    assert isinstance(ticket["title"], str) and ticket["title"]
    assert isinstance(ticket["severity"], str)
    assert isinstance(ticket["suggested_actions"], list)
    assert all(isinstance(a, str) for a in ticket["suggested_actions"])
    assert 0.0 <= ticket["confidence_score"] <= 1.0
    assert ticket["action_tier"] in ("P1", "P2", "P3", "P4")
