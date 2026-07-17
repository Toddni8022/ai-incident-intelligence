"""Offline tests for workflow.ticket_poller and the LangGraph poll-node wiring.

All HTTP is mocked via ``monkeypatch``; no real network access occurs.
"""

import json

import pytest

httpx = pytest.importorskip("httpx", reason="httpx not installed (requirements-api.txt)")

from workflow.langgraph_incident import poll_ticket_stub_node, refine_node
from workflow.ticket_poller import (
    is_terminal_status,
    poll_ticket_outcome,
    real_ticket_polling_enabled,
)


class _FakeResponse:
    """Minimal stand-in for ``httpx.Response``."""

    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


# ---------------------------------------------------------------------------
# Stub fallback (fully offline)
# ---------------------------------------------------------------------------


def test_stub_fallback_returns_sample_resolved_outcome(monkeypatch):
    monkeypatch.delenv("AI_INCIDENT_USE_STUB", raising=False)
    monkeypatch.delenv("AI_INCIDENT_TICKET_API_BASE", raising=False)
    outcome = poll_ticket_outcome("INC-1")
    assert isinstance(outcome, dict)
    assert outcome["status"] == "resolved"
    assert outcome["ticket_id"] == "INC-1"


def test_stub_mode_wins_over_explicit_base_url(monkeypatch):
    monkeypatch.setenv("AI_INCIDENT_USE_STUB", "1")

    def _boom(*args, **kwargs):
        raise AssertionError("network must not be touched in stub mode")

    monkeypatch.setattr(httpx, "get", _boom)
    outcome = poll_ticket_outcome("INC-2", base_url="https://itsm.example.com")
    assert outcome["status"].lower() in ("resolved", "closed", "done")


def test_is_terminal_status_is_case_insensitive():
    assert is_terminal_status("Resolved")
    assert is_terminal_status("CLOSED")
    assert is_terminal_status("done")
    assert not is_terminal_status("pending")
    assert not is_terminal_status(None)


def test_invalid_poll_params_raise_valueerror():
    with pytest.raises(ValueError, match="interval_s"):
        poll_ticket_outcome("INC-1", base_url="https://x.example.com", interval_s=0)
    with pytest.raises(ValueError, match="timeout_s"):
        poll_ticket_outcome("INC-1", base_url="https://x.example.com", timeout_s=0)


# ---------------------------------------------------------------------------
# Live polling (httpx mocked)
# ---------------------------------------------------------------------------


def test_poll_transitions_pending_to_resolved(monkeypatch):
    monkeypatch.delenv("AI_INCIDENT_USE_STUB", raising=False)
    payloads = [
        {"ticket_id": "INC-7", "status": "pending"},
        {"ticket_id": "INC-7", "status": "in_progress"},
        {"ticket_id": "INC-7", "status": "Resolved"},  # case-insensitive terminal
    ]
    calls = {"n": 0}

    def fake_get(url, *, headers=None, timeout=None):
        assert url == "https://itsm.example.com/tickets/INC-7"
        assert headers["Authorization"] == "Bearer secret"
        payload = payloads[min(calls["n"], len(payloads) - 1)]
        calls["n"] += 1
        return _FakeResponse(payload)

    monkeypatch.setattr(httpx, "get", fake_get)
    outcome = poll_ticket_outcome(
        "INC-7",
        base_url="https://itsm.example.com/",  # trailing slash tolerated
        api_key="secret",
        timeout_s=5.0,
        interval_s=0.01,
    )
    assert outcome["status"] == "Resolved"
    assert calls["n"] == 3


def test_poll_uses_env_config_when_args_omitted(monkeypatch):
    monkeypatch.delenv("AI_INCIDENT_USE_STUB", raising=False)
    monkeypatch.setenv("AI_INCIDENT_TICKET_API_BASE", "https://env-itsm.example.com")
    monkeypatch.setenv("AI_INCIDENT_TICKET_API_KEY", "envkey")
    seen = {}

    def fake_get(url, *, headers=None, timeout=None):
        seen["url"] = url
        seen["auth"] = headers["Authorization"]
        return _FakeResponse({"ticket_id": "INC-3", "status": "closed"})

    monkeypatch.setattr(httpx, "get", fake_get)
    outcome = poll_ticket_outcome("INC-3", timeout_s=1.0, interval_s=0.01)
    assert outcome["status"] == "closed"
    assert seen["url"] == "https://env-itsm.example.com/tickets/INC-3"
    assert seen["auth"] == "Bearer envkey"


def test_poll_times_out_when_status_never_terminal(monkeypatch):
    monkeypatch.delenv("AI_INCIDENT_USE_STUB", raising=False)
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse({"ticket_id": "INC-9", "status": "pending"})
    )
    with pytest.raises(TimeoutError, match="INC-9"):
        poll_ticket_outcome(
            "INC-9",
            base_url="https://itsm.example.com",
            timeout_s=0.2,
            interval_s=0.05,
        )


def test_poll_unreachable_raises_valueerror(monkeypatch):
    monkeypatch.delenv("AI_INCIDENT_USE_STUB", raising=False)

    def fake_get(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(ValueError, match="Cannot reach ticket API"):
        poll_ticket_outcome(
            "INC-4",
            base_url="https://itsm.example.com",
            timeout_s=1.0,
            interval_s=0.01,
        )


def test_poll_http_error_status_raises_valueerror(monkeypatch):
    monkeypatch.delenv("AI_INCIDENT_USE_STUB", raising=False)
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse({"detail": "oops"}, status_code=503)
    )
    with pytest.raises(ValueError, match="503"):
        poll_ticket_outcome(
            "INC-5",
            base_url="https://itsm.example.com",
            timeout_s=1.0,
            interval_s=0.01,
        )


