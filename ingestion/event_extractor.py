"""
Extract important log events (errors, warnings, and their timestamps) as JSON-ready data.

Builds on :mod:`ingestion.log_parser` line parsing. Output is plain ``dict`` / ``list``
structures suitable for :func:`json.dumps`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from ingestion.log_parser import LogEntry, parse_logs

# Levels treated as important by default (case-insensitive match on normalized level).
_DEFAULT_IMPORTANT_LEVELS: Set[str] = frozenset(
    {"ERROR", "WARN", "WARNING", "FATAL", "CRITICAL"}
)


def is_important_event(
    entry: LogEntry,
    *,
    levels: Optional[Set[str]] = None,
    include_unknown_with_keywords: bool = False,
) -> bool:
    """
    Return True if *entry* should be included as an important event.

    Parameters
    ----------
    entry
        Parsed log line.
    levels
        Uppercase level names to include. Defaults to error/warning/fatal/critical.
    include_unknown_with_keywords
        If True, include ``UNKNOWN`` level lines whose message suggests failure
        (e.g. contains "error", "exception", "failed", "timeout").
    """
    want = levels if levels is not None else _DEFAULT_IMPORTANT_LEVELS
    lv = entry.level.upper()
    if lv in want:
        return True
    if not include_unknown_with_keywords or lv != "UNKNOWN":
        return False
    msg = entry.message.lower()
    hints = ("error", "exception", "failed", "failure", "timeout", "fatal", "panic")
    return any(h in msg for h in hints)


def log_entry_to_dict(entry: LogEntry) -> Dict[str, Any]:
    """
    Convert a :class:`LogEntry` to a JSON-serializable dict.

    ``component`` is omitted from JSON when empty (``None`` in output).
    """
    return {
        "timestamp": entry.timestamp,
        "level": entry.level,
        "component": entry.component or None,
        "message": entry.message,
        "raw_line": entry.raw_line,
    }


def extract_important_events(
    entries: List[LogEntry],
    *,
    levels: Optional[Set[str]] = None,
    include_unknown_with_keywords: bool = False,
) -> List[Dict[str, Any]]:
    """
    Filter *entries* to important events and return each as a dict.

    Returns
    -------
    list of dict
        Event records with ``timestamp``, ``level``, ``component``, ``message``, ``raw_line``.
    """
    return [
        log_entry_to_dict(e)
        for e in entries
        if is_important_event(
            e,
            levels=levels,
            include_unknown_with_keywords=include_unknown_with_keywords,
        )
    ]


def build_events_payload(
    entries: List[LogEntry],
    *,
    source: Optional[str] = None,
    levels: Optional[Set[str]] = None,
    include_unknown_with_keywords: bool = False,
) -> Dict[str, Any]:
    """
    Build a full JSON-friendly payload: counts, source path, and important events.

    Parameters
    ----------
    entries
        All parsed log entries from :func:`ingestion.log_parser.parse_logs`.
    source
        Optional file path or label for the log origin.
    levels
        Passed to :func:`is_important_event`.
    include_unknown_with_keywords
        Passed to :func:`is_important_event`.

    Returns
    -------
    dict
        Keys: ``source``, ``total_entries``, ``important_event_count``, ``important_events``.
    """
    important = extract_important_events(
        entries,
        levels=levels,
        include_unknown_with_keywords=include_unknown_with_keywords,
    )
    return {
        "source": source,
        "total_entries": len(entries),
        "important_event_count": len(important),
        "important_events": important,
    }


def parse_log_text_to_events_json(
    text: str,
    *,
    source: Optional[str] = None,
    indent: Optional[int] = 2,
    levels: Optional[Set[str]] = None,
    include_unknown_with_keywords: bool = False,
) -> str:
    """
    Parse log *text*, extract important events, return a JSON string.

    Parameters
    ----------
    text
        Raw log file content.
    source
        Optional origin label stored in the payload.
    indent
        Indentation for :func:`json.dumps` (``None`` for compact output).
    levels
        Which uppercase levels to treat as important.
    include_unknown_with_keywords
        Whether to surface UNKNOWN lines that look like failures.

    Returns
    -------
    str
        UTF-8 JSON text.
    """
    entries = parse_logs(text)
    payload = build_events_payload(
        entries,
        source=source,
        levels=levels,
        include_unknown_with_keywords=include_unknown_with_keywords,
    )
    return json.dumps(payload, indent=indent, ensure_ascii=False)


def parse_log_file_to_events_json(
    path: str | Path,
    *,
    encoding: str = "utf-8",
    indent: Optional[int] = 2,
    levels: Optional[Set[str]] = None,
    include_unknown_with_keywords: bool = False,
) -> str:
    """
    Read a log file from disk, extract important events, return JSON string.

    Parameters
    ----------
    path
        Filesystem path to the log file.
    encoding
        Text encoding for reading the file.
    indent
        JSON pretty-print indent; ``None`` for compact output.
    levels
        Which levels count as important.
    include_unknown_with_keywords
        Passed to :func:`build_events_payload`.

    Returns
    -------
    str
        JSON document describing parsed important events.
    """
    p = Path(path)
    text = p.read_text(encoding=encoding)
    return parse_log_text_to_events_json(
        text,
        source=str(p.resolve()),
        indent=indent,
        levels=levels,
        include_unknown_with_keywords=include_unknown_with_keywords,
    )


def parse_log_file_to_events_dict(
    path: str | Path,
    *,
    encoding: str = "utf-8",
    levels: Optional[Set[str]] = None,
    include_unknown_with_keywords: bool = False,
) -> Dict[str, Any]:
    """
    Read a log file and return the same structure as inside the JSON payload (as dict).

    Useful when you need Python objects without round-tripping through a string.
    """
    p = Path(path)
    text = p.read_text(encoding=encoding)
    entries = parse_logs(text)
    return build_events_payload(
        entries,
        source=str(p.resolve()),
        levels=levels,
        include_unknown_with_keywords=include_unknown_with_keywords,
    )
