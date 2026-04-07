"""
Optional vector retrieval over runbooks / postmortems using ChromaDB.

Install ``chromadb`` (see ``requirements.txt``). If Chroma is missing or fails,
callers should skip grounding and analyze logs only.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ingestion.log_parser import LogEntry

_COLLECTION = "ai_incident_runbooks"


def is_rag_available() -> bool:
    """Return True if ``chromadb`` can be imported."""
    try:
        import chromadb  # noqa: F401
    except ImportError:
        return False
    return True


def _client(persist_directory: Path):
    import chromadb

    persist_directory.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(persist_directory))


def ingest_runbook_directory(
    docs_dir: Path,
    persist_directory: Path,
) -> int:
    """
    Upsert all ``.md`` and ``.txt`` files under *docs_dir* into a Chroma collection.

    Returns
    -------
    int
        Number of files ingested.
    """
    if not is_rag_available():
        raise RuntimeError("chromadb is not installed; pip install chromadb")

    docs_dir = docs_dir.resolve()
    if not docs_dir.is_dir():
        raise FileNotFoundError(f"Runbook directory not found: {docs_dir}")

    texts: List[str] = []
    ids: List[str] = []
    metas: List[dict] = []

    for path in sorted(docs_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".md", ".txt"}:
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        rid = str(path.resolve())
        texts.append(body)
        ids.append(rid)
        metas.append({"path": str(path.relative_to(docs_dir))})

    if not texts:
        return 0

    client = _client(persist_directory)
    col = client.get_or_create_collection(_COLLECTION)
    col.upsert(documents=texts, ids=ids, metadatas=metas)
    return len(texts)


def retrieve_grounding_context(
    query: str,
    persist_directory: Path,
    *,
    n_results: int = 5,
) -> str:
    """
    Query the runbook collection and return a single string of retrieved chunks.

    If the collection is empty or query fails, returns an empty string.
    """
    if not is_rag_available() or not query.strip():
        return ""

    persist_directory.mkdir(parents=True, exist_ok=True)
    try:
        client = _client(persist_directory)
        col = client.get_or_create_collection(_COLLECTION)
        count = col.count()
        if count == 0:
            return ""
        res = col.query(query_texts=[query[:8000]], n_results=min(n_results, max(1, count)))
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
    except Exception:
        return ""

    blocks: List[str] = []
    for i, doc in enumerate(docs or []):
        if not (doc or "").strip():
            continue
        src = ""
        if i < len(metas or []) and metas[i]:
            src = str(metas[i].get("path") or "")
        head = f"### Snippet ({src})\n\n" if src else "### Snippet\n\n"
        blocks.append(head + doc.strip())
    return "\n\n".join(blocks).strip()


def build_log_query_for_retrieval(
    entries: List["LogEntry"],
    max_lines: int = 40,
) -> str:
    """
    Build a short query string from parsed :class:`~ingestion.log_parser.LogEntry` rows.

    Prefers ERROR/WARNING lines, then falls back to the log tail.
    """
    from ingestion.log_parser import LogEntry as LE

    if not entries:
        return ""

    important: List[LE] = []
    for e in entries:
        lv = e.level.upper()
        if lv in {"ERROR", "WARN", "WARNING", "FATAL", "CRITICAL"}:
            important.append(e)
    pick = important[-max_lines:] if important else entries[-max_lines:]
    return "\n".join(e.to_compact_line() for e in pick)
