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


def _resolve_read_path(path: str) -> Path:
    """Resolve a file path for reading: try the outputs dir first, then the project root.

    This lets agents read both files they generate (outputs/...) and source files
    in the workspace (e.g. ResumeIQ/...).
    """
    p = Path(path).expanduser()
    if p.is_absolute():
        _safe_path(str(p), OUTPUTS_DIR)
        _safe_path(str(p), BASE_DIR.parent)
        return p.resolve()
    for root in (OUTPUTS_DIR, BASE_DIR.parent):
        candidate = root / p
        if candidate.is_file():
            return candidate
    return (BASE_DIR.parent / p).resolve()


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
    p = _safe_path(path, OUTPUTS_DIR)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {p}"


def tool_append_file(path: str, content: str) -> str:
    p = _safe_path(path, OUTPUTS_DIR)
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


# ---- Memory tools (per-agent notes, persisted as JSON) ----


def _memory_file(agent: str) -> Path:
    return DATA_DIR / f"{agent}_memory.json"


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
    row = f"{date},{type},{category},{description.replace(',', ' ').strip()},{amount:.2f}\n"
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


COMMON_TOOLS: list[Tool] = [
    Tool("get_time", "Get the current date and time.", tool_get_time, {"type": "object", "properties": {}}),
    Tool("list_files", "List files in a directory (relative to the project root).", tool_list_files, {"type": "object", "properties": {"dirpath": {"type": "string", "description": "directory path"}}}),
    Tool("read_file", "Read a text file relative to the project root.", tool_read_file, {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
    Tool("write_file", "Write a text file under the outputs/ directory.", tool_write_file, {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}),
    Tool("append_file", "Append text to a file under the outputs/ directory.", tool_append_file, {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}),
    Tool("web_search", "Search the web for current information.", tool_web_search, {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}),
    Tool("remember", "Save a note/fact for this agent to remember later.", tool_remember, {"type": "object", "properties": {"agent": {"type": "string"}, "note": {"type": "string"}}, "required": ["agent", "note"]}),
    Tool("recall", "Show the saved notes for this agent.", tool_recall, {"type": "object", "properties": {"agent": {"type": "string"}}, "required": ["agent"]}),
]

FINANCE_TOOLS: list[Tool] = [
    Tool("ledger_add", "Record an income or expense in the finance ledger.", tool_ledger_add, {"type": "object", "properties": {"amount": {"type": "number"}, "category": {"type": "string"}, "description": {"type": "string"}, "type": {"type": "string", "enum": ["income", "expense"]}, "date": {"type": "string"}}, "required": ["amount", "category", "description"]}),
    Tool("ledger_summary", "Summarise income/expenses for a period.", tool_ledger_summary, {"type": "object", "properties": {"period": {"type": "string", "enum": ["all", "month", "year"]}}}),
]


def build_tools(agent: str, extra: list[Tool] | None = None) -> list[Tool]:
    return COMMON_TOOLS + (extra or [])


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
