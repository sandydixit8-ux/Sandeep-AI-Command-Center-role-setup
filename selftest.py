"""Offline self-test for the Agents suite. No API key required.

Run:  python selftest.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

tmp = tempfile.mkdtemp(prefix="agents_selftest_")
os.environ["AGENT_LLM_PROVIDER"] = "mock"
os.environ["AGENT_DATA_DIR"] = str(Path(tmp) / "data")
os.environ["AGENT_OUTPUTS_DIR"] = str(Path(tmp) / "outputs")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents_core.agent import Agent
from agents_core.registry import get_agent, list_agents
from agents_core.tools import execute_tool, build_tools, FINANCE_TOOLS

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL += 1
        print(f"FAIL  {label}  {detail}")


def main() -> int:
    print("== registry ==")
    keys = list_agents()
    for expected in ("exec", "finance", "bd", "jobsearch", "marketing", "docs", "coach"):
        check(f"agent registered: {expected}", expected in keys)

    print("\n== tool schemas ==")
    t = build_tools("docs")[0]
    an = t.anthropic_schema()
    oa = t.openai_schema()
    check("anthropic schema shape", an["name"] and "input_schema" in an)
    check("openai schema shape", oa["function"]["name"] and "parameters" in oa["function"])

    print("\n== finance ledger tools ==")
    r1 = execute_tool(FINANCE_TOOLS, "ledger_add", {"amount": 1200, "category": "rent", "description": "office rent", "type": "expense", "date": "2026-08-01"}, "finance")
    check("ledger_add records", "recorded" in r1, r1)
    r2 = execute_tool(FINANCE_TOOLS, "ledger_add", {"amount": 5000, "category": "consulting", "description": "client invoice", "type": "income", "date": "2026-08-03"}, "finance")
    check("ledger_add income", "recorded" in r2, r2)
    r3 = execute_tool(FINANCE_TOOLS, "ledger_summary", {"period": "month"}, "finance")
    check("ledger_summary reports income", "5000.00" in r3, r3)
    check("ledger_summary reports expenses", "1200.00" in r3, r3)
    check("ledger_summary reports net", "3800.00" in r3, r3)
    r4 = execute_tool(FINANCE_TOOLS, "ledger_add", {"amount": -5, "category": "x", "description": "bad"}, "finance")
    check("ledger_add rejects bad type", "error" in r4, r4)

    print("\n== tool loop end-to-end (mock LLM) ==")
    agent = get_agent("docs")
    out = agent.run('Please write a file: @tool write_file {"path": "test.md", "content": "hello from agent"}')
    check("mock returned final text", out.startswith("[mock] task complete"), out)
    f = Path(os.environ["AGENT_OUTPUTS_DIR"]) / "test.md"
    check("tool wrote output file", f.exists() and f.read_text() == "hello from agent")
    check("tool result appended to history", any(m["role"] == "tool" for m in agent.history))
    agent.close()

    print("\n== file tools ==")
    r5 = execute_tool(build_tools("exec"), "write_file", {"path": "notes.md", "content": "x"}, "exec")
    check("write_file ok", "wrote" in r5, r5)
    r6 = execute_tool(build_tools("exec"), "read_file", {"path": "notes.md"}, "exec")
    check("read_file ok", r6 == "x", r6)
    r7 = execute_tool(build_tools("exec"), "write_file", {"path": "../../evil.md", "content": "x"}, "exec")
    check("path traversal blocked", "error" in r7, r7)

    print(f"\n{'-' * 40}\nresult: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
