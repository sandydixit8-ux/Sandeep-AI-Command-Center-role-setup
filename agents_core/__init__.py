"""Sandeep AI Command Center — agents suite.

Usage: python sandbox.py --agent <key> "task"
       python sandbox.py --agent finance --chat     (interactive REPL)
"""
from . import agent, config, llm, prompts, registry, tools

__all__ = ["agent", "config", "llm", "prompts", "registry", "tools"]
__version__ = "0.1.0"
