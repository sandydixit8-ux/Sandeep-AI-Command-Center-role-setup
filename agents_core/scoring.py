"""Honest skill-match scoring between a resume and a job description.

Extracts known skill keywords from both texts, reports the overlap as a percentage,
and lists the real gaps. Never fabricates skills — it only matches what is literally
present in the provided text.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

SKILL_KEYWORDS: tuple[str, ...] = (
    # AI / ML / data
    "machine learning", "deep learning", "neural network", "nlp", "llm", "llms",
    "prompt engineering", "langchain", "llamaindex", "openai", "anthropic", "claude",
    "rag", "embeddings", "vector database", "fine-tuning", "computer vision",
    "tensorflow", "pytorch", "pandas", "numpy", "scikit-learn", "data analysis",
    "data engineering", "etl", "sql", "bigquery", "spark", "statistics", "a/b testing",
    # software engineering
    "python", "javascript", "typescript", "node.js", "react", "next.js", "fastapi",
    "flask", "django", "java", "c#", "go", "rust", "ruby", "php", "html", "css",
    "api", "rest api", "graphql", "grpc", "docker", "kubernetes", "k8s", "aws", "azure",
    "gcp", "serverless", "ci/cd", "git", "github", "github actions", "jenkins",
    "terraform", "microservices", "websockets", "redis", "postgres", "postgresql",
    "mysql", "mongodb", "sqlite", "testing", "unit testing", "pytest", "selenium",
    "oop", "data structures", "algorithms", "system design", "linux", "bash",
    # automation / low-code
    "automation", "rpa", "zapier", "make.com", "n8n", "power automate", "workflow",
    "scripting", "vba", "excel macros", "document automation", "docx", "pdf automation",
    # project / PMO
    "project management", "pmo", "pmp", "prince2", "agile", "scrum", "kanban",
    "waterfall", "risk management", "stakeholder management", "resource planning",
    "critical path", "wbs", "ms project", "jira", "confluence", "sdlc",
    # business development / sales / proposals
    "proposal writing", "rfp", "eoi", "tender", "business development", "lead generation",
    "crm", "salesforce", "hubspot", "pipeline management", "negotiation", "capability statement",
    "win strategy", "bid management",
    # marketing
    "seo", "sem", "ppc", "google ads", "google analytics", "meta ads", "facebook ads",
    "content marketing", "email marketing", "copywriting", "crm marketing", "funnel",
    "conversion rate optimization", "cro", "landing page", "google tag manager",
    "keyword research", "backlinks", "social media marketing",
    # finance
    "financial modeling", "budgeting", "forecasting", "cash flow", "invoicing",
    "xero", "quickbooks", "excel", "power bi", "tableau", "kpis", "p&l",
    # executive / admin
    "executive support", "calendar management", "meeting management", "minute taking",
    "travel arrangements", "email management", "documentation", "presentation design",
    "powerpoint", "pitch deck", "report writing", "dashboarding",
    # soft skills commonly listed
    "communication", "leadership", "team management", "problem solving", "time management",
    "attention to detail", "adaptability", "collaboration", "mentoring",
)

_TERMS_CACHE: dict[str, tuple[str, ...]] = {}


def _term_variants(term: str) -> list[str]:
    """Lowercase variant(s) of a keyword, e.g. 'python' -> {'python'}."""
    return [term.lower()]


def extract_skills(text: str) -> set[str]:
    """Return the known-skill keywords found in the text (lowercased)."""
    t = text.lower()
    found: set[str] = set()
    for term in SKILL_KEYWORDS:
        for variant in _term_variants(term):
            if variant in t:
                found.add(term)
                break
    return found


def score_resume(resume_text: str, jd_text: str) -> dict[str, object]:
    """Score a resume against a JD.

    Returns score (0-100), matched skills, gaps, and raw coverage.
    The score is a strict text-overlap measure; treat it as a screening estimate,
    not a guarantee of fit.
    """
    jd_skills = extract_skills(jd_text)
    resume_skills = extract_skills(resume_text)
    matched = jd_skills & resume_skills
    gaps = jd_skills - resume_skills
    total = len(jd_skills)
    score = round(100 * len(matched) / total) if total else 0
    return {
        "score": score,
        "coverage": f"{len(matched)}/{total}",
        "matched": sorted(matched),
        "gaps": sorted(gaps),
        "jd_skills_known": total,
        "note": "Strict text-overlap estimate. Includes only keywords found verbatim; "
        "verify scoring with the real JD before applying.",
    }


def score_resume_files(resume_path: str | Path, jd_path: str | Path) -> dict[str, object]:
    """Convenience wrapper: read both files and score them."""
    resume = Path(resume_path).read_text(encoding="utf-8", errors="replace")
    jd = Path(jd_path).read_text(encoding="utf-8", errors="replace")
    return score_resume(resume, jd)


def format_score(result: dict[str, object]) -> str:
    """Human-readable rendering of a score result."""
    lines = [
        f"skill match: {result['score']}%  (coverage {result['coverage']} of known JD keywords)",
        "",
        f"matched ({len(result['matched'])}):",
    ]
    lines.append("  " + (", ".join(map(str, result["matched"])) if result["matched"] else "(none)"))
    lines.append("")
    lines.append(f"gaps ({len(result['gaps'])}) — prepare for these in interview or add real evidence:")
    lines.append("  " + (", ".join(map(str, result["gaps"])) if result["gaps"] else "(none)"))
    lines.append("")
    lines.append(str(result["note"]))
    return "\n".join(lines)


def skills_summary(text: str) -> str:
    """Return a list of known skills detected in arbitrary text (for capability statements)."""
    skills = extract_skills(text)
    if not skills:
        return "(no known skill keywords detected)"
    return ", ".join(sorted(skills))


def _strip_markup(text: str) -> str:
    """Strip PDF-ish/HTML noise so scoring still works on raw dumps."""
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text
