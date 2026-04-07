# AI Incident Intelligence

Python toolkit that ingests **system logs**, runs **LLM-based incident analysis**, and produces a **Markdown incident report** plus **structured support ticket** fields (title, description, severity, suggested actions). Uses the **OpenAI API** by default.

## Workflow

```text
logs → parse → LLM analysis → Markdown report → support ticket
```

The CLI (`main.py`) runs this end-to-end. You can also import individual modules (see below).

## Project layout

| Path | Purpose |
|------|---------|
| `ingestion/log_parser.py` | Parse lines into `LogEntry` (timestamps, `[LEVEL]`, `LEVEL: msg`, `LEVEL msg`) |
| `ingestion/event_extractor.py` | Filter errors/warnings; JSON payloads |
| `analysis/incident_analyzer.py` | LLM → `IncidentFinding` (legacy richer schema) |
| `analysis/structured_incident_llm.py` | LLM → `StructuredIncidentAnalysis` (summary, root cause, severity, actions) |
| `reporting/report_generator.py` | Report from `IncidentFinding` + optional LLM polish |
| `reporting/structured_incident_markdown.py` | Format structured analysis → Markdown report |
| `tickets/ticket_generator.py` | `SupportTicket` from `IncidentFinding` + optional LLM |
| `tickets/incident_report_ticket.py` | Report / analysis → `StructuredSupportTicket` |
| `examples/sample_logs.txt` | Sample logs |
| `main.py` | Full pipeline CLI |

## Requirements

- Python 3.10+ recommended  
- `pip install -r requirements.txt` (installs `openai`)

## Setup

```powershell
cd ai-incident-intelligence
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Environment variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | Required for live LLM calls (unless stub mode). Strip trailing newlines/spaces. |
| `OPENAI_MODEL` | Optional; default `gpt-4o-mini` |
| `AI_INCIDENT_USE_STUB` | Set to `1` / `true` / `yes` for **no API calls** — fixed demo analysis |

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:OPENAI_MODEL="gpt-4o-mini"   # optional
```

## CLI usage

Default log file: `examples/sample_logs.txt`.

```powershell
python main.py
python main.py examples/sample_logs.txt
python main.py --log examples/sample_logs.txt
python main.py -l examples/sample_logs.txt
```

### Options

| Option | Description |
|--------|-------------|
| `--log`, `-l PATH` | Log file (overrides positional path) |
| `--max-llm-lines N` | Max lines sent in the analysis prompt (default `200`) |
| `--max-report-log-lines N` | Max lines in the report log excerpt (default `80`) |
| `--out-report PATH` | Write Markdown report |
| `--out-ticket PATH` | Write ticket text (`format_text`) |
| `--out-ticket-json PATH` | Write ticket as JSON (`title`, `description`, `severity`, `suggested_actions`) |
| `-q`, `--quiet` | Only write `--out-*` files; no stdout |

### Examples

```powershell
# Offline demo (no API key)
$env:AI_INCIDENT_USE_STUB="1"
python main.py --log examples/sample_logs.txt

# Production-style: save artifacts
python main.py --log examples/sample_logs.txt --out-report report.md --out-ticket ticket.txt --out-ticket-json ticket.json

# Quiet run
python main.py -l examples/sample_logs.txt --out-report report.md -q
```

## Programmatic use

```python
from pathlib import Path
from main import run_pipeline

report_md, ticket = run_pipeline(Path("examples/sample_logs.txt"))
print(ticket.to_json())
```

Other entry points:

- `ingestion.parse_logs`, `ingestion.parse_log_file_to_events_json`
- `analysis.analyze_parsed_logs`, `analysis.analyze_logs`
- `reporting.format_structured_incident_markdown`
- `tickets.incident_analysis_to_structured_ticket`

## Log formats (parser)

The parser recognizes common patterns, including:

- ISO-ish timestamps at line start  
- `[INFO]`, `[ERROR]`, …  
- `LEVEL: message`  
- `LEVEL message` (space-separated, no colon)

## License

Use and modify freely for your own operations.
