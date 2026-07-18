"""Optional FastAPI service wrapper around the incident pipeline."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from security_controls import (
    api_key_is_valid,
    enforce_log_limits,
    redact_sensitive_text,
    validate_runbook_dir,
)

_API_INSTALL_HINT = "Run: pip install -r requirements-api.txt"
APP_VERSION = "1.1.0"


def _stub_mode_enabled() -> bool:
    return os.environ.get("AI_INCIDENT_USE_STUB", "").lower() in ("1", "true", "yes")


def _rag_available() -> bool:
    try:
        from grounding.rag_store import is_rag_available
        return bool(is_rag_available())
    except Exception:
        return False


def health_payload() -> Dict[str, Any]:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "stub_mode": _stub_mode_enabled(),
        "rag_available": _rag_available(),
        "authentication_required": bool(os.environ.get("AI_INCIDENT_API_KEY", "").strip()),
    }


def _write_temp_text(text: str, *, suffix: str, keep: List[Path]) -> Path:
    fh = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=suffix, delete=False)
    try:
        fh.write(text)
    finally:
        fh.close()
    path = Path(fh.name)
    keep.append(path)
    return path


def _result_to_payload(result: Any, *, redaction_count: int) -> Dict[str, Any]:
    return {
        "analysis": result.analysis.to_dict(),
        "ticket": result.ticket.to_dict(),
        "report_markdown": result.report_markdown,
        "processing_metadata": {
            "sensitive_values_redacted": redaction_count,
            "confidence_type": "llm_self_assessment",
        },
    }


def create_app() -> Any:
    try:
        from fastapi import FastAPI, Header, HTTPException, Request
        from starlette.concurrency import run_in_threadpool
    except ImportError as e:
        raise ImportError(f"FastAPI is not installed. {_API_INSTALL_HINT}") from e

    app = FastAPI(
        title="AI Incident Intelligence",
        summary="Parse logs into a grounded incident assessment and structured ticket.",
        version=APP_VERSION,
    )

    async def health() -> Dict[str, Any]:
        return health_payload()

    app.add_api_route("/health", health, methods=["GET"])

    async def analyze(
        request: Request,
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    ) -> Dict[str, Any]:
        from main import run_pipeline

        if not api_key_is_valid(x_api_key):
            raise HTTPException(status_code=401, detail="Missing or invalid API key.")

        content_type = request.headers.get("content-type", "")
        log_text: Optional[str] = None
        runbook_dir_raw: Optional[str] = None
        ticket_outcome: Any = None

        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(status_code=422, detail="Multipart request must include a 'file'.")
            raw_bytes = await upload.read()
            log_text = raw_bytes.decode("utf-8", errors="replace")
            runbook_dir_raw = str(form.get("runbook_dir")) if form.get("runbook_dir") else None
            raw_outcome = form.get("ticket_outcome")
            if raw_outcome:
                try:
                    ticket_outcome = json.loads(str(raw_outcome))
                except json.JSONDecodeError as e:
                    raise HTTPException(status_code=422, detail="'ticket_outcome' is not valid JSON.") from e
        else:
            try:
                body = await request.json()
            except json.JSONDecodeError as e:
                raise HTTPException(status_code=422, detail="Request body must be valid JSON.") from e
            if not isinstance(body, dict):
                raise HTTPException(status_code=422, detail="JSON body must be an object.")
            log_text = body.get("log_text")
            if log_text is not None and not isinstance(log_text, str):
                raise HTTPException(status_code=422, detail="'log_text' must be a string.")
            runbook_dir_raw = str(body.get("runbook_dir")) if body.get("runbook_dir") else None
            ticket_outcome = body.get("ticket_outcome")

        if log_text is None or not log_text.strip():
            raise HTTPException(status_code=422, detail="Provide non-empty log content.")
        if ticket_outcome is not None and not isinstance(ticket_outcome, dict):
            raise HTTPException(status_code=422, detail="'ticket_outcome' must be a JSON object.")

        try:
            enforce_log_limits(log_text)
            runbook_dir = validate_runbook_dir(runbook_dir_raw)
        except ValueError as e:
            raise HTTPException(status_code=413 if "limit" in str(e).lower() else 422, detail=str(e)) from e

        redacted_log_text, redaction_count = redact_sensitive_text(log_text)
        temp_paths: List[Path] = []
        try:
            log_path = _write_temp_text(redacted_log_text, suffix=".log", keep=temp_paths)
            outcome_path: Optional[Path] = None
            if ticket_outcome is not None:
                redacted_outcome, outcome_redactions = redact_sensitive_text(json.dumps(ticket_outcome))
                redaction_count += outcome_redactions
                outcome_path = _write_temp_text(redacted_outcome, suffix=".json", keep=temp_paths)
            result = await run_in_threadpool(
                run_pipeline,
                log_path,
                runbook_dir=runbook_dir,
                ticket_outcome_path=outcome_path,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        finally:
            for path in temp_paths:
                path.unlink(missing_ok=True)

        return _result_to_payload(result, redaction_count=redaction_count)

    analyze.__annotations__["request"] = Request
    app.add_api_route("/analyze", analyze, methods=["POST"])
    return app


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    app = create_app()
    try:
        import uvicorn
    except ImportError as e:
        raise ImportError(f"uvicorn is not installed. {_API_INSTALL_HINT}") from e
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    serve()
