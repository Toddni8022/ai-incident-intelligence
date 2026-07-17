# AI Incident Intelligence — slim API/CLI image.
#
# Installs requirements.txt (minus chromadb) + requirements-api.txt.
# chromadb is deliberately excluded to keep the image small, so RAG grounding
# (--runbook-dir) is NOT available in this image: build a chromadb-enabled
# variant with the unfiltered requirements.txt if you need it (see README
# "Run with Docker"). requirements-langgraph.txt is not installed either —
# the LangGraph workflow stays a local/optional extra.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first for better layer caching. chromadb is filtered
# out of requirements.txt to keep the image slim (see note above).
COPY requirements.txt requirements-api.txt ./
RUN grep -vi chromadb requirements.txt > requirements-slim.txt \
    && pip install --no-cache-dir -r requirements-slim.txt -r requirements-api.txt

COPY . .

# Run as an unprivileged user; the app only writes to /tmp (temp uploads).
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# Offline demo default so `docker run` works without an OPENAI_API_KEY;
# override with `-e AI_INCIDENT_USE_STUB=` (empty) plus your key for live calls.
ENV AI_INCIDENT_USE_STUB=1

CMD ["python", "main.py", "--serve", "--host", "0.0.0.0", "--port", "8000"]
