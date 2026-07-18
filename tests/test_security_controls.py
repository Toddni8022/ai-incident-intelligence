from __future__ import annotations

from pathlib import Path

import pytest

from analysis.structured_incident_llm import structured_incident_from_llm_dict
from security_controls import (
    api_key_is_valid,
    enforce_log_limits,
    redact_sensitive_text,
    validate_runbook_dir,
)


def test_invalid_model_severity_is_normalized() -> None:
    analysis = structured_incident_from_llm_dict(
        {
            "incident_summary": "test",
            "possible_root_cause": "unknown",
            "severity_level": "SEVERE",
            "recommended_actions": [],
            "confidence_score": 88,
            "action_tier": "P9",
        }
    )
    assert analysis.severity_level == "MEDIUM"
    assert analysis.confidence_score == 0.88
    assert analysis.action_tier == "P3"


def test_sensitive_values_are_redacted() -> None:
    text = "email=user@example.com password=hunter2 Authorization: Bearer abcdefghijklmnop"
    redacted, count = redact_sensitive_text(text)
    assert "user@example.com" not in redacted
    assert "hunter2" not in redacted
    assert "abcdefghijklmnop" not in redacted
    assert count >= 3


def test_log_byte_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_INCIDENT_MAX_REQUEST_BYTES", "10")
    with pytest.raises(ValueError, match="byte limit"):
        enforce_log_limits("x" * 11)


def test_runbook_path_cannot_escape_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "runbooks"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("AI_INCIDENT_RUNBOOK_ROOT", str(root))

    assert validate_runbook_dir(".") == root.resolve()
    with pytest.raises(ValueError, match="outside"):
        validate_runbook_dir(str(outside))


def test_api_key_is_optional_but_enforced_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_INCIDENT_API_KEY", raising=False)
    assert api_key_is_valid(None)

    monkeypatch.setenv("AI_INCIDENT_API_KEY", "correct-key")
    assert api_key_is_valid("correct-key")
    assert not api_key_is_valid("wrong-key")
    assert not api_key_is_valid(None)
