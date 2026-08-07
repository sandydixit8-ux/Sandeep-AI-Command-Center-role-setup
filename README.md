# Sandeep AI Command Center — Agents Suite

A lightweight, dependency-light Python framework that runs a suite of AI agents for
Sandeep's day-to-day work: executive support, finance tracking, BD & proposals, job
search, digital marketing, document automation, and learning coaching.

The default agent — **`commander`** — is the full "Sandeep AI Command Center" from the
role setup prompt: one integrated advisor covering all domains in a single voice, with
every tool available. The specialist agents (`exec`, `finance`, `bd`, ...) are the same
core with a narrower prompt and toolset when you want a focused session.

Each agent is the same core (`agents_core`) with a domain system prompt and a tailored
set of tools. All agents share one framework, so adding a new agent is a ~5 line change.

## Quick start

```bash
cd Agents
python -m pip install -r requirements.txt
copy .env.example .env        # then add your API key (Anthropic or OpenAI-compatible)
```

Use your existing ResumeIQ Anthropic key for `AGENT_ANTHROPIC_API_KEY`.

```bash
python sandbox.py "Draft an email to a client and record a 1200 expense"   # commander (default)
python sandbox.py --agent finance "Record a 50 GBP software subscription expense"
python sandbox.py --agent bd --chat          # interactive REPL
python sandbox.py --agent commander --chat   # full command center in REPL
python sandbox.py --agent docs --mock "..."  # offline test, no API key
python sandbox.py --list
```

## Agents

| Key | Agent | What it does |
|-----|-------|--------------|
| `commander` | **Command Center** | the full integrated role prompt — all domains, one voice, all tools |
| `exec` | Executive Assistant | email drafts, meeting notes (decisions + owners), task prioritisation |
| `finance` | Finance Tracker | records income/expenses to a CSV ledger, cash-flow summaries, invoice follow-ups |
| `bd` | BD & Proposals | RFP compliance checks, win-probability, personalised outreach, proposal drafts |
| `jobsearch` | Job Search | honest resume-vs-JD skill scoring, tailored resumes, interview prep |
| `marketing` | Digital Marketing | ad copy, SEO, campaign structure, competitor research |
| `docs` | Document Automation | writes DPRs, proposals, memos and deck outlines as files |
| `coach` | Learning Coach | study plans and skill roadmaps tied to a concrete goal |

## Configuration (Agents/.env)

| Variable | Default | Purpose |
|----------|---------|---------|
| `AGENT_LLM_PROVIDER` | `anthropic` | `anthropic`, `openai`, or `mock` |
| `AGENT_ANTHROPIC_API_KEY` | — | Anthropic key |
| `AGENT_ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Anthropic model |
| `AGENT_OPENAI_API_KEY` / `AGENT_OPENAI_BASE_URL` / `AGENT_OPENAI_MODEL` | — | OpenAI-compatible (OpenAI, Groq, Ollama, LM Studio) |
| `AGENT_SEARCH_BRAVE_API_KEY` | — | Optional. If unset, `web_search` uses DuckDuckGo HTML (may be rate-limited) |
| `AGENT_GMAIL_USER` / `AGENT_GMAIL_APP_PASSWORD` | — | Gmail inbox triage + reply drafts (app password from Google) |
| `AGENT_GOOGLE_SERVICE_ACCOUNT_FILE` / `AGENT_SHEET_ID` / `AGENT_SHEET_RANGE` | — | Google Sheets ledger sync (optional, needs `pip install google-api-python-client google-auth`) |

## Integrations

### Gmail (exec + bd agents)
Set `AGENT_GMAIL_USER` and an [app password](https://myaccount.google.com/apppasswords).
Tools: `gmail_inbox` (IMAP query, default `UNSEEN`), `gmail_thread`, `gmail_draft`
(saves a reply for review — never sends), and `gmail_send` (only called after you
explicitly approve the content). Sending uses Gmail SMTP.

### Google Sheets ledger (finance agent)
1. Create a service account in Google Cloud Console and share your sheet with its email.
2. Set `AGENT_GOOGLE_SERVICE_ACCOUNT_FILE`, `AGENT_SHEET_ID`, then
   `pip install google-api-python-client google-auth`.
Tools: `sheets_push` (CSV → sheet) and `sheets_pull` (sheet → CSV).

### Job-search scoring (jobsearch agent)
The `skill_match` tool scores a resume against a JD by strict keyword overlap (0–100%),
listing matched skills and the real gaps. It only counts skills literally present in the
text — it never fabricates fit. `skills_in` extracts known skill keywords from any text.

### Web API
```bash
uvicorn webapi.app:app --reload      # run from the Agents/ directory
```
Endpoints: `GET /health`, `GET /api/v1/agents`, `POST /api/v1/run` (`{agent, task}`).
`commander` is available like any other agent here. Deploy on Render with the included
`render.yaml` (Blueprint → select this repo).

### Scheduled runs (Windows Task Scheduler)
`scripts/scheduled_run.py` runs a standalone finance summary (`--finance`) or inbox
triage (`--inbox --limit 10`) and logs to `data/scheduled/scheduled.log`. Add it as a
Task Scheduler daily/weekly trigger — the script header has a ready-made task config.

## Architecture

```
Agents/
  sandbox.py            CLI entry point
  selftest.py           offline tests (no API key)
  render.yaml           Render blueprint for the web API
  webapi/
    app.py              FastAPI app (health, agents list, run)
  agents_core/
    config.py           env config
    llm.py              Anthropic + OpenAI-compatible clients + tool-loop primitives
    tools.py            tool registry + built-in tools (file, search, memory, ledger)
    agent.py            BaseAgent: history, tool-calling loop, error handling
    scoring.py          honest resume-vs-JD skill-match scoring
    gmail.py            Gmail IMAP/SMTP (stdlib only)
    sheets.py           Google Sheets ledger sync (optional dependency)
    prompts.py          system prompts for all 7 agents
    registry.py         agent factory + registry
  data/                 (gitignored) agent memory + finance_ledger.csv
  outputs/              (gitignored) generated files
```

The tool-calling loop: the agent calls the LLM; if the model requests tools, each tool
is executed with schema-validated arguments and the results are fed back; repeat until
the model produces a final answer or the step cap is reached. Both Anthropic Messages
API and OpenAI-compatible chat completions are supported.

## Safety

- `write_file` / `append_file` only write inside `outputs/`; `read_file` is limited to
  the workspace (path traversal is rejected).
- The finance agent records only what you tell it; forecasts are flagged as projections,
  and it is not a tax/compliance adviser.
- Agents never fabricate client names, project history, or metrics — they use only what
  you provide.
- Email is never sent without explicit user approval; drafts are saved to
  `outputs/gmail/` for review first.

## Testing

```bash
python selftest.py     # 40 offline checks: registry, tools, scoring, schemas, tool loop
```

## Roadmap

- Streaming responses over the web API (SSE) for long agent runs.
- A simple chat UI (static HTML) served by FastAPI.
- PDF resume parsing so `skill_match` can score PDF resumes directly.
