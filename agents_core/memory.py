"""Trading memory engine — the persistence layer of the memory/feedback loop.

Implements the "MEMORY" and "MEMORY ENGINE" boxes of the trading-agent
architecture:

- MEMORY: every decision, executed trade outcome and extracted lesson is written
  to an append-only, hash-chained JSONL store under ``DATA_DIR/memory/``.
- MEMORY ENGINE: ``memory_summary()`` folds recent history into a compact
  context block (win rate, expectancy, per-symbol results, replayed lessons)
  that the trading agent reads before reasoning — the day-by-day feedback loop.

Design properties:
- Append-only, size-capped per stream (oldest rows trimmed first) so the files
  never grow without bound.
- Thread-safe: the auto-trading loop and paper trades may write concurrently.
- Fail-open on read: a corrupt/missing store returns empty summaries, never an
  exception, so a memory problem can never block a trade decision.
- Hash-chained so the audit trail cannot be silently rewritten.

Stores (each a JSONL file):
- ``decisions.jsonl``  every trading decision (symbol, action, signal, reason)
- ``outcomes.jsonl``   closed-trade results (P&L, win/loss, holding period)
- ``lessons.jsonl``    human/agent extracted lessons replayable into prompts
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import DATA_DIR

MEMORY_DIR = DATA_DIR / "memory"
_DECISIONS = "decisions.jsonl"
_OUTCOMES = "outcomes.jsonl"
_LESSONS = "lessons.jsonl"

_CAPS = {
    _DECISIONS: 2000,
    _OUTCOMES: 1000,
    _LESSONS: 500,
}

_lock = threading.Lock()


# ------------------------------------------------------------------ io


def _path(name: str) -> Path:
    return MEMORY_DIR / name


def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _append(name: str, record: dict[str, Any]) -> None:
    with _lock:
        try:
            f = _path(name)
            f.parent.mkdir(parents=True, exist_ok=True)
            prev = ""
            if f.exists():
                prev = f.read_text(encoding="utf-8").strip().splitlines()[-1]
            row = {
                "ts": _ts(),
                **record,
                "prev_hash": hashlib.sha256(prev.encode("utf-8")).hexdigest(),
            }
            lines = [json.dumps(row, ensure_ascii=False, default=str)]
            if f.exists():
                existing = f.read_text(encoding="utf-8").strip().splitlines()
                existing.append(lines[0])
                lines = existing[-_CAPS.get(name, 2000):]
            f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:  # noqa: BLE001 — memory must never break the caller
            pass


def _rows(name: str) -> list[dict[str, Any]]:
    f = _path(name)
    if not f.exists():
        return []
    try:
        out = []
        with _lock:
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out
    except OSError:
        return []


# ------------------------------------------------------------------ record


def record_decision(symbol: str, action: str, signal: str = "", confidence: float = 0.0,
                    reason: str = "", source: str = "auto") -> None:
    """Persist one trading decision (signal → action)."""
    _append(_DECISIONS, {
        "symbol": symbol.upper(), "action": action, "signal": signal,
        "confidence": round(float(confidence), 1) if confidence else 0.0,
        "reason": (reason or "")[:400], "source": source,
    })


def record_outcome(symbol: str, pnl: float, *, pnl_pct: float = 0.0,
                   holding_hours: float = 0.0, exit_reason: str = "") -> None:
    """Persist a closed-trade outcome. pnl is signed currency P&L."""
    _append(_OUTCOMES, {
        "symbol": symbol.upper(), "pnl": round(float(pnl), 2),
        "pnl_pct": round(float(pnl_pct), 3),
        "holding_hours": round(float(holding_hours), 2),
        "exit_reason": (exit_reason or "")[:200],
        "win": float(pnl) > 0,
    })


def record_lesson(symbol: str, lesson: str, category: str = "general") -> None:
    """Persist a lesson learned so it can be replayed into future reasoning."""
    _append(_LESSONS, {
        "symbol": symbol.upper(), "lesson": (lesson or "").strip()[:500],
        "category": category,
    })


# ------------------------------------------------------------------ analytics


def _win_stats(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    if not outcomes:
        return {"n": 0, "win_rate": 0.0, "expectancy": 0.0, "avg_win": 0.0,
                "avg_loss": 0.0, "total_pnl": 0.0}
    vals = [float(o.get("pnl", 0) or 0) for o in outcomes]
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    return {
        "n": len(outcomes),
        "win_rate": round(len(wins) / len(outcomes), 3),
        "expectancy": round(sum(vals) / len(outcomes), 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "total_pnl": round(sum(vals), 2),
        "best": round(max(vals), 2) if vals else 0.0,
        "worst": round(min(vals), 2) if vals else 0.0,
    }


def _by_symbol(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    per: dict[str, list[float]] = {}
    for o in outcomes:
        per.setdefault(o.get("symbol", "?"), []).append(float(o.get("pnl", 0) or 0))
    ranked = sorted(
        ({"symbol": s, "n": len(vs), "net": round(sum(vs), 2),
          "wins": sum(1 for v in vs if v > 0), "losses": sum(1 for v in vs if v < 0)}
         for s, vs in per.items()),
        key=lambda r: (r["net"], r["wins"]),
        reverse=True,
    )
    return ranked


def memory_stats() -> dict[str, Any]:
    """Full memory snapshot for tools / UI."""
    decs = _rows(_DECISIONS)
    outs = _rows(_OUTCOMES)
    less = _rows(_LESSONS)
    return {
        "status": "ok",
        "counts": {"decisions": len(decs), "outcomes": len(outs), "lessons": len(less)},
        "outcomes": _win_stats(outs),
        "by_symbol": _by_symbol(outs),
        "recent_decisions": list(reversed(decs[-15:])),
        "recent_outcomes": list(reversed(outs[-15:])),
        "recent_lessons": list(reversed(less[-10:])),
        "dir": str(MEMORY_DIR),
    }


def memory_summary(decision_hint: str = "", limit: int = 10) -> dict[str, Any]:
    """Compact streak-summary for injecting into the reasoning context.

    Returns a dict with a ``context`` string the agent can read in one pass:
    recent outcomes with win rate and expectancy, per-symbol net, and the most
    recent replayed lessons. Never raises (fail-open).
    """
    decs = _rows(_DECISIONS)
    outs = _rows(_OUTCOMES)
    less = _rows(_LESSONS)
    recent_out = outs[-limit:]
    stats = _win_stats(outs)
    per = _by_symbol(outs)[:8]

    if not outs and not decs:
        return {
            "status": "empty", "decision_hint": decision_hint,
            "context": "No trading memory yet — no past trades or lessons are "
                       "available to condition on. Proceed on current data alone.",
        }

    lines = [
        "TRADING MEMORY (replay for context):",
        f"Closed trades: {stats['n']} | win rate {stats['win_rate']*100:.0f}% | "
        f"expectancy {stats['expectancy']:+.2f} | total P&L {stats['total_pnl']:+.2f}",
    ]
    if per:
        lines.append("Per-symbol net: " + ", ".join(
            f"{p['symbol']} {p['net']:+.0f} ({p['wins']}W/{p['losses']}L)" for p in per))
    if recent_out:
        lines.append("Recent outcomes:")
        for o in recent_out:
            hs = o.get("holding_hours", 0)
            lines.append(f"- {o.get('symbol')} {o.get('pnl'):+.0f} "
                         f"({'win' if o.get('win') else 'loss'}) "
                         f"~{hs:.0f}h {('· ' + str(o.get('exit_reason',''))) if o.get('exit_reason') else ''}")
    if less:
        lines.append("Lessons to keep in mind:")
        for l in less[-6:]:
            lines.append(f"- {l.get('symbol')}: {l.get('lesson')}")
    if decision_hint:
        lines.append(f"Decision at hand: {decision_hint}")
    lines.append("Memory is context, not instruction — verify with live data before acting.")
    return {"status": "ok", "counts": {"decisions": len(decs), "outcomes": len(outs),
                                       "lessons": len(less)},
            "context": "\n".join(lines)}


# ------------------------------------------------------------------ maintenance


def prune(days: int = 90) -> dict[str, Any]:
    """Trim entries older than ``days`` per stream; returns counts removed."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    removed = {}
    for name in (_DECISIONS, _OUTCOMES, _LESSONS):
        rows = _rows(name)
        kept = [r for r in rows if r.get("ts", "") >= cutoff]
        removed[name] = len(rows) - len(kept)
        try:
            with _lock:
                f = _path(name)
                f.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in kept),
                             encoding="utf-8")
        except OSError:
            removed[name] = -1
    return {"status": "ok", "removed": removed}


__all__ = [
    "record_decision", "record_outcome", "record_lesson",
    "memory_stats", "memory_summary", "prune", "MEMORY_DIR",
]