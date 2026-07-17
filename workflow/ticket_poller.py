"""
Ticket-outcome polling: real ITSM API integration with an offline stub fallback.

This module is the production half of the ``poll_ticket_stub`` seam in
:mod:`workflow.langgraph_incident`: point it at a Jira / ServiceNow / PagerDuty-style
HTTP API and the graph polls for ticket closure instead of reading a static file.

Configuration (environment)
---------------------------
AI_INCIDENT_TICKET_API_BASE
    Base URL of the ticket API, e.g. ``https://itsm.example.com``. Tickets are
    fetched as ``GET {base_url}/tickets/{ticket_id}``.
AI_INCIDENT_TICKET_API_KEY
    Optional bearer token sent as ``Authorization: Bearer <key>``.
AI_INCIDENT_USE_STUB
    When set (``1`` / ``true`` / ``yes``), no HTTP calls are made and a canned
    resolved outcome is returned (``examples/sample_ticket_outcome.json`` when
    present), keeping demos and tests fully offline.

``httpx`` is imported lazily inside :func:`poll_ticket_outcome`, so importing this
module never requires network-related dependencies.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

#: Ticket ``status`` values (compared case-insensitively) that end polling.
TERMINAL_STATUSES = frozenset({"resolved", "closed", "done"})

_SAMPLE_OUTCOME_PATH = (
    Path(__file__).resolve().parents[1] / "examples" / "sample_ticket_outcome.json"
)

#: Per-request HTTP timeout (seconds); independent of the overall poll deadline.
_REQUEST_TIMEOUT_S = 10.0


def _stub_mode_enabled() -> bool:
    """Return True when ``AI_INCIDENT_USE_STUB`` requests offline stub behavior."""
    return os.environ.get("AI_INCIDENT_USE_STUB", "").lower() in ("1", "true", "yes")


def _env_value(name: str) -> Optional[str]:
    """Return the stripped value of env var *name*, or None when unset/blank."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def real_ticket_polling_enabled() -> bool:
    """
    Return True when live polling should be attempted.

    Live polling requires a configured base URL (``AI_INCIDENT_TICKET_API_BASE``)
    and stub mode being off. Used by the LangGraph ``poll_ticket_stub`` node to
    decide between the legacy stub note and a real API poll.
    """
    return _env_value("AI_INCIDENT_TICKET_API_BASE") is not None and not _stub_mode_enabled()


def is_terminal_status(status: Any) -> bool:
    """Return True if *status* is one of :data:`TERMINAL_STATUSES` (case-insensitive)."""
    return isinstance(status, str) and status.strip().lower() in TERMINAL_STATUSES


def _canned_outcome(ticket_id: str) -> Dict[str, Any]:
    """
    Build the stub outcome: the sample resolved ticket JSON when available.

    Falls back to a minimal inline payload when ``examples/sample_ticket_outcome.json``
    is missing or unreadable. The requested *ticket_id* wins when non-empty.
    """
    if _SAMPLE_OUTCOME_PATH.is_file():
        try:
            data = json.loads(_SAMPLE_OUTCOME_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            if ticket_id:
                data["ticket_id"] = ticket_id
            return data
    return {
        "ticket_id": ticket_id,
        "status": "resolved",
        "resolution_notes": "(stub) canned resolved outcome; no ticket API configured.",
        "actual_root_cause": "",
    }


def poll_ticket_outcome(
    ticket_id: str,
    *,
    base_url: Optional[str] = None,
    timeout_s: float = 60.0,
    interval_s: float = 2.0,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Poll a ticket API until the ticket reaches a terminal status, then return it.

    Parameters
    ----------
    ticket_id
        Ticket identifier appended to the URL: ``GET {base_url}/tickets/{ticket_id}``.
    base_url
        Ticket API base URL. When None, ``AI_INCIDENT_TICKET_API_BASE`` is used.
        When neither is set — or ``AI_INCIDENT_USE_STUB`` is enabled — a canned
        stub outcome is returned without any network access.
    timeout_s
        Overall polling deadline in seconds (default 60).
    interval_s
        Delay between poll attempts in seconds (default 2.0).
    api_key
        Bearer token for the ``Authorization`` header. When None,
        ``AI_INCIDENT_TICKET_API_KEY`` is used; when both are unset, no
        Authorization header is sent.

    Returns
    -------
    dict
        The parsed ticket JSON whose ``status`` is one of ``resolved`` /
        ``closed`` / ``done`` (case-insensitive).

    Raises
    ------
    TimeoutError
        If no terminal status is observed within *timeout_s* seconds.
    ValueError
        If *timeout_s* / *interval_s* are not positive, *ticket_id* is empty for
        a live poll, the API is unreachable, returns a non-2xx status, or the
        response is not a JSON object with a ``status`` field.
    ImportError
        If a live poll is requested but ``httpx`` is not installed.
    """
    if interval_s <= 0:
        raise ValueError(f"interval_s must be > 0, got {interval_s!r}.")
    if timeout_s <= 0:
        raise ValueError(f"timeout_s must be > 0, got {timeout_s!r}.")

    resolved_base = base_url or _env_value("AI_INCIDENT_TICKET_API_BASE")
    resolved_key = api_key or _env_value("AI_INCIDENT_TICKET_API_KEY")

    if resolved_base is None or _stub_mode_enabled():
        return _canned_outcome(ticket_id)

    if not str(ticket_id).strip():
        raise ValueError("ticket_id must be non-empty when polling a live ticket API.")

    try:
        import httpx
    except ImportError as e:
        raise ImportError(
            "httpx is required for live ticket polling. "
            "Run: pip install -r requirements-api.txt"
        ) from e

    url = f"{resolved_base.rstrip('/')}/tickets/{ticket_id}"
    headers = {"Accept": "application/json"}
    if resolved_key:
        headers["Authorization"] = f"Bearer {resolved_key}"

    deadline = time.monotonic() + float(timeout_s)
    last_status: Optional[str] = None

    while True:
        try:
            resp = httpx.get(url, headers=headers, timeout=_REQUEST_TIMEOUT_S)
        except httpx.HTTPError as e:
            raise ValueError(f"Cannot reach ticket API at {url}: {e}") from e

        if not 200 <= resp.status_code < 300:
            raise ValueError(
                f"Ticket API GET {url} returned HTTP {resp.status_code}; "
                "expected a 2xx JSON response."
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise ValueError(f"Ticket API GET {url} did not return valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(
                f"Ticket API GET {url} must return a JSON object with a 'status' field."
            )
        if "status" not in data:
            raise ValueError(
                f"Ticket API response from {url} is missing the required 'status' field."
            )

        if is_terminal_status(data.get("status")):
            return data
        raw_status = data.get("status")
        last_status = str(raw_status) if raw_status is not None else None

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Ticket '{ticket_id}' did not reach a terminal status "
                f"{sorted(TERMINAL_STATUSES)} within {timeout_s:.1f}s "
                f"(last status: {last_status!r}, url: {url})."
            )
        time.sleep(min(float(interval_s), remaining))
