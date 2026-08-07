# Sandeep AI Command Center — Agents Suite

A lightweight, dependency-light Python framework that runs a suite of AI agents for
Sandeep's day-to-day work: executive support, finance tracking, BD & proposals, job
search, digital marketing, document automation, and learning coaching.

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
python sandbox.py --agent finance "Record a 50 GBP software subscription expense"
python sandbox.py --agent bd --chat          # interactive REPL
python sandbox.py --agent docs --mock "..."  # offline test, no API key
python sandbox.py --list
```

## Agents

| Key | Agent | What it does |
|-----|-------|--------------|
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

## Architecture

```
Agents/
  sandbox.py            CLI entry point
  selftest.py           offline tests (no API key)
  agents_core/
    config.py           env config
    llm.py              Anthropic + OpenAI-compatible clients + tool-loop primitives
    tools.py            tool registry + built-in tools (file, search, memory, ledger)
    agent.py            BaseAgent: history, tool-calling loop, error handling
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

## Testing

```bash
python selftest.py     # 21 offline checks: registry, tools, schemas, tool loop
```

## Roadmap

- Gmail/IMAP integration for the executive assistant (inbox triage + send drafts).
- Google Sheets-backed ledger instead of CSV.
- Scheduled runs (Windows Task Scheduler) for weekly finance summaries.
- A web UI (FastAPI) on top of `agents_core`, deployable on Render like ResumeIQ.
