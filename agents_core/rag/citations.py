"""Citation rendering for retrieved knowledge.

Turns raw retrieval results into a prompt-ready context block with numbered
citations, so the agent (and the user) can trace every grounded claim back to
a specific corpus file. Provenance is first-class: a claim must never be
presented without its source.
"""
from __future__ import annotations

from typing import Any


def render_citations(results: list[dict[str, Any]], max_chars_per_chunk: int = 900) -> str:
    """Render retrieved chunks as a numbered, quoted context block.

    Returns something like:

      [KNOWLEDGE CONTEXT]
      [1] source: sebi/sebi-algo-framework.md (score 0.5847)
          > Every algo order must carry a unique algo identifier...
      [2] source: risk/risk-policy.md (score 0.3526)
          > A daily loss circuit breaker stops all trading for the day...

    Cite these as [1], [2], ... in your answer.
    """
    if not results:
        return ""
    lines = ["[KNOWLEDGE CONTEXT]"]
    for i, r in enumerate(results, start=1):
        source = r.get("source", "unknown")
        score = r.get("score", 0.0)
        text = (r.get("text") or "").strip()
        if max_chars_per_chunk and len(text) > max_chars_per_chunk:
            text = text[:max_chars_per_chunk].rstrip() + " ..."
        lines.append(f"[{i}] source: {source} (score {score:g})")
        lines.append(f"    > {text}")
    lines.append("Cite these as [1], [2], ... in your answer.")
    return "\n".join(lines)


def citation_index(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A compact source map: citation number -> {source, path, score}."""
    out = []
    for i, r in enumerate(results, start=1):
        out.append({
            "ref": f"[{i}]",
            "source": r.get("source"),
            "path": r.get("path"),
            "score": r.get("score"),
        })
    return out
