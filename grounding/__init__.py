"""RAG-style grounding: runbooks and org knowledge (ChromaDB)."""

from grounding.rag_store import (
    build_log_query_for_retrieval,
    ingest_runbook_directory,
    is_rag_available,
    retrieve_grounding_context,
)

__all__ = [
    "build_log_query_for_retrieval",
    "ingest_runbook_directory",
    "is_rag_available",
    "retrieve_grounding_context",
]
