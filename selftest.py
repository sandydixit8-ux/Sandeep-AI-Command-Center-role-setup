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
# Keep the suite offline: clear any real credentials from .env so the gmail/sheets
# checks exercise the "not configured" paths deterministically.
os.environ["AGENT_GMAIL_USER"] = ""
os.environ["AGENT_GMAIL_APP_PASSWORD"] = ""
os.environ["AGENT_GOOGLE_SERVICE_ACCOUNT_FILE"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents_core.agent import Agent
from agents_core.registry import get_agent, list_agents
from agents_core.llm import _parse_failed_generation, _parse_tool_arguments, _retry_after
from agents_core.scoring import score_resume, extract_skills
from agents_core.tools import (
    execute_tool,
    build_tools,
    FINANCE_TOOLS,
    GMAIL_TOOLS,
    JOBSEARCH_TOOLS,
    COMMON_TOOLS,
    MARKET_TOOLS,
    RISK_TOOLS,
)
from agents_core import market as mkt

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
    for expected in ("commander", "exec", "finance", "bd", "jobsearch", "marketing", "docs", "coach", "market", "risk"):
        check(f"agent registered: {expected}", expected in keys)

    print("\n== commander has all tools ==")
    cmdr = get_agent("commander")
    names = {t.name for t in cmdr.tools}
    for tool in ("ledger_add", "ledger_summary", "skill_match", "gmail_inbox", "gmail_send", "web_search", "write_file", "market_signal", "market_regime"):
        check(f"commander has tool: {tool}", tool in names)
    cmdr.close()

    print("\n== market/risk agents have market tools ==")
    mkt_agent = get_agent("market")
    mkt_names = {t.name for t in mkt_agent.tools}
    for tool in ("market_indices", "market_quote", "market_technical", "market_fundamental", "market_score", "market_signal", "market_screener", "market_brief", "market_news", "paper_buy", "paper_sell"):
        check(f"market agent has tool: {tool}", tool in mkt_names, str(sorted(mkt_names)))
    mkt_agent.close()
    risk_agent = get_agent("risk")
    risk_names = {t.name for t in risk_agent.tools}
    for tool in ("position_size", "portfolio_risk", "market_regime", "paper_portfolio"):
        check(f"risk agent has tool: {tool}", tool in risk_names, str(sorted(risk_names)))
    risk_agent.close()

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

    data_dir = Path(os.environ["AGENT_DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "jd.txt").write_text("Need Python, FastAPI, Docker, kubernetes.", encoding="utf-8")
    r8 = execute_tool(build_tools("docs"), "read_file", {"path": "data/jd.txt"}, "docs")
    check("read_file resolves data/ prefix", r8 == "Need Python, FastAPI, Docker, kubernetes.", r8)
    r9 = execute_tool(JOBSEARCH_TOOLS, "skill_match", {"resume": "data/jd.txt", "jd": "Python developer with Docker and kubernetes."}, "jobsearch")
    check("skill_match reads data/ file", "skill match:" in r9 and "python" in r9, r9[:80])

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

    import email.message as _email

    html_msg = _email.EmailMessage()
    html_msg["From"] = "noreply@example.com"
    html_msg["Subject"] = "Jobs"
    html_msg.set_content("<div><h1>Jobs</h1><p>Python Developer</p></div>", subtype="html")
    from agents_core.gmail import _body_text, _valid_id, _require_id

    check("gmail rejects placeholder id", not _valid_id("<ID>"))
    check("gmail accepts numeric id", _valid_id("4948"))
    try:
        _require_id("nope")
        check("gmail requires numeric id", False, "no error raised")
    except Exception:  # noqa: BLE001
        check("gmail requires numeric id", True)
    body = _body_text(html_msg)
    check("gmail strips html-only body", "Python Developer" in body and "<div>" not in body, body)
    zw_msg = _email.EmailMessage()
    zw_msg.set_content("Hiring\u200b\u200c\u200d\u2060\ufeff now")
    check("gmail strips zero-width chars", "now" in _body_text(zw_msg), repr(_body_text(zw_msg)))

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

    print("\n== market engine: indicators ==")
    closes = [float(i) for i in range(1, 31)]
    sma20 = mkt.sma(closes, 20)
    check("sma(20) has 19 leading Nones", sma20[:19] == [None] * 19 and sma20[-1] == 20.5, str(sma20[-1]))
    ema3 = mkt.ema(closes, 3)
    check("ema(3) length matches", len(ema3) == len(closes) and ema3[0] is None)
    r = mkt.rsi(closes, 14)
    check("rsi of steadily rising series near 100", r is not None and (r[-1] or 0) > 90, str(r[-1]))
    bb = mkt.bollinger(closes, 20)
    check("bollinger upper > mid > lower", bb["upper"][-1] > bb["mid"][-1] > bb["lower"][-1])
    series = mkt.get_provider().ohlc("RELIANCE", 60)
    a = mkt.atr(series, 14)
    check("atr is positive on real series", a[-1] is not None and a[-1] > 0, str(a[-1]))
    check("vwap within price range", 0 < mkt.vwap(series) < 10_000)
    sr = mkt.support_resistance(series)
    check("support < resistance", sr["support"] < sr["resistance"])

    print("\n== market engine: provider ==")
    prov = mkt.get_provider()
    check("default provider is live moneycontrol", prov.name() == "moneycontrol-live", prov.name())
    st = prov.status()
    check("provider status has mode", st["mode"] in ("live", "fallback"), st.get("mode"))
    check("provider status has quality", "quality" in st and st["quality"] != "", st.get("quality"))
    mock_prov = mkt.MockMarketProvider()
    check("mock provider name", mock_prov.name() == "mock-nse-demo")
    check("mock indices labelled delayed", all("Delayed" in i["quality"] for i in mock_prov.get_indices()))
    # Fallback path: a degraded provider must return mock data without raising.
    degraded = mkt.MoneycontrolMarketProvider()
    degraded._degraded_until = 10 ** 12  # force circuit breaker open
    fb_idx = degraded.get_indices()
    check("degraded provider falls back for indices", len(fb_idx) == len(mkt.INDICES) and all("Delayed" in i["quality"] for i in fb_idx), str(len(fb_idx)))
    fb_stock = degraded.get_stock("RELIANCE")
    check("degraded provider falls back for stocks", fb_stock is not None and fb_stock["source"] == "mock-nse-demo", str(fb_stock and fb_stock.get("source")))
    fb_ohlc = degraded.ohlc("RELIANCE", 30)
    check("degraded provider falls back for ohlc", len(fb_ohlc) == 30 and all("date" in b for b in fb_ohlc), str(len(fb_ohlc)))
    check("degraded status reports fallback", degraded.status()["mode"] == "fallback", degraded.status().get("mode"))

    print("\n== market engine: scoring ==")
    sc = mkt.market_score("TCS")
    check("score in 0..100", 0 <= sc["score"] <= 100, str(sc["score"]))
    check("score has 6 weighted factors", len(sc["factors"]) == 6 and all("evidence" in f for f in sc["factors"]))
    check("score model tag", sc["model"] == "mkt-score-v1")

    print("\n== market engine: composite signal ==")
    sig = mkt.signal_engine("TCS")
    check("signal is one of the allowed set", sig["signal"] in ("BUY CANDIDATE", "SELL / REDUCE RISK", "HOLD / WATCH", "NO SIGNAL"), sig["signal"])
    check("signal confidence 0..100", 0 <= sig["confidence"] <= 100)
    check("signal combines multiple factors", sig["total_factors"] >= 4 and len(sig["checks"]) == sig["total_factors"])
    check("signal carries disclaimer", "not investment advice" in sig["disclaimer"])
    lone = mkt.signal_engine("INFY")
    check("composite signal respects evidence strength", lone["evidence_strength"] in ("High", "Moderate", "Low"))

    print("\n== market engine: regime ==")
    reg = mkt.regime_engine()
    check("regime detected", reg["regime"].split(" ")[0] in ("Bull", "Bear", "Sideways"), reg["regime"])
    check("regime has breadth + tone", 0 <= reg["breadth"] <= 1 and reg["tone"] in ("Risk-On", "Risk-Off", "Mixed"))
    check("regime has evidence", "evidence" in reg and len(reg["evidence"]) > 5)

    print("\n== market engine: screener ==")
    all_stocks = mkt.screener({})
    check("screener returns full universe", len(all_stocks) == len(mkt.STOCKS))
    filtered = mkt.screener({"sector": "Banking"})
    check("screener filters by sector", all(s["sector"] == "Banking" for s in filtered) and 0 < len(filtered) < len(all_stocks))
    scored = mkt.screener({"min_score": 60})
    check("screener min_score respected", all(s["ai_score"] >= 60 for s in scored))
    sorted_scores = [s["ai_score"] for s in all_stocks]
    check("screener sorted by score desc", sorted_scores == sorted(sorted_scores, reverse=True))

    print("\n== market engine: position sizing ==")
    ps = mkt.position_size("RELIANCE", 100_000, risk_per_trade_pct=2.0)
    check("sizing caps max risk", ps["max_risk"] == 2000.0, str(ps["max_risk"]))
    check("sizing qty is floor of risk/stop", ps["max_quantity"] >= 1 and ps["max_quantity"] * (ps["price"] - ps["stop_loss_price"]) <= 2000.0 + 1e-6)
    check("sizing explains the math", "max risk" in ps["explanation"].lower() and "stop distance" in ps["explanation"].lower())

    print("\n== market engine: portfolio risk ==")
    pr = mkt.portfolio_risk([{"symbol": "RELIANCE", "value": 400_000}, {"symbol": "INFY", "value": 100_000}], capital=500_000)
    check("portfolio exposure computed", pr["exposure_pct"] == 100.0, str(pr["exposure_pct"]))
    check("sector concentration flagged", any("concentration" in f for f in pr["flags"]))
    pr2 = mkt.portfolio_risk(
        [{"symbol": "RELIANCE", "value": 20_000}, {"symbol": "ITC", "value": 25_000}, {"symbol": "SUNPHARMA", "value": 20_000}],
        capital=500_000,
    )
    check("low-risk portfolio has no flags", any("risk limits" in f for f in pr2["flags"]), str(pr2["flags"]))

    print("\n== market engine: backtest ==")
    bt = mkt.backtest("RELIANCE", "EMA+RSI", "stop/target", stop_loss_pct=8.0, days=500)
    check("backtest returns metrics", "cagr_pct" in bt and "sharpe" in bt and "max_drawdown_pct" in bt)
    check("backtest metrics are numeric", isinstance(bt["sharpe"], float) and isinstance(bt["total_return_pct"], float))
    check("backtest equity curve non-empty", len(bt["equity_curve"]) >= 30)
    check("backtest anti-overfit grade", bt["strategy_quality"]["grade"] in ("Good", "Caution", "Poor"))
    check("backtest disclaimer present", "does not guarantee" in bt["disclaimer"])

    print("\n== market engine: paper trading ==")
    pf0 = mkt.paper_portfolio()
    check("paper starts with demo capital", pf0["cash"] == 1_000_000 and pf0["mode"] == "paper")
    b = mkt.paper_buy("TCS", 10)
    check("paper buy executes simulated", "PAPER TRADE" in b["status"] and b["cash"] < 1_000_000)
    pf1 = mkt.paper_portfolio()
    check("paper portfolio reflects position", any(p["symbol"] == "TCS" for p in pf1["positions"]))
    s = mkt.paper_sell("TCS", 10)
    check("paper sell clears position", s["symbol"] == "TCS" and not any(p["symbol"] == "TCS" for p in mkt.paper_portfolio()["positions"]))
    check("paper sell logs pnl", any(t["symbol"] == "TCS" and t["type"] == "SELL" for t in mkt.paper_portfolio()["trades"]))

    print("\n== market engine: market tools via execute_tool ==")
    rq = execute_tool(MARKET_TOOLS, "market_quote", {"symbol": "TCS"}, "market")
    check("market_quote tool works", "Tata Consultancy" in rq, rq[:80])
    rr = execute_tool(MARKET_TOOLS, "market_regime", {}, "market")
    check("market_regime tool works", '"regime"' in rr, rr[:80])
    rs = execute_tool(MARKET_TOOLS, "market_signal", {"symbol": "INFY"}, "market")
    check("market_signal tool works", '"signal"' in rs and '"confidence"' in rs, rs[:80])
    rp = execute_tool(RISK_TOOLS, "position_size", {"symbol": "RELIANCE", "capital": 200_000}, "risk")
    check("position_size tool works", '"max_risk": 4000.0' in rp, rp[:120])
    rerr = execute_tool(MARKET_TOOLS, "market_quote", {"symbol": "NOTREAL"}, "market")
    check("market_quote errors on unknown symbol", "error" in rerr, rerr[:80])

    print("\n== market engine: news + brief ==")
    news = mkt.news_sentiment()
    check("news feed non-empty", len(news) > 0)
    ns = mkt.news_sentiment("RELIANCE")
    check("news filters by sector", all(n["sector"] in ("Energy", "Economy", "Global") for n in ns))
    brief = mkt.market_brief()
    check("brief has summary + regime", "summary" in brief and "regime" in brief and len(brief["summary"]) > 20)

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
    res = _parse_failed_generation(
        '{"error":{"message":"tool call validation failed: attempted to call tool \'skills_in[]{\\"text\\": \\"CERTIFICATIONS\\\\nbullets {here}\\"}</function>"}}'
    )
    check("rescue parses []-suffix from message field", res is not None and res.tool_calls[0].name == "skills_in", str(res.tool_calls if res else None))
    check("rescue preserves brace inside string", res is not None and res.tool_calls[0].arguments.get("text") == "CERTIFICATIONS\nbullets {here}", str(res.tool_calls if res else None))
    res = _parse_failed_generation("some plain text with <function=notes_add:{\"text\": \"x\"}> no json")
    check("rescue parses from raw body", res is not None and res.tool_calls[0].name == "notes_add", str(res.tool_calls if res else None))

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
