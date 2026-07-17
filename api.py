"""
Optional FastAPI service wrapper around the incident pipeline.

Exposes ``POST /analyze`` (multipart log-file upload **or** JSON body) and returns
the structured analysis plus the structured ticket JSON. ``GET /health`` reports
service status, stub-mode, RAG availability, and the service version.

Install the extra dependencies and run::

    pip install -r requirements-api.txt
    python main.py --serve --host 127.0.0.1 --port 8000

``fastapi`` / ``uvicorn`` are imported lazily inside :func:`create_app` and
:func:`serve`, so ``import api`` works even when they are not installed.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

_API_INSTALL_HINT = "Run: pip install -r requirements-api.txt"

APP_VERSION = "1.0.0"


def _stub_mode_enabled() -> bool:
    """Return True when ``AI_INCIDENT_USE_STUB`` requests offline stub responses."""
    return os.environ.get("AI_INCIDENT_USE_STUB", "").lower() in ("1", "true", "yes")


def _rag_available() -> bool:
    """Return True when Chroma-backed RAG grounding is usable (guarded import)."""
    try:
        from grounding.rag_store import is_rag_available
    except ImportError:
        return False
    try:
        return bool(is_rag_available())
    except Exception:  # pragma: no cover - defensive: never break /health
        return False


def health_payload() -> Dict[str, Any]:
    """
    Build the ``GET /health`` response body.

    Returns
    -------
    dict
        ``status`` (``"ok"``), ``version``, ``stub_mode`` (from
        ``AI_INCIDENT_USE_STUB``), and ``rag_available`` (chromadb importable).
        Evaluated at call time so tests/containers see current env vars.
    """
    return {
        "status": "ok",
        "version": APP_VERSION,
        "stub_mode": _stub_mode_enabled(),
        "rag_available": _rag_available(),
    }


def _write_temp_text(text: str, *, suffix: str, keep: List[Path]) -> Path:
    """Write *text* to a temp file (registered in *keep* for later cleanup)."""
    fh = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=suffix, delete=False
    )
    try:
        fh.write(text)
    finally:
        fh.close()
    path = Path(fh.name)
    keep.append(path)
    return path


def _result_to_payload(result: Any) -> Dict[str, Any]:
    """Serialize a :class:`pipeline_result.PipelineResult` for a JSON response."""
    return {
        "analysis": result.analysis.to_dict(),
        "ticket": result.ticket.to_dict(),
        "report_markdown": result.report_markdown,
    }


def create_app() -> Any:
    """
    Build the FastAPI application with the ``POST /analyze`` endpoint.

    Returns
    -------
    fastapi.FastAPI
        The ASGI app (also usable with ``fastapi.testclient.TestClient``).

    Raises
    ------
    ImportError
        If ``fastapi`` is not installed, with a clear install hint.
    """
    try:
        from fastapi import FastAPI, HTTPException, Request
    except ImportError as e:
        raise ImportError(f"FastAPI is not installed. {_API_INSTALL_HINT}") from e

    app = FastAPI(
        title="AI Incident Intelligence",
        summary="Parse logs → LLM incident analysis → Markdown report + ticket JSON.",
        version=APP_VERSION,
    )

    async def health() -> Dict[str, Any]:
        """Liveness probe: status, version, stub-mode, and RAG availability."""
        return health_payload()

    app.add_api_route("/health", health, methods=["GET"])

    async def analyze(request: Request) -> Dict[str, Any]:
        """
        Analyze logs sent as a multipart ``file`` upload or a JSON body.

        JSON body shape::

            {"log_text": "...", "runbook_dir": "optional/server/dir",
             "ticket_outcome": {"status": "resolved", ...}}

        Multipart form fields: ``file`` (required), optional ``runbook_dir``
        and ``ticket_outcome`` (JSON-encoded object string).
        """
        from main import run_pipeline

        content_type = request.headers.get("content-type", "")
        log_text: Optional[str] = None
        runbook_dir: Optional[str] = None
        ticket_outcome: Any = None

        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(
                    status_code=422,
                    detail="Multipart request must include a 'file' log upload.",
                )
            raw_bytes = await upload.read()
            log_text = raw_bytes.decode("utf-8", errors="replace")
            rd = form.get("runbook_dir")
            runbook_dir = str(rd) if rd else None
            to = form.get("ticket_outcome")
            if to:
                try:
                    ticket_outcome = json.loads(str(to))
                except json.JSONDecodeError as e:
                    raise HTTPException(
                        status_code=422,
                        detail=f"'ticket_outcome' form field is not valid JSON: {e}",
                    ) from e
        else:
            try:
                body = await request.json()
            except json.JSONDecodeError as e:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Request body must be JSON with a 'log_text' field, or use "
                        f"multipart file upload. ({e})"
                    ),
                ) from e
            if not isinstance(body, dict):
                raise HTTPException(
                    status_code=422,
                    detail="JSON body must be an object with a 'log_text' field.",
                )
            log_text_raw = body.get("log_text")
            if log_text_raw is not None and not isinstance(log_text_raw, str):
                raise HTTPException(
                    status_code=422, detail="'log_text' must be a string."
                )
            log_text = log_text_raw
            rd = body.get("runbook_dir")
            runbook_dir = str(rd) if rd else None
            ticket_outcome = body.get("ticket_outcome")

        if log_text is None or not log_text.strip():
            raise HTTPException(
                status_code=422,
                detail="Provide non-empty 'log_text' (JSON body) or a 'file' upload "
                "(multipart); the log content must not be empty.",
            )
        if ticket_outcome is not None and not isinstance(ticket_outcome, dict):
            raise HTTPException(
                status_code=422,
                detail="'ticket_outcome' must be a JSON object with keys such as "
                "'status', 'ticket_id', 'resolution_notes', 'actual_root_cause'.",
            )

        temp_paths: List[Path] = []
        try:
            log_path = _write_temp_text(log_text, suffix=".log", keep=temp_paths)
            outcome_path: Optional[Path] = None
            if ticket_outcome is not None:
                outcome_path = _write_temp_text(
                    json.dumps(ticket_outcome), suffix=".json", keep=temp_paths
                )
            result = run_pipeline(
                log_path,
                runbook_dir=Path(runbook_dir) if runbook_dir else None,
                ticket_outcome_path=outcome_path,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        finally:
            for p in temp_paths:
                p.unlink(missing_ok=True)

        return _result_to_payload(result)

    # ``from __future__ import annotations`` stores the handler's annotations as
    # strings, and FastAPI resolves them against module globals — where ``Request``
    # (imported lazily above) does not exist. Bind the real class before
    # registering the route so FastAPI treats it as the ASGI request object.
    analyze.__annotations__["request"] = Request
    app.add_api_route("/analyze", analyze, methods=["POST"])

    return app


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """
    Run the service with uvicorn (blocking).

    Parameters
    ----------
    host
        Bind address (default ``127.0.0.1``).
    port
        Bind port (default ``8000``).

    Raises
    ------
    ImportError
        If ``fastapi`` or ``uvicorn`` is not installed, with an install hint.
    """
    app = create_app()
    try:
        import uvicorn
    except ImportError as e:
        raise ImportError(f"uvicorn is not installed. {_API_INSTALL_HINT}") from e
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    serve()
