"""Tests for the optional FastAPI service (skipped when fastapi is not installed)."""

import pytest

pytest.importorskip("fastapi", reason="FastAPI extras not installed (requirements-api.txt)")

from fastapi.testclient import TestClient

import api


def _make_client() -> TestClient:
    return TestClient(api.create_app())


_SAMPLE_LOG = (
    "2025-04-06T14:22:07Z [ERROR] postgres-primary[18234]: connection pool exhausted\n"
    "ERROR Database connection timeout\n"
    "INFO Service restarted\n"
)


def test_analyze_endpoint_json_body(monkeypatch):
    monkeypatch.setenv("AI_INCIDENT_USE_STUB", "1")
    client = _make_client()
    resp = client.post("/analyze", json={"log_text": _SAMPLE_LOG})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["analysis"]["severity_level"] == "HIGH"
    assert payload["ticket"]["title"]
    assert "Incident Report" in payload["report_markdown"]


def test_analyze_endpoint_multipart_upload(monkeypatch):
    monkeypatch.setenv("AI_INCIDENT_USE_STUB", "1")
    client = _make_client()
    resp = client.post(
        "/analyze",
        files={"file": ("app.log", _SAMPLE_LOG.encode("utf-8"), "text/plain")},
    )
    assert resp.status_code == 200
    assert resp.json()["ticket"]["severity"] == "HIGH"


def test_analyze_endpoint_with_ticket_outcome_refinement(monkeypatch):
    monkeypatch.setenv("AI_INCIDENT_USE_STUB", "1")
    client = _make_client()
    resp = client.post(
        "/analyze",
        json={
            "log_text": _SAMPLE_LOG,
            "ticket_outcome": {
                "ticket_id": "INC-2042",
                "status": "resolved",
                "actual_root_cause": "Connection pool too small for traffic.",
            },
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["analysis"]["possible_root_cause"] == "Connection pool too small for traffic."
    assert payload["analysis"]["lessons_learned"]
    assert "INC-2042" in payload["ticket"]["lessons_learned"]


def test_analyze_endpoint_empty_log_returns_422(monkeypatch):
    monkeypatch.setenv("AI_INCIDENT_USE_STUB", "1")
    client = _make_client()
    resp = client.post("/analyze", json={"log_text": "   \n "})
    assert resp.status_code == 422
    assert "empty" in resp.json()["detail"]


def test_analyze_endpoint_bad_ticket_outcome_type_returns_422(monkeypatch):
    monkeypatch.setenv("AI_INCIDENT_USE_STUB", "1")
    client = _make_client()
    resp = client.post(
        "/analyze",
        json={"log_text": _SAMPLE_LOG, "ticket_outcome": "not-an-object"},
    )
    assert resp.status_code == 422
    assert "ticket_outcome" in resp.json()["detail"]
