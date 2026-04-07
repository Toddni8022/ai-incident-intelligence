"""
Parse heterogeneous system log text into structured records.

Supports common patterns: ISO-8601 timestamps, bracketed levels,
``LEVEL: message``, and plain ``LEVEL message`` (space-separated).
Unknown lines are still captured.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class LogEntry:
    """A single parsed log line with optional metadata."""

    timestamp: Optional[str]
    level: str
    component: str
    message: str
    raw_line: str

    def to_compact_line(self) -> str:
        """Return a short string suitable for LLM context windows."""
        parts = []
        if self.timestamp:
            parts.append(self.timestamp)
        parts.append(f"[{self.level}]")
        if self.component:
            parts.append(f"{self.component}:")
        parts.append(self.message)
        return " ".join(parts)


# ISO-like prefix: 2025-04-06T12:00:00Z or 2025-04-06 12:00:00
_ISO_PREFIX = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)\s+"
)
# [INFO] or [error]
_BRACKET_LEVEL = re.compile(r"^\[(?P<level>[A-Za-z]+)\]\s*")
# component[pid]: message (syslog-ish)
_COMPONENT = re.compile(r"^(?P<comp>[\w.-]+)(?:\[\d+\])?:\s*(?P<msg>.+)$")
# LEVEL: message
_LEVEL_COLON = re.compile(r"^(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL):\s*",
                          re.IGNORECASE)
# LEVEL message (no colon), e.g. "ERROR Database connection timeout"
_LEVEL_SPACE = re.compile(
    r"^(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\s+(?P<msg>.+)$",
    re.IGNORECASE,
)


def parse_log_line(line: str) -> Optional[LogEntry]:
    """
    Parse a single line of log text into a :class:`LogEntry`.

    Parameters
    ----------
    line
        Raw text; leading/trailing whitespace is stripped. Empty lines
        return ``None``.

    Returns
    -------
    LogEntry or None
        Structured entry, or ``None`` if the line is empty.
    """
    text = line.strip()
    if not text:
        return None

    timestamp: Optional[str] = None
    rest = text

    m = _ISO_PREFIX.match(rest)
    if m:
        timestamp = m.group("ts")
        rest = rest[m.end() :]

    level = "UNKNOWN"
    m = _BRACKET_LEVEL.match(rest)
    if m:
        level = m.group("level").upper()
        rest = rest[m.end() :]
    else:
        m2 = _LEVEL_COLON.match(rest)
        if m2:
            level = m2.group("level").upper()
            rest = rest[m2.end() :]
        else:
            m3s = _LEVEL_SPACE.match(rest)
            if m3s:
                level = m3s.group("level").upper()
                rest = m3s.group("msg")

    component = ""
    message = rest.strip()

    m3 = _COMPONENT.match(message)
    if m3:
        component = m3.group("comp")
        message = m3.group("msg").strip()

    return LogEntry(
        timestamp=timestamp,
        level=level,
        component=component,
        message=message,
        raw_line=text,
    )


def parse_logs(text: str) -> List[LogEntry]:
    """
    Split *text* into lines and parse each non-empty line.

    Parameters
    ----------
    text
        Full log file or stream content.

    Returns
    -------
    list of LogEntry
        Parsed entries in original order.
    """
    entries: List[LogEntry] = []
    for line in text.splitlines():
        parsed = parse_log_line(line)
        if parsed is not None:
            entries.append(parsed)
    return entries


def entries_to_context(entries: List[LogEntry], max_lines: int = 200) -> str:
    """
    Build a newline-separated block of compact log lines for LLM prompts.

    Parameters
    ----------
    entries
        Parsed log entries.
    max_lines
        Maximum number of lines to include (from the end if truncated).

    Returns
    -------
    str
        Text block for inclusion in model prompts.
    """
    lines = [e.to_compact_line() for e in entries]
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return "\n".join(lines)