def test_poll_missing_status_field_raises_valueerror(monkeypatch):
    monkeypatch.delenv("AI_INCIDENT_USE_STUB", raising=False)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse({"note": "hi"}))
    with pytest.raises(ValueError, match="'status'"):
        poll_ticket_outcome(
            "INC-6",
            base_url="https://itsm.example.com",
            timeout_s=1.0,
            interval_s=0.01,
        )


def test_poll_non_object_json_raises_valueerror(monkeypatch):
    monkeypatch.delenv("AI_INCIDENT_USE_STUB", raising=False)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(["resolved"]))
    with pytest.raises(ValueError, match="JSON object"):
        poll_ticket_outcome(
            "INC-8",
            base_url="https://itsm.example.com",
            timeout_s=1.0,
            interval_s=0.01,
        )


# ---------------------------------------------------------------------------
# LangGraph node wiring (nodes are plain functions; langgraph not required)
# ---------------------------------------------------------------------------


def test_real_ticket_polling_enabled_flag(monkeypatch):
    monkeypatch.delenv("AI_INCIDENT_USE_STUB", raising=False)
    monkeypatch.delenv("AI_INCIDENT_TICKET_API_BASE", raising=False)
    assert real_ticket_polling_enabled() is False
    monkeypatch.setenv("AI_INCIDENT_TICKET_API_BASE", "https://itsm.example.com")
    assert real_ticket_polling_enabled() is True
    monkeypatch.setenv("AI_INCIDENT_USE_STUB", "1")
    assert real_ticket_polling_enabled() is False


def test_poll_node_stub_mode_keeps_legacy_note(monkeypatch):
    monkeypatch.setenv("AI_INCIDENT_USE_STUB", "1")
    monkeypatch.setenv("AI_INCIDENT_TICKET_API_BASE", "https://itsm.example.com")
    out = poll_ticket_stub_node(
        {"ticket_outcome_file": "examples/sample_ticket_outcome.json"}
    )
    assert "Stub monitor" in out["poll_note"]
    assert "polled_ticket_outcome" not in out


def test_poll_node_real_polling_stores_outcome(monkeypatch):
    monkeypatch.delenv("AI_INCIDENT_USE_STUB", raising=False)
    monkeypatch.setenv("AI_INCIDENT_TICKET_API_BASE", "https://itsm.example.com")
    seen = {}

    def fake_get(url, *, headers=None, timeout=None):
        seen["url"] = url
        return _FakeResponse(
            {
                "ticket_id": "INC-2042",
                "status": "done",
                "actual_root_cause": "Pool sized correctly after hotfix.",
            }
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    # ticket id comes from the outcome file's "ticket_id" key (INC-2042)
    out = poll_ticket_stub_node(
        {"ticket_outcome_file": "examples/sample_ticket_outcome.json"}
    )
    assert out["polled_ticket_outcome"]["status"] == "done"
    assert "INC-2042" in out["poll_note"]
    assert seen["url"] == "https://itsm.example.com/tickets/INC-2042"


def test_poll_node_real_polling_uses_explicit_state_ticket_id(monkeypatch):
    monkeypatch.delenv("AI_INCIDENT_USE_STUB", raising=False)
    monkeypatch.setenv("AI_INCIDENT_TICKET_API_BASE", "https://itsm.example.com")
    seen = {}

    def fake_get(url, *, headers=None, timeout=None):
        seen["url"] = url
        return _FakeResponse({"ticket_id": "INC-55", "status": "resolved"})

    monkeypatch.setattr(httpx, "get", fake_get)
    out = poll_ticket_stub_node({"ticket_id": "INC-55"})
    assert out["polled_ticket_outcome"]["status"] == "resolved"
    assert seen["url"] == "https://itsm.example.com/tickets/INC-55"


def test_poll_node_real_polling_requires_ticket_id(monkeypatch):
    monkeypatch.delenv("AI_INCIDENT_USE_STUB", raising=False)
    monkeypatch.setenv("AI_INCIDENT_TICKET_API_BASE", "https://itsm.example.com")
    with pytest.raises(ValueError, match="ticket id"):
        poll_ticket_stub_node({})


def test_refine_node_prefers_polled_outcome_over_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_INCIDENT_USE_STUB", "1")
    from analysis.structured_incident_llm import analyze_parsed_logs
    from ingestion.log_parser import parse_logs

    entries = parse_logs("ERROR Database connection timeout\nINFO Service restarted")
    analysis = analyze_parsed_logs(entries)

    outcome_file = tmp_path / "outcome.json"
    outcome_file.write_text(
        json.dumps(
            {
                "ticket_id": "FILE-1",
                "status": "resolved",
                "actual_root_cause": "cause from file",
            }
        ),
        encoding="utf-8",
    )
    polled = {
        "ticket_id": "INC-9",
        "status": "done",
        "actual_root_cause": "cause from live poll",
        "resolution_notes": "verified in prod",
    }
    out = refine_node(
        {
            "entries": entries,
            "analysis": analysis,
            "max_context_lines": 50,
            "ticket_outcome_file": str(outcome_file),
            "polled_ticket_outcome": polled,
        }
    )
    refined = out["refined_analysis"]
    assert refined.possible_root_cause == "cause from live poll"
    assert "INC-9" in refined.lessons_learned


def test_refine_node_passthrough_without_any_outcome(monkeypatch):
    monkeypatch.setenv("AI_INCIDENT_USE_STUB", "1")
    sentinel = object()
    out = refine_node({"analysis": sentinel, "entries": []})
    assert out["refined_analysis"] is sentinel
