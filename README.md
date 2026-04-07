# AI Incident Intelligence

Python toolkit that turns raw system logs into **structured analysis**, an **incident report**, and **support ticket** text using an LLM (OpenAI API).

## Workflow

```text
logs → parsing → AI analysis → incident report → support ticket
```

## Layout

- `ingestion/log_parser.py` — parse log lines into `LogEntry` records
- `analysis/incident_analyzer.py` — LLM JSON analysis → `IncidentFinding`
- `reporting/report_generator.py` — Markdown incident report
- `tickets/ticket_generator.py` — ticket title, body, priority (`SupportTicket`)
- `examples/sample_logs.txt` — example input
- `main.py` — CLI pipeline

## Setup

```bash
cd ai-incident-intelligence
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Set your API key (avoid trailing spaces or newlines in the value; they break HTTP headers):

```powershell
$env:OPENAI_API_KEY="sk-..."
```

```cmd
set OPENAI_API_KEY=sk-...
```

Optional:

- `OPENAI_MODEL` — default `gpt-4o-mini`
- `AI_INCIDENT_USE_STUB=1` — no API calls; canned analysis for demos

PowerShell stub example:

```powershell
$env:AI_INCIDENT_USE_STUB="1"
python main.py
```

## Usage

```bash
python main.py examples/sample_logs.txt
```

Write outputs to files:

```bash
python main.py examples/sample_logs.txt --out-report report.md --out-ticket ticket.txt
```

Skip extra LLM passes (faster / cheaper): analysis still requires an API key unless stub mode is on.

```bash
python main.py --no-report-llm --no-ticket-llm
```

## Stub mode (no API key)

```bash
set AI_INCIDENT_USE_STUB=1
python main.py
```

## License

Use and modify freely for your own operations.
