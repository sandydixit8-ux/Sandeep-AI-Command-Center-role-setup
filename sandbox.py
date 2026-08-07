#!/usr/bin/env python
"""Sandeep AI Command Center — CLI.

One-liner:
    python sandbox.py --agent finance "Record a 50 GBP software subscription expense"

Interactive chat:
    python sandbox.py --agent bd --chat

List agents / offline test:
    python sandbox.py --list
    python sandbox.py --agent docs "Write a DPR" --mock
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents_core.config import get_settings
from agents_core.registry import get_agent, list_agents


def main() -> int:
    parser = argparse.ArgumentParser(description="Sandeep AI Command Center — agents suite")
    parser.add_argument("--agent", "-a", default="commander", help="agent key (commander, exec, finance, bd, jobsearch, marketing, docs, coach)")
    parser.add_argument("--chat", action="store_true", help="interactive REPL")
    parser.add_argument("--mock", action="store_true", help="use the offline mock LLM (no API key needed)")
    parser.add_argument("--list", action="store_true", help="list available agents")
    parser.add_argument("task", nargs="*", help="task text (one-shot mode)")
    args = parser.parse_args()

    if args.list:
        print("Available agents:")
        print(list_agents())
        return 0

    if args.mock:
        os.environ["AGENT_LLM_PROVIDER"] = "mock"

    try:
        settings = get_settings()
    except ValueError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    if settings.provider != "mock" and not settings.has_provider_key():
        print(
            f"no API key configured for provider '{settings.provider}'.\n"
            "Copy Agents/.env.example to Agents/.env and set the key, or use --mock.",
            file=sys.stderr,
        )
        return 1

    if not args.agent:
        args.agent = "commander"

    try:
        agent = get_agent(args.agent)
    except KeyError as exc:
        print(f"{exc}. Available agents:\n{list_agents()}", file=sys.stderr)
        return 1

    label = getattr(agent, "label", args.agent)
    print(f"[{label}] provider={settings.provider} model={settings.anthropic_model if settings.provider == 'anthropic' else settings.openai_model if settings.provider == 'openai' else 'mock'}")

    try:
        if args.chat:
            return _repl(agent)
        task = " ".join(args.task)
        if not task:
            parser.error("provide a task, e.g. python sandbox.py --agent finance \"Add a 50 GBP expense for software\"")
        print(agent.run(task))
        return 0
    finally:
        agent.close()


def _repl(agent) -> int:
    print("Type 'exit' or Ctrl+C to quit.")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line.lower() in ("exit", "quit"):
            return 0
        print(f"{agent.name}> {agent.chat(line)}")


if __name__ == "__main__":
    raise SystemExit(main())
