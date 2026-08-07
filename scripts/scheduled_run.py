"""Scheduled runs for the AI Command Center — designed for Windows Task Scheduler.

Example weekly finance summary + inbox triage. Each run is standalone (fresh agent).

Schedule with Windows Task Scheduler (runs in the foreground once):
    python scripts/scheduled_run.py --finance
    python scripts/scheduled_run.py --inbox --limit 5

Or add a daily/weekly trigger to Task Scheduler:
    Program:  C:\\Users\\Ats\\AppData\\Local\\Python\\bin\\python.exe
    Arguments: "C:\\Users\\Ats\\OneDrive\\Documents\\Default Project\\Agents\\scripts\\scheduled_run.py" --finance
    Start in: C:\\Users\\Ats\\OneDrive\\Documents\\Default Project\\Agents
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents_core.registry import get_agent


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    log_dir = Path(__file__).resolve().parent.parent / "data" / "scheduled"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "scheduled.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run_finance() -> None:
    agent = get_agent("finance")
    try:
        report = agent.run(
            "Produce this month's finance summary: use ledger_summary for this month and "
            "all-time totals, flag any cash-flow risks, and write the summary to outputs/finance/."
        )
        log(f"finance summary complete:\n{report}")
    finally:
        agent.close()


def run_inbox(limit: int) -> None:
    agent = get_agent("exec")
    try:
        task = (
            f"Triage my inbox: use gmail_inbox (UNSEEN, limit {limit}), then for each item "
            "say whether it needs action, who owns it, and draft replies for anything that "
            "can be answered. Save the triage to outputs/gmail/triage.md. Do NOT send anything."
        )
        report = agent.run(task)
        log(f"inbox triage complete:\n{report}")
    finally:
        agent.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Scheduled agent runs")
    parser.add_argument("--finance", action="store_true", help="weekly finance summary")
    parser.add_argument("--inbox", action="store_true", help="inbox triage")
    parser.add_argument("--limit", type=int, default=10, help="max emails for inbox triage")
    args = parser.parse_args()

    if args.finance:
        run_finance()
    if args.inbox:
        run_inbox(args.limit)
    if not args.finance and not args.inbox:
        parser.error("use --finance and/or --inbox")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
