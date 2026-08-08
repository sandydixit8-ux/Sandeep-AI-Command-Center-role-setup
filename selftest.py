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
from agents_core.llm import _parse_failed_generation, _parse_tool_arguments, _retry_after
from agents_core.scoring import score_resume, extract_skills
from agents_core.tools import execute_tool, build_tools, FINANCE_TOOLS, GMAIL_TOOLS, JOBSEARCH_TOOLS, COMMON_TOOLS

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
    for expected in ("commander", "exec", "finance", "bd", "jobsearch", "marketing", "docs", "coach"):
        check(f"agent registered: {expected}", expected in keys)

    print("\n== commander has all tools ==")
    cmdr = get_agent("commander")
    names = {t.name for t in cmdr.tools}
    for tool in ("ledger_add", "ledger_summary", "skill_match", "gmail_inbox", "gmail_send", "web_search", "write_file"):
        check(f"commander has tool: {tool}", tool in names)
    cmdr.close()

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
    r5b = execute_tool(build_tools("exec"), "write_file", {"path": "outputs/notes2.md", "content": "y"}, "exec")
    check("write_file no double-prefix", "wrote" in r5b, r5b)
    check("no outputs/outputs nesting", not (Path(os.environ["AGENT_OUTPUTS_DIR"]) / "outputs").exists())
    r6 = execute_tool(build_tools("exec"), "read_file", {"path": "notes.md"}, "exec")
    check("read_file ok", r6 == "x", r6)
    r7 = execute_tool(build_tools("exec"), "write_file", {"path": "../../evil.md", "content": "x"}, "exec")
    check("path traversal blocked", "error" in r7, r7)

    print("\n== skill-match scoring ==")
    resume = "Experienced Python developer. 5 years writing REST APIs with FastAPI, Docker, AWS, git. Project management with agile and jira."
    jd = "Looking for a Python + FastAPI engineer. Must know Docker, AWS, git, CI/CD, Kubernetes. Project management with scrum is a plus."
    result = score_resume(resume, jd)
    check("score computed", isinstance(result["score"], int) and 0 <= result["score"] <= 100, str(result["score"]))
    check("python matched", "python" in result["matched"], str(result["matched"]))
    check("fastapi matched", "fastapi" in result["matched"], str(result["matched"]))
    check("kubernetes flagged as gap", "kubernetes" in result["gaps"], str(result["gaps"]))
    check("ci/cd flagged as gap", "ci/cd" in result["gaps"], str(result["gaps"]))
    check("no fabricated skills", "basket weaving" not in result["matched"])
    r = execute_tool(JOBSEARCH_TOOLS, "skill_match", {"resume": resume, "jd": jd}, "jobsearch")
    check("skill_match tool renders score", "skill match:" in r and "gaps" in r, r[:80])
    rs = execute_tool(JOBSEARCH_TOOLS, "skills_in", {"text": "Python, FastAPI, docker, kubernetes"}, "jobsearch")
    check("skills_in detects known skills", "python" in rs and "docker" in rs and "kubernetes" in rs, rs)

    print("\n== gmail tools (unconfigured) ==")
    rg = execute_tool(GMAIL_TOOLS, "gmail_inbox", {}, "exec")
    check("gmail_inbox gives setup error", "error" in rg and "Gmail is not configured" in rg, rg)
    rg2 = execute_tool(GMAIL_TOOLS, "gmail_send", {"to": "a@b.com", "subject": "hi", "body": "hello"}, "exec")
    check("gmail_send blocked when unconfigured", "error" in rg2, rg2)

    print("\n== sheets tools (unconfigured) ==")
    rs2 = execute_tool(FINANCE_TOOLS, "sheets_push", {}, "finance")
    check("sheets_push gives setup error", "error" in rs2, rs2)

    print("\n== PDF parsing ==")
    try:
        from pypdf import PdfReader  # noqa: F401

        from agents_core.pdfutil import pdf_to_text

        def _make_pdf(path: Path) -> None:
            text = b"Python FastAPI Docker AWS git Kubernetes"
            stream = b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET"
            objs = [
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
                b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            ]
            out = bytearray(b"%PDF-1.4\n")
            offsets = []
            for i, body in enumerate(objs, start=1):
                offsets.append(len(out))
                out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
            xref_pos = len(out)
            out += b"xref\n0 6\n0000000000 65535 f \n"
            for off in offsets:
                out += f"{off:010d} 00000 n \n".encode()
            out += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref_pos).encode() + b"\n%%EOF\n"
            path.write_bytes(bytes(out))

        pdf_dir = Path(os.environ["AGENT_OUTPUTS_DIR"])
        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = pdf_dir / "resume.pdf"
        _make_pdf(pdf_path)

        txt = pdf_to_text(pdf_path)
        check("pdf_to_text extracts page text", "Python" in txt and "Docker" in txt, txt[:120])

        rp = execute_tool(COMMON_TOOLS, "pdf_to_text", {"path": "resume.pdf"}, "docs")
        check("pdf_to_text tool reads by path", "Python" in rp, rp[:120])

        rp2 = execute_tool(COMMON_TOOLS, "pdf_to_text", {"path": "does-not-exist.pdf"}, "docs")
        check("pdf_to_text reports missing file", "error" in rp2, rp2)

        rp3 = execute_tool(JOBSEARCH_TOOLS, "skill_match", {"resume": "resume.pdf", "jd": "Need a Python FastAPI engineer with Docker."}, "jobsearch")
        check("skill_match auto-extracts PDF resume", "skill match:" in rp3, rp3[:80])
    except ImportError:
        check("pypdf installed (for PDF tests)", False, "pip install pypdf")
    except Exception as exc:  # noqa: BLE001
        check("PDF tests ran", False, repr(exc))

    print("\n== streaming ==")
    agent = get_agent("docs")
    events = list(agent.run_stream('Please write a file: @tool write_file {"path": "stream.md", "content": "streamed"}'))
    types = [e["type"] for e in events]
    check("stream yields tool_call event", "tool_call" in types, str(types))
    check("stream yields result event", "result" in types, str(types))
    check("stream result is final text", events[-1]["text"].startswith("[mock] task complete"), events[-1].get("text"))
    sf = Path(os.environ["AGENT_OUTPUTS_DIR"]) / "stream.md"
    check("stream tool executed file write", sf.exists() and sf.read_text() == "streamed")
    agent.close()

    print("\n== failed-generation rescue parser ==")
    import json as _json

    cases = [
        ('{"error":{"failed_generation":"<function=ledger_summary={\\"period\\": \\"month\\"}</function>"}}', "ledger_summary"),
        ('{"error":{"failed_generation":"<function=ledger_summary{\\"period\\": \\"month\\"}</function>"}}', "ledger_summary"),
        ('{"error":{"failed_generation":"<function=ledger_add={ \\"amount\\": 5 }>"}}', "ledger_add"),
        ('{"error":{"failed_generation":"<function=get_time:{}>"}}', "get_time"),
        ('{"error":{"failed_generation":"<function=gmail_inbox:{\\"query\\": \\"job\\"}>"}}', "gmail_inbox"),
    ]
    for body, expected in cases:
        res = _parse_failed_generation(body)
        ok = res is not None and res.tool_calls and res.tool_calls[0].name == expected
        check(f"rescue parser handles: {expected}", ok, str(res.tool_calls if res else None))
    res = _parse_failed_generation('{"error":{"message":"boom"}}')
    check("rescue parser returns None when no generation", res is None)

    print("\n== tool-arguments parsing (Groq 'null' args) ==")
    check("null args become empty dict", _parse_tool_arguments("null") == {})
    check("empty args become empty dict", _parse_tool_arguments("") == {})
    check("bad json becomes empty dict", _parse_tool_arguments("not json") == {})
    check("real args pass through", _parse_tool_arguments('{"period": "month"}') == {"period": "month"})
    check("retry-after parses Groq body", _retry_after('{"error":{"message":"try again in 8.26s"}}') == 8.26)
    check("retry-after default", _retry_after("no hint here") == 8.0)

    print("\n== tool dedup ==")
    cmdr_tools = get_agent("commander")
    names = [t.name for t in cmdr_tools.tools]
    check("commander tools deduped", len(names) == len(set(names)), str(names))
    check("pdf_to_text appears once", names.count("pdf_to_text") == 1, str(names))
    cmdr_tools.close()

    print(f"\n{'-' * 40}\nresult: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
