"""Security and privacy controls shared by API and LLM workflows."""
from __future__ import annotations

import hmac
import os
import re
from pathlib import Path
from typing import Any

VALID_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
DEFAULT_MAX_REQUEST_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_LOG_LINES = 10_000

_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|passwd|pwd)\s*[:=]\s*[^\s,;]+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s]+"), "[REDACTED_CONNECTION_STRING]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[REDACTED_ACCOUNT_NUMBER]"),
)


def env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Read a positive integer environment variable with a safe fallback."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def normalize_severity(value: Any, *, fallback: str = "MEDIUM") -> str:
    """Return an allowlisted incident severity."""
    severity = str(value or "").strip().upper()
    return severity if severity in VALID_SEVERITIES else fallback


def redact_sensitive_text(text: str) -> tuple[str, int]:
    """Redact common secrets and personal identifiers from untrusted text."""
    result = text
    count = 0
    for pattern, replacement in _REDACTION_PATTERNS:
        result, replacements = pattern.subn(replacement, result)
        count += replacements
    return result, count


def enforce_log_limits(text: str) -> str:
    """Reject oversized log payloads before parsing or model submission."""
    max_bytes = env_int("AI_INCIDENT_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES)
    encoded_size = len(text.encode("utf-8"))
    if encoded_size > max_bytes:
        raise ValueError(f"Log payload exceeds the configured {max_bytes}-byte limit.")

    max_lines = env_int("AI_INCIDENT_MAX_LOG_LINES", DEFAULT_MAX_LOG_LINES)
    line_count = text.count("\n") + (1 if text else 0)
    if line_count > max_lines:
        raise ValueError(f"Log payload exceeds the configured {max_lines}-line limit.")
    return text


def validate_runbook_dir(value: str | None) -> Path | None:
    """Confine client-selected runbooks to AI_INCIDENT_RUNBOOK_ROOT."""
    if not value:
        return None
    root_raw = os.environ.get("AI_INCIDENT_RUNBOOK_ROOT")
    if not root_raw:
        raise ValueError(
            "Client-selected runbooks are disabled. Configure AI_INCIDENT_RUNBOOK_ROOT first."
        )
    root = Path(root_raw).expanduser().resolve()
    requested = Path(value).expanduser()
    candidate = requested.resolve() if requested.is_absolute() else (root / requested).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Requested runbook directory is outside the configured root.")
    if not candidate.is_dir():
        raise ValueError("Requested runbook directory does not exist or is not a directory.")
    return candidate


def api_key_is_valid(provided: str | None) -> bool:
    """Validate an optional service API key using constant-time comparison."""
    expected = os.environ.get("AI_INCIDENT_API_KEY", "").strip()
    if not expected:
        return True
    return bool(provided) and hmac.compare_digest(provided.strip(), expected)
