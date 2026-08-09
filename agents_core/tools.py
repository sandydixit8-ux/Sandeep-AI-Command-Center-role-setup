"""Tool registry: schema + python callable pairs the agents can invoke."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import BASE_DIR, DATA_DIR, OUTPUTS_DIR


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., Any]
    input_schema: dict[str, Any]
    agent: str = "common"

    def anthropic_schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

    def openai_schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.input_schema}}


class ToolError(Exception):
    pass


def _safe_path(target: str, allowed_root: Path) -> Path:
    """Resolve a path and ensure it stays inside the allowed root (no traversal)."""
    p = Path(target).expanduser()
    if not p.is_absolute():
        p = allowed_root / p
    p = p.resolve()
    root = allowed_root.resolve()
    if root not in p.parents and p != root:
        raise ToolError(f"path outside allowed directory: {target}")
    return p


def _strip_root_prefix(path: str, root: Path) -> Path:
    """Drop a leading 'outputs'/'data' prefix so paths work against the matching root."""
    p = Path(path).expanduser()
    parts = list(p.parts)
    if parts and parts[0].lower() == root.name.lower():
        p = Path(*parts[1:])
    return p


def _resolve_read_path(path: str) -> Path:
    """Resolve a file path for reading: try outputs, then data, then the project root.

    This lets agents read both files they generate (outputs/...), reference data
    (data/...), and source files in the workspace (e.g. ResumeIQ/...). A leading
    'outputs'/'data' prefix is tolerated.
    """
    p = Path(path).expanduser()
    if p.is_absolute():
        _safe_path(str(p), OUTPUTS_DIR)
        _safe_path(str(p), DATA_DIR)
        _safe_path(str(p), BASE_DIR.parent)
        cand = p.resolve()
        _deny_sensitive(cand)
        return cand
    for root in (OUTPUTS_DIR, DATA_DIR, BASE_DIR.parent):
        candidate = root / _strip_root_prefix(path, root)
        if candidate.is_file():
            _deny_sensitive(candidate)
            return candidate
    fallback = (BASE_DIR.parent / p).resolve()
    if fallback.is_file():
        _deny_sensitive(fallback)
    return fallback


def _outputs_path(path: str) -> Path:
    """Resolve a path under outputs/, tolerating a leading 'outputs/' prefix.

    The model may pass 'outputs/finance/x.md' or 'finance/x.md'; both resolve to
    {OUTPUTS_DIR}/finance/x.md (no double-prefixing).
    """
    p = Path(path).expanduser()
    parts = list(p.parts)
    if parts and parts[0].lower() == "outputs":
        p = Path(*parts[1:])
    return _safe_path(str(p), OUTPUTS_DIR)


def tool_get_time(fmt: str = "%Y-%m-%d %H:%M") -> str:
    return datetime.now().strftime(fmt)


def tool_list_files(dirpath: str = "outputs") -> str:
    root = BASE_DIR if dirpath in ("outputs", "data") else BASE_DIR / dirpath
    target = _safe_path(dirpath if dirpath in ("outputs", "data") else str(root), BASE_DIR)
    try:
        entries = sorted(target.iterdir())
    except FileNotFoundError:
        return f"directory not found: {target}"
    if not entries:
        return f"directory empty: {target}"
    lines = []
    for e in entries:
        tag = "dir" if e.is_dir() else f"{e.stat().st_size} bytes"
        lines.append(f"{e.name} ({tag})")
    return "\n".join(lines)


def tool_read_file(path: str) -> str:
    p = _resolve_read_path(path)
    if not p.is_file():
        raise ToolError(f"file not found: {path}")
    if p.stat().st_size > 200_000:
        raise ToolError(f"file too large to read ({p.stat().st_size} bytes): {path}")
    return p.read_text(encoding="utf-8", errors="replace")


def tool_write_file(path: str, content: str) -> str:
    p = _outputs_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {p}"


def tool_append_file(path: str, content: str) -> str:
    p = _outputs_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(content if content.endswith("\n") else content + "\n")
    return f"appended to {p}"


def _fetch(url: str, timeout: float = 30) -> str:
    import httpx

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            resp.raise_for_status()
            return resp.text
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"web request failed: {exc}") from exc


def _strip_html(text: str, max_chars: int = 6000) -> str:
    import re

    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    # drop zero-width / invisible formatting chars injected by HTML emails
    text = re.sub(r"[\u200b\u200c\u200d\u2060\ufeff]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()[:max_chars]


def tool_web_search(query: str, max_results: int = 5) -> str:
    """Search the web. Uses Brave Search API if a key is configured, else DuckDuckGo HTML."""
    from .config import get_settings

    key = get_settings().search_brave_api_key
    if key:
        return _brave_search(key, query, max_results)
    return _ddg_search(query, max_results)


def _brave_search(key: str, query: str, max_results: int) -> str:
    import httpx

    try:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])[:max_results]
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"brave search failed: {exc}") from exc
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title', '')}\n   {r.get('url', '')}\n   {r.get('description', '')}")
    return "\n\n".join(lines) if lines else "no results"


def _ddg_search(query: str, max_results: int) -> str:
    html = _fetch(f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}")
    import re

    blocks = re.findall(r'(?is)class="result__a"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</', html)
    if not blocks:
        return "no results (DuckDuckGo may have blocked the request; set AGENT_SEARCH_BRAVE_API_KEY)"
    lines = []
    for i, (title, snippet) in enumerate(blocks[:max_results], 1):
        clean_title = re.sub(r"<[^>]+>", "", title).strip()
        clean_snippet = _strip_html(snippet, 500)
        lines.append(f"{i}. {clean_title}\n   {clean_snippet}")
    return "\n\n".join(lines) or "no results"


def _tool_result(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, default=str)
    except TypeError:
        return str(value)


# ---- Memory tools (per-agent notes, stored as JSON) ----

_SENSITIVE_BASENAMES = (".env",)
_SENSITIVE_PREFIXES = (".env.",)

def _clean_agent_name(agent: str) -> str:
    """Normalise an agent name so it can never influence the path (no traversal)."""
    import re

    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", str(agent))
    cleaned = cleaned.replace("..", "_").strip("._") or "agent"
    return cleaned[:64]


def _deny_sensitive(path: Path) -> None:
    """Reject reads of credential-like files (e.g. .env) wherever they resolve."""
    name = path.name
    if name in _SENSITIVE_BASENAMES or name.startswith(_SENSITIVE_PREFIXES):
        raise ToolError(f"reading {name!r} is not allowed")


def _memory_file(agent: str) -> Path:
    return DATA_DIR / f"{_clean_agent_name(agent)}_memory.json"


def _load_memory(agent: str) -> dict[str, Any]:
    f = _memory_file(agent)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"notes": [], "history": []}


def tool_remember(agent: str, note: str) -> str:
    mem = _load_memory(agent)
    if note not in mem["notes"]:
        mem["notes"].append(note)
    _memory_file(agent).write_text(json.dumps(mem, indent=2, ensure_ascii=False), encoding="utf-8")
    return f"saved note ({len(mem['notes'])} total): {note}"


def tool_recall(agent: str) -> str:
    mem = _load_memory(agent)
    notes = mem.get("notes", [])
    if not notes:
        return "(no saved notes yet)"
    return "\n".join(f"- {n}" for n in notes)


# ---- Finance ledger tools (shared by the finance agent) ----


def _ledger_path() -> Path:
    return DATA_DIR / "finance_ledger.csv"


def _ensure_ledger() -> None:
    p = _ledger_path()
    if not p.exists():
        p.write_text("date,type,category,description,amount\n", encoding="utf-8")


def _csv_cell(value: str) -> str:
    """Sanitise a CSV cell so it cannot inject spreadsheet formulas (=, +, -, @, tab, CR)."""
    v = str(value).replace("\r", " ").replace("\n", " ").strip()
    if v.startswith(("=", "+", "-", "@", "\t")):
        v = "'" + v
    return v


def tool_ledger_add(amount: float, category: str, description: str, type: str = "expense", date: str = "") -> str:
    """Record a transaction. type is 'income' or 'expense'. date is YYYY-MM-DD (defaults to today)."""
    if type not in ("income", "expense"):
        raise ToolError("type must be 'income' or 'expense'")
    try:
        amount = float(amount)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"amount must be a number, got {amount!r}") from exc
    if amount <= 0:
        raise ToolError("amount must be greater than zero")
    _ensure_ledger()
    date = date or datetime.now().strftime("%Y-%m-%d")
    row = f"{_csv_cell(date)},{_csv_cell(type)},{_csv_cell(category)},{_csv_cell(description)},{amount:.2f}\n"
    with _ledger_path().open("a", encoding="utf-8") as fh:
        fh.write(row)
    return f"recorded {type} of {amount:.2f} ({category}): {description} on {date}"


def _parse_ledger() -> list[dict[str, Any]]:
    _ensure_ledger()
    rows: list[dict[str, Any]] = []
    with _ledger_path().open(encoding="utf-8") as fh:
        next(fh, None)  # header
        for line in fh:
            parts = line.rstrip("\n").split(",")
            if len(parts) < 5:
                continue
            date, typ, category, description, amount = parts
            try:
                rows.append(
                    {"date": date, "type": typ, "category": category, "description": description, "amount": float(amount)}
                )
            except ValueError:
                continue
    return rows


def tool_ledger_summary(period: str = "month") -> str:
    """Summarise income/expenses. period is 'all', 'month' (this calendar month) or 'year'."""
    from collections import defaultdict

    rows = _parse_ledger()
    if not rows:
        return "(ledger is empty — add transactions first)"
    now = datetime.now()
    if period == "month":
        rows = [r for r in rows if r["date"].startswith(now.strftime("%Y-%m"))]
    elif period == "year":
        rows = [r for r in rows if r["date"].startswith(now.strftime("%Y"))]
    elif period != "all":
        raise ToolError("period must be 'all', 'month' or 'year'")

    income = sum(r["amount"] for r in rows if r["type"] == "income")
    expense = sum(r["amount"] for r in rows if r["type"] == "expense")
    by_cat: dict[str, float] = defaultdict(float)
    for r in rows:
        if r["type"] == "expense":
            by_cat[r["category"]] += r["amount"]
    lines = [
        f"period: {period}  |  entries: {len(rows)}",
        f"income: {income:.2f}",
        f"expenses: {expense:.2f}",
        f"net: {income - expense:.2f}",
    ]
    if by_cat:
        lines.append("top expense categories:")
        for cat, amt in sorted(by_cat.items(), key=lambda kv: -kv[1])[:8]:
            lines.append(f"  {cat}: {amt:.2f}")
    return "\n".join(lines)


def build_tools(agent: str, extra: list[Tool] | None = None) -> list[Tool]:
    seen: set[str] = set()
    out: list[Tool] = []
    for t in COMMON_TOOLS + (extra or []):
        if t.name in seen:
            continue
        seen.add(t.name)
        out.append(t)
    return out


def execute_tool(tools: list[Tool], name: str, args: dict[str, Any], agent: str) -> str:
    for t in tools:
        if t.name == name:
            try:
                return _tool_result(t.fn(**args))
            except ToolError as exc:
                return f"error: {exc}"
            except TypeError as exc:
                return f"error: bad arguments for {name}: {exc}"
            except Exception as exc:  # noqa: BLE001
                return f"error: {name} failed: {exc}"
    return f"error: unknown tool: {name}"


# ------------------------------------------------------------------ skill match


def _read_tool_input(value: str) -> str:
    """If `value` points at an existing file, read it; otherwise treat it as text.

    PDF paths are transparently converted to text via pypdf so agents can score PDF
    resumes/JDs directly.
    """
    if len(value) > 500:
        return value
    for root in (BASE_DIR.parent, DATA_DIR, OUTPUTS_DIR):
        candidate = root / _strip_root_prefix(value, root)
        if candidate.is_file() and candidate.stat().st_size <= 200_000:
            _deny_sensitive(candidate)
            if candidate.suffix.lower() == ".pdf":
                from .pdfutil import pdf_to_text

                return pdf_to_text(candidate)
            return candidate.read_text(encoding="utf-8", errors="replace")
    return value


def tool_pdf_to_text(path: str) -> str:
    """Extract text from a PDF file (path relative to the workspace root)."""
    from .pdfutil import pdf_to_text

    return pdf_to_text(_resolve_read_path(path))


def tool_skill_match(resume: str, jd: str) -> str:
    """Score a resume against a job description. Each arg may be a file path or raw text."""
    from .scoring import format_score, score_resume

    return format_score(score_resume(_read_tool_input(resume), _read_tool_input(jd)))


def tool_skills_in(text: str) -> str:
    """List known skill keywords detected in a text (e.g. a capability statement)."""
    from .scoring import skills_summary

    return skills_summary(_read_tool_input(text))


# ------------------------------------------------------------------ gmail


def tool_gmail_inbox(query: str = "UNSEEN", limit: int = 10) -> str:
    from . import gmail as g

    return g.inbox_list(query, limit)


def tool_gmail_thread(message_id: str) -> str:
    from . import gmail as g

    return g.read_thread(message_id)


def tool_gmail_draft(message_id: str, reply_body: str) -> str:
    from . import gmail as g

    return g.draft_reply(message_id, reply_body)


def tool_gmail_send(to: str, subject: str, body: str) -> str:
    from . import gmail as g

    return g.send_email(to, subject, body)


# ------------------------------------------------------------------ sheets


def tool_sheets_push() -> str:
    from . import sheets as s

    return s.push_to_sheets()


def tool_sheets_pull() -> str:
    from . import sheets as s

    return s.pull_from_sheets()


# ------------------------------------------------------------------ tool lists


COMMON_TOOLS: list[Tool] = [
    Tool("get_time", "Get the current date and time.", tool_get_time, {"type": "object", "properties": {}}),
    Tool("list_files", "List files in a directory (relative to the project root).", tool_list_files, {"type": "object", "properties": {"dirpath": {"type": "string", "description": "directory path"}}}),
    Tool("read_file", "Read a text file relative to the project root.", tool_read_file, {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
    Tool("pdf_to_text", "Extract text from a PDF file.", tool_pdf_to_text, {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
    Tool("write_file", "Write a text file under the outputs/ directory.", tool_write_file, {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}),
    Tool("append_file", "Append text to file under the outputs/ directory.", tool_append_file, {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}),
    Tool("web_search", "Search the web for current information.", tool_web_search, {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}),
    Tool("remember", "Save a note/fact for this agent to remember later.", tool_remember, {"type": "object", "properties": {"agent": {"type": "string"}, "note": {"type": "string"}}, "required": ["agent", "note"]}),
    Tool("recall", "Show the saved notes for this agent.", tool_recall, {"type": "object", "properties": {"agent": {"type": "string"}}, "required": ["agent"]}),
]

FINANCE_TOOLS: list[Tool] = [
    Tool("ledger_add", "Record an income or expense in the finance ledger.", tool_ledger_add, {"type": "object", "properties": {"amount": {"type": "number"}, "category": {"type": "string"}, "description": {"type": "string"}, "type": {"type": "string", "enum": ["income", "expense"]}, "date": {"type": "string"}}, "required": ["amount", "category", "description"]}),
    Tool("ledger_summary", "Summarise income/expenses for a period.", tool_ledger_summary, {"type": "object", "properties": {"period": {"type": "string", "enum": ["all", "month", "year"]}}}),
    Tool("sheets_push", "Push the local finance ledger to Google Sheets.", tool_sheets_push, {"type": "object", "properties": {}}),
    Tool("sheets_pull", "Pull the Google Sheet ledger back into the local CSV.", tool_sheets_pull, {"type": "object", "properties": {}}),
]

JOBSEARCH_TOOLS: list[Tool] = [
    Tool("skill_match", "Score a resume against a job description. Each argument may be a file path (including PDF) or raw text.", tool_skill_match, {"type": "object", "properties": {"resume": {"type": "string"}, "jd": {"type": "string"}}, "required": ["resume", "jd"]}),
    Tool("skills_in", "List known skill keywords detected in a text or file.", tool_skills_in, {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}),
    Tool("pdf_to_text", "Extract text from a PDF file.", tool_pdf_to_text, {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
]

GMAIL_TOOLS: list[Tool] = [
    Tool("gmail_inbox", "List inbox messages. Use Gmail search syntax for query, e.g. 'from:linkedin.com subject:job is:unread'. Default query UNSEEN.", tool_gmail_inbox, {"type": "object", "properties": {"query": {"type": "string", "description": "Gmail search syntax, e.g. 'from:x subject:job is:unread'"}, "limit": {"type": "integer"}}}),
    Tool("gmail_thread", "Read a full email thread by its numeric id (from gmail_inbox output).", tool_gmail_thread, {"type": "object", "properties": {"message_id": {"type": "string", "description": "numeric message id, e.g. '4948'"}}, "required": ["message_id"]}),
    Tool("gmail_draft", "Save a reply draft to a file for review (does not send).", tool_gmail_draft, {"type": "object", "properties": {"message_id": {"type": "string", "description": "numeric message id from gmail_inbox"}, "reply_body": {"type": "string"}}, "required": ["message_id", "reply_body"]}),
    Tool("gmail_send", "SEND an email via Gmail. Only call after the user has explicitly approved the content.", tool_gmail_send, {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to", "subject", "body"]}),
]

# ------------------------------------------------------------------ market intelligence


def _market_module():
    from . import market as m

    return m


def tool_market_indices() -> str:
    return _market_module().get_provider().get_indices()


def tool_market_quote(symbol: str) -> str:
    s = _market_module().get_provider().get_stock(symbol)
    if s is None:
        raise ToolError(f"unknown symbol: {symbol}")
    return s


def tool_market_stocks() -> str:
    return _market_module().get_provider().list_stocks()


def tool_market_technical(symbol: str) -> str:
    return _market_module().technical_view(symbol)


def tool_market_fundamental(symbol: str) -> str:
    return _market_module().fundamental_view(symbol)


def tool_market_score(symbol: str) -> str:
    return _market_module().market_score(symbol)


def tool_market_signal(symbol: str) -> str:
    return _market_module().signal_engine(symbol)


def tool_market_regime() -> str:
    return _market_module().regime_engine()


def tool_market_screener(min_score: float | None = None, sector: str | None = None,
                         max_pe: float | None = None, min_momentum: float | None = None,
                         min_market_cap: float | None = None) -> str:
    filters = {}
    if min_score is not None:
        filters["min_score"] = min_score
    if sector:
        filters["sector"] = sector
    if max_pe is not None:
        filters["max_pe"] = max_pe
    if min_momentum is not None:
        filters["min_momentum"] = min_momentum
    if min_market_cap is not None:
        filters["min_market_cap"] = min_market_cap
    return _market_module().screener(filters)


def tool_market_news(symbol: str | None = None) -> str:
    return _market_module().news_sentiment(symbol)


def tool_market_brief() -> str:
    return _market_module().market_brief()


def tool_position_size(symbol: str, capital: float, risk_per_trade_pct: float = 2.0) -> str:
    return _market_module().position_size(symbol, capital, risk_per_trade_pct)


def tool_portfolio_risk(positions: list[dict[str, Any]], capital: float) -> str:
    return _market_module().portfolio_risk(positions, capital)


def tool_backtest(symbol: str, entry_rule: str = "EMA+RSI", exit_rule: str = "stop/target",
                  stop_loss_pct: float = 8.0, target_pct: float | None = None, days: int = 500) -> str:
    return _market_module().backtest(symbol, entry_rule, exit_rule, stop_loss_pct, target_pct, days)


def tool_paper_portfolio() -> str:
    return _market_module().paper_portfolio()


def tool_paper_buy(symbol: str, quantity: int) -> str:
    return _market_module().paper_buy(symbol, quantity)


def tool_paper_sell(symbol: str, quantity: int) -> str:
    return _market_module().paper_sell(symbol, quantity)


# ------------------------------------------------------------------ broker execution (Upstox)


def _broker_module():
    from . import upstox as u

    return u


def _broker_manager():
    return _broker_module().OrderManager()


def _as_json(data) -> str:
    import json as _json

    try:
        return _json.dumps(data, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(data)


def tool_broker_status() -> str:
    """Read-only broker status. Always safe (no gate needed)."""
    return _as_json(_broker_manager().status())


def tool_broker_place(
    symbol: str,
    quantity: int,
    transaction_type: str,
    order_type: str = "MARKET",
    product: str = "I",
    price: float = 0.0,
    trigger_price: float = 0.0,
) -> str:
    """Place a broker order via Upstox. Fail-closed: refused unless UPSTOX_MODE
    is configured; live orders also require LIVE_TRADING_ENABLED=true."""
    return _as_json(_broker_manager().place(
        symbol=symbol,
        quantity=quantity,
        transaction_type=transaction_type,
        order_type=order_type,
        product=product,
        price=price,
        trigger_price=trigger_price,
    ))


def tool_broker_modify(order_id: str, quantity: int | None = None,
                       price: float | None = None,
                       trigger_price: float | None = None) -> str:
    return _as_json(_broker_manager().modify(
        order_id, quantity=quantity, price=price, trigger_price=trigger_price))


def tool_broker_cancel(order_id: str) -> str:
    return _as_json(_broker_manager().cancel(order_id))


def tool_broker_order_status(order_id: str) -> str:
    return _as_json(_broker_manager().order_status(order_id))


def tool_broker_portfolio() -> str:
    return _as_json(_broker_manager().portfolio())


def tool_broker_audit() -> str:
    """Broker order audit trail (SEBI retention-ready)."""
    import json as _json

    f = _broker_module().broker_audit_file()
    if not f.exists():
        return _as_json({"lines": 0, "file": str(f)})
    lines = f.read_text(encoding="utf-8").strip().splitlines()
    return _as_json({"lines": len(lines), "file": str(f), "tail": [json.loads(l) for l in lines[-5:]]})


def tool_broker_reconcile() -> str:
    """Reconcile internal expected positions vs broker-reported positions.
    FAIL-CLOSED: refused when the broker is OFF. Verdict: MATCHED / DRIFT / FLAT."""
    return _as_json(_broker_module().reconcile())


# ------------------------------------------------------------------ knowledge retrieval (RAG)


def _rag_module():
    from . import rag as r

    return r


def tool_rag_query(question: str, top_k: int = 5) -> str:
    """Retrieve the most relevant knowledge chunks for a question from the
    indexed corpus (SEBI/NSE rules, broker API docs, risk policies, strategy
    docs, playbooks). Answers should be grounded in these chunks and cited."""
    return _as_json(_rag_module().rag_query(question, top_k))


def tool_rag_index(path: str | None = None) -> str:
    """Rebuild the knowledge index from the corpus directory (optionally a
    different directory). New/changed documents are picked up."""
    return _as_json(_rag_module().rag_index(path))


def tool_rag_status() -> str:
    """Knowledge index status: number of documents, chunks, terms indexed."""
    return _as_json(_rag_module().rag_status())


# ------------------------------------------------------------------ option chain intelligence


def _options_module():
    from . import options as o

    return o


def _jsonish(data, max_chars: int = 6000) -> str:
    import json as _json

    try:
        text = _json.dumps(data, ensure_ascii=False, default=str)
    except TypeError:
        text = str(data)
    return text[:max_chars]


def tool_option_chain(underlying: str = "NIFTY", expiry: str | None = None) -> str:
    return _jsonish(_options_module().analyze_chain(underlying, expiry))


def tool_option_metrics(underlying: str = "NIFTY", expiry: str | None = None) -> str:
    a = _options_module().analyze_chain(underlying, expiry)
    return _jsonish(a["analytics"])


def tool_option_support_resistance(underlying: str = "NIFTY", expiry: str | None = None) -> str:
    a = _options_module().analyze_chain(underlying, expiry)
    return _jsonish(a["support_resistance"])


def tool_option_unusual(underlying: str = "NIFTY", expiry: str | None = None) -> str:
    a = _options_module().analyze_chain(underlying, expiry)
    return _jsonish(a["analytics"]["unusual_activity"])


def tool_option_scenarios(underlying: str = "NIFTY", expiry: str | None = None) -> str:
    a = _options_module().analyze_chain(underlying, expiry)
    return _jsonish(a["scenarios"])


def tool_option_signal(underlying: str = "NIFTY", expiry: str | None = None) -> str:
    a = _options_module().analyze_chain(underlying, expiry)
    return _jsonish(a["signal"])


def tool_option_brief(underlying: str = "NIFTY", expiry: str | None = None) -> str:
    return _options_module().format_brief(_options_module().analyze_chain(underlying, expiry))


def tool_option_strategy(underlying: str = "NIFTY", expiry: str | None = None) -> str:
    a = _options_module().analyze_chain(underlying, expiry)
    return _jsonish(a["strategies"] + a["suggestions"])


def tool_option_paper_open(underlying: str, expiry: str, strike: float, option_type: str,
                           action: str, quantity: int, entry_price: float) -> str:
    p = _options_module().OptionsPaperEngine()
    pos = p.open(underlying, expiry, strike, option_type, action, quantity, entry_price)
    return _jsonish({"opened": pos.__dict__, "note": "Paper trade only — no real money."})


def tool_option_paper_positions() -> str:
    return _jsonish(_options_module().OptionsPaperEngine().positions())


def tool_option_backtest(strategy: str = "iron_condor", notional: float = 100000.0,
                         hold_days: int = 30, premium_pct: float = 0.04) -> str:
    return _jsonish(_options_module().OptionsBacktest().run(
        strategy, notional, hold_days, premium_pct))


def _intel_module():
    from . import options_intel as oi

    return oi


def tool_option_intel(underlying: str = "NIFTY", expiry: str | None = None) -> str:
    return _jsonish(_intel_module().intelligence_report(underlying, expiry, record=True))


def tool_option_futures(underlying: str = "NIFTY", expiry: str | None = None) -> str:
    a = _options_module().analyze_chain(underlying, expiry)
    meta = a["meta"]
    f = _intel_module().FuturesAnalytics(meta["underlying"], meta["expiry"], meta["spot"])
    return _jsonish(f.future_with_basis())


def tool_option_expiry(underlying: str = "NIFTY", expiry: str | None = None) -> str:
    a = _options_module().analyze_chain(underlying, expiry)
    meta = a["meta"]
    next_exp = meta["expiries"][1] if len(meta["expiries"]) > 1 else None
    return _jsonish(_intel_module().ExpiryAnalytics(
        meta["underlying"], meta["expiry"], next_exp).compare())


def tool_option_iv_stats(underlying: str = "NIFTY", expiry: str | None = None) -> str:
    a = _options_module().analyze_chain(underlying, expiry)
    return _jsonish(_intel_module().VolStats(underlying, a).all())


def tool_option_velocity(underlying: str = "NIFTY", minutes: int = 120) -> str:
    return _jsonish(_intel_module().SnapshotHistoryStore().velocity(underlying, minutes))


def tool_option_no_trade(underlying: str = "NIFTY", expiry: str | None = None) -> str:
    a = _options_module().analyze_chain(underlying, expiry)
    strategy = a["strategies"][0] if a.get("strategies") else None
    liq = _intel_module().LiquidityExecution(a["contracts"], a["meta"]["spot"]).quote_quality()
    return _jsonish(_intel_module().NoTradeEngine().decide(
        a, strategy, liquidity_grade=liq.get("grade", "LOW"), data_ok=True))


def tool_option_signal_performance(strategy: str = "all") -> str:
    return _jsonish(_intel_module().SignalsPerformance().stats())


def tool_option_events() -> str:
    return _jsonish(_intel_module().EventCalendar().upcoming())


MARKET_TOOLS: list[Tool] = [
    Tool("market_indices", "List tracked market indices (NIFTY 50, SENSEX, etc.) with change and status.", tool_market_indices, {"type": "object", "properties": {}}),
    Tool("market_quote", "Get a stock quote (price, change, market cap, valuation) by symbol.", tool_market_quote, {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
    Tool("market_stocks", "List all stocks in the tracked market universe.", tool_market_stocks, {"type": "object", "properties": {}}),
    Tool("market_technical", "Technical analysis of a stock: trend, momentum, volatility, volume, support/resistance.", tool_market_technical, {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
    Tool("market_fundamental", "Fundamental view of a stock: valuation, profitability, leverage, dividend yield.", tool_market_fundamental, {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
    Tool("market_score", "Transparent factor score (0-100) for a stock with per-factor evidence.", tool_market_score, {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
    Tool("market_signal", "Composite buy/hold/sell signal for a stock built from multiple factors with confidence.", tool_market_signal, {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
    Tool("market_regime", "Current market regime (Bull/Bear/Sideways) with breadth and risk tone.", tool_market_regime, {"type": "object", "properties": {}}),
    Tool("market_screener", "Screen the stock universe by filters (score, sector, P/E, momentum, market cap).", tool_market_screener, {"type": "object", "properties": {"min_score": {"type": "number"}, "sector": {"type": "string"}, "max_pe": {"type": "number"}, "min_momentum": {"type": "number"}, "min_market_cap": {"type": "number"}}}),
    Tool("market_news", "Market news feed with sentiment, optionally filtered by stock symbol.", tool_market_news, {"type": "object", "properties": {"symbol": {"type": "string"}}}),
    Tool("market_brief", "One-line market brief: regime, index moves, breadth, top news.", tool_market_brief, {"type": "object", "properties": {}}),
    Tool("position_size", "Compute position size from capital and risk-per-trade so max loss is capped.", tool_position_size, {"type": "object", "properties": {"symbol": {"type": "string"}, "capital": {"type": "number"}, "risk_per_trade_pct": {"type": "number"}}, "required": ["symbol", "capital"]}),
    Tool("portfolio_risk", "Check portfolio exposure, sector and single-position concentration.", tool_portfolio_risk, {"type": "object", "properties": {"positions": {"type": "array", "items": {"type": "object"}}, "capital": {"type": "number"}}, "required": ["positions", "capital"]}),
    Tool("backtest", "Backtest a strategy on real historical OHLC (delayed demo fallback offline) and return performance metrics.", tool_backtest, {"type": "object", "properties": {"symbol": {"type": "string"}, "entry_rule": {"type": "string"}, "exit_rule": {"type": "string"}, "stop_loss_pct": {"type": "number"}, "target_pct": {"type": "number"}, "days": {"type": "integer"}}, "required": ["symbol"]}),
    Tool("paper_portfolio", "Show the simulated paper-trading portfolio (positions, cash, P&L).", tool_paper_portfolio, {"type": "object", "properties": {}}),
    Tool("paper_buy", "Execute a simulated paper buy (no real money) at the current quoted price.", tool_paper_buy, {"type": "object", "properties": {"symbol": {"type": "string"}, "quantity": {"type": "integer"}}, "required": ["symbol", "quantity"]}),
    Tool("paper_sell", "Execute a simulated paper sell (no real money) at the current quoted price.", tool_paper_sell, {"type": "object", "properties": {"symbol": {"type": "string"}, "quantity": {"type": "integer"}}, "required": ["symbol", "quantity"]}),
]

OPTION_TOOLS: list[Tool] = [
    Tool("option_chain", "Full NIFTY/BANKNIFTY/FINNIFTY/SENSEX option-chain analysis: OI, PCR, IV, max pain, support/resistance, scenarios, signal, strategies.", tool_option_chain, {"type": "object", "properties": {"underlying": {"type": "string", "enum": ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]}, "expiry": {"type": "string"}}}),
    Tool("option_metrics", "Option-chain calculated metrics: OI, OI change, PCR (OI and volume), IV smile/regime/skew, max pain, expected move, liquidity, unusual activity.", tool_option_metrics, {"type": "object", "properties": {"underlying": {"type": "string"}, "expiry": {"type": "string"}}}),
    Tool("option_support_resistance", "Support/resistance levels derived from option OI clusters with confidence.", tool_option_support_resistance, {"type": "object", "properties": {"underlying": {"type": "string"}, "expiry": {"type": "string"}}}),
    Tool("option_unusual_activity", "Unusual option activity: volume-to-OI spikes (evidence, not a trade recommendation).", tool_option_unusual, {"type": "object", "properties": {"underlying": {"type": "string"}, "expiry": {"type": "string"}}}),
    Tool("option_scenarios", "Bull / bear / range scenarios with invalidation levels (scenario, not prediction).", tool_option_scenarios, {"type": "object", "properties": {"underlying": {"type": "string"}, "expiry": {"type": "string"}}}),
    Tool("option_signal", "Composite option-chain signal score (0-100) with documented weights and confidence gating on data quality.", tool_option_signal, {"type": "object", "properties": {"underlying": {"type": "string"}, "expiry": {"type": "string"}}}),
    Tool("option_brief", "Concise natural-language option-chain brief with the data-quality badge.", tool_option_brief, {"type": "object", "properties": {"underlying": {"type": "string"}, "expiry": {"type": "string"}}}),
    Tool("option_strategy", "Evaluate option strategies (long call/put, spreads, strangle, iron condor, covered call) with payoff, breakevens, max profit/loss and est. margin.", tool_option_strategy, {"type": "object", "properties": {"underlying": {"type": "string"}, "expiry": {"type": "string"}}}),
    Tool("option_paper_open", "Open a PAPER option position (no real money) at a given entry price.", tool_option_paper_open, {"type": "object", "properties": {"underlying": {"type": "string"}, "expiry": {"type": "string"}, "strike": {"type": "number"}, "option_type": {"type": "string", "enum": ["CE", "PE"]}, "action": {"type": "string", "enum": ["BUY", "SELL"]}, "quantity": {"type": "integer"}, "entry_price": {"type": "number"}}, "required": ["underlying", "expiry", "strike", "option_type", "action", "quantity", "entry_price"]}),
    Tool("option_paper_positions", "List open/closed paper option positions with P&L.", tool_option_paper_positions, {"type": "object", "properties": {}}),
    Tool("option_backtest", "Run a simulated options strategy backtest with costs and slippage (approximate, not real results).", tool_option_backtest, {"type": "object", "properties": {"strategy": {"type": "string"}, "notional": {"type": "number"}, "hold_days": {"type": "integer"}, "premium_pct": {"type": "number"}}}),
    Tool("option_intel", "Full option-intelligence report: futures (ESTIMATED), expiry/rollover, OI velocity, IV rank/crush/skew, liquidity, trade quality, NO-TRADE decision card, breadth, events, failover.", tool_option_intel, {"type": "object", "properties": {"underlying": {"type": "string", "enum": ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]}, "expiry": {"type": "string"}}}),
    Tool("option_futures", "Estimated futures value and basis from cost-of-carry (labelled ESTIMATED; NSE has no reachable futures feed).", tool_option_futures, {"type": "object", "properties": {"underlying": {"type": "string"}, "expiry": {"type": "string"}}}),
    Tool("option_expiry", "Near-v-next expiry comparison: PCR, IV, DTE, max pain, term shape.", tool_option_expiry, {"type": "object", "properties": {"underlying": {"type": "string"}, "expiry": {"type": "string"}}}),
    Tool("option_iv_stats", "IV rank/percentile from recorded history, crush detection, put-call skew, smile shape.", tool_option_iv_stats, {"type": "object", "properties": {"underlying": {"type": "string"}, "expiry": {"type": "string"}}}),
    Tool("option_velocity", "Snapshot-window velocity: per-hour change in ATM IV, OI, spot, PCR between oldest/newest.", tool_option_velocity, {"type": "object", "properties": {"underlying": {"type": "string"}, "minutes": {"type": "integer"}}}),
    Tool("option_no_trade", "Explicit NO-TRADE decision with auditable reasons; staying flat is a valid outcome.", tool_option_no_trade, {"type": "object", "properties": {"underlying": {"type": "string"}, "expiry": {"type": "string"}}}),
    Tool("option_signal_performance", "Closed-signal performance: win rate, expectancy, drawdown, kill/observation verdict.", tool_option_signal_performance, {"type": "object", "properties": {"strategy": {"type": "string"}}}),
    Tool("option_events", "Upcoming macro/earnings events with typical IV-impact so expiry/IV risk can be gated.", tool_option_events, {"type": "object", "properties": {}}),
]

RISK_TOOLS: list[Tool] = [
    Tool("position_size", "Compute position size from capital and risk-per-trade so max loss is capped.", tool_position_size, {"type": "object", "properties": {"symbol": {"type": "string"}, "capital": {"type": "number"}, "risk_per_trade_pct": {"type": "number"}}, "required": ["symbol", "capital"]}),
    Tool("portfolio_risk", "Check portfolio exposure, sector and single-position concentration.", tool_portfolio_risk, {"type": "object", "properties": {"positions": {"type": "array", "items": {"type": "object"}}, "capital": {"type": "number"}}, "required": ["positions", "capital"]}),
    Tool("market_regime", "Current market regime (Bull/Bear/Sideways) with breadth and risk tone.", tool_market_regime, {"type": "object", "properties": {}}),
    Tool("market_signal", "Composite buy/hold/sell signal for a stock built from multiple factors with confidence.", tool_market_signal, {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
    Tool("paper_portfolio", "Show the simulated paper-trading portfolio (positions, cash, P&L).", tool_paper_portfolio, {"type": "object", "properties": {}}),
    Tool("paper_buy", "Execute a simulated paper buy (no real money) at the current quoted price.", tool_paper_buy, {"type": "object", "properties": {"symbol": {"type": "string"}, "quantity": {"type": "integer"}}, "required": ["symbol", "quantity"]}),
    Tool("paper_sell", "Execute a simulated paper sell (no real money) at the current quoted price.", tool_paper_sell, {"type": "object", "properties": {"symbol": {"type": "string"}, "quantity": {"type": "integer"}}, "required": ["symbol", "quantity"]}),
]

BROKER_TOOLS: list[Tool] = [
    Tool("broker_status", "Read-only broker status (mode, ready, algo id, rate cap). Safe to call anytime.", tool_broker_status, {"type": "object", "properties": {}}),
    Tool("broker_place", "Place a broker order via Upstox. FAIL-CLOSED: refused unless UPSTOX_MODE is configured; live orders also require LIVE_TRADING_ENABLED=true. Prefer paper_buy/paper_sell for simulation.", tool_broker_place, {"type": "object", "properties": {"symbol": {"type": "string"}, "quantity": {"type": "integer"}, "transaction_type": {"type": "string", "enum": ["BUY", "SELL"]}, "order_type": {"type": "string", "enum": ["MARKET", "LIMIT", "SL", "SL-M"]}, "product": {"type": "string", "enum": ["I", "D"]}, "price": {"type": "number"}, "trigger_price": {"type": "number"}}, "required": ["symbol", "quantity", "transaction_type"]}),
    Tool("broker_modify", "Modify an open broker order by order_id.", tool_broker_modify, {"type": "object", "properties": {"order_id": {"type": "string"}, "quantity": {"type": "integer"}, "price": {"type": "number"}, "trigger_price": {"type": "number"}}, "required": ["order_id"]}),
    Tool("broker_cancel", "Cancel an open broker order by order_id.", tool_broker_cancel, {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}),
    Tool("broker_order_status", "Get the status/details of a broker order by order_id.", tool_broker_order_status, {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}),
    Tool("broker_portfolio", "Broker positions, holdings and funds (read-only).", tool_broker_portfolio, {"type": "object", "properties": {}}),
    Tool("broker_audit", "Broker order audit trail (SEBI retention-ready, hash-chained).", tool_broker_audit, {"type": "object", "properties": {}}),
    Tool("broker_reconcile", "Reconcile internal expected positions vs broker positions. FAIL-CLOSED when broker OFF. Verdict: MATCHED / DRIFT / FLAT.", tool_broker_reconcile, {"type": "object", "properties": {}}),
]

RAG_TOOLS: list[Tool] = [
    Tool("rag_query", "Retrieve relevant knowledge chunks (SEBI/NSE rules, broker API docs, risk policies, strategy docs) for a question; ground answers in these and cite the source.", tool_rag_query, {"type": "object", "properties": {"question": {"type": "string"}, "top_k": {"type": "integer"}}, "required": ["question"]}),
    Tool("rag_index", "Rebuild the knowledge index from the corpus directory (optionally a different directory).", tool_rag_index, {"type": "object", "properties": {"path": {"type": "string"}}}),
    Tool("rag_status", "Knowledge index status: documents, chunks, terms, index file.", tool_rag_status, {"type": "object", "properties": {}}),
]
