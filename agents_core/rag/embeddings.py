"""Text normalisation + lexical scoring primitives (TF / BM25).

Pure standard-library math: no external embeddings API or vector DB. These
functions are shared by the retriever; keeping them separate lets the scoring
be unit-tested and swapped (e.g. for an embeddings-based semantic scorer)
without touching the retrieval flow.
"""
from __future__ import annotations

import math
import re
import unicodedata

STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "and", "or", "but", "if", "then", "else", "for", "to", "of", "in", "on",
    "with", "at", "from", "by", "as", "it", "its", "this", "that", "these",
    "those", "not", "no", "do", "does", "did", "will", "would", "can", "could",
    "should", "shall", "may", "might", "must", "have", "has", "had", "into",
    "over", "under", "about", "against", "between", "through", "during",
    "before", "after", "above", "below", "up", "down", "out", "off", "just",
    "than", "too", "very", "also", "how", "what", "when", "where", "which",
    "who", "whom", "why", "all", "any", "both", "each", "few", "more", "most",
    "other", "some", "such", "only", "own", "same", "so", "then", "there",
    "here", "please", "make", "use", "using", "used", "trade", "trading",
}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("₹", " inr ").replace("Rs.", " inr ").replace("Rs ", " inr ")
    return text


def tokenise(text: str) -> list[str]:
    text = normalise(text).lower()
    return [t for t in re.findall(r"[a-z0-9]+", text) if t not in STOPWORDS and len(t) > 1]


def term_frequency(chunk: str) -> dict[str, float]:
    """Normalised term frequency over a chunk: {term: count/len}."""
    tokens = tokenise(chunk)
    if not tokens:
        return {}
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    n = len(tokens)
    return {t: c / n for t, c in counts.items()}


def bm25(chunk: str, query_terms: list[str], df: dict[str, int],
         total_chunks: int, avg_chunk_len: float,
         k1: float = 1.5, b: float = 0.75) -> float:
    """OKAPI BM25 score of a chunk against a query.

    df: document frequency per term across the corpus.
    total_chunks: number of chunks in the corpus.
    avg_chunk_len: mean token count per chunk.
    """
    tf = term_frequency(chunk)
    tokens = tokenise(chunk)
    dl = len(tokens)
    score = 0.0
    for t in query_terms:
        if t not in tf:
            continue
        df_t = df.get(t, 1)
        idf = math.log(1 + (total_chunks - df_t + 0.5) / (df_t + 0.5))
        denom = tf[t] + k1 * (1 - b + b * (dl / avg_chunk_len))
        score += idf * (tf[t] * (k1 + 1)) / (denom if denom else 1e-9)
    return score


# ---------------------------------------------------------------------------
# Local embeddings (dependency-free "semantic" vectors)
#
# Real embeddings APIs (OpenAI, etc.) are optional. To keep the RAG layer
# self-contained and testable offline we embed with feature hashing: each term
# maps (deterministically) to a fixed-dimension signed vector, and a chunk's
# embedding is the length-normalised, IDF-weighted sum of its term vectors.
# This gives cosine similarity a genuinely semantic-ish signal (shared domain
# vocabulary lights up) without any network dependency. Set
# ``EMBEDDING_DIM`` to tune the dimension (default 512).
# ---------------------------------------------------------------------------

import os as _os

EMBED_DIM = int(_os.environ.get("EMBEDDING_DIM", "512") or 512)
_HASH_SALT = 0x9E3779B97F4A7C15


def _h(value: str) -> int:
    h = _HASH_SALT
    for ch in value.encode("utf-8"):
        h ^= ch + 0x9E3779B9 + (h << 6) + (h >> 2)
        h &= 0xFFFFFFFFFFFFFFFF
    return h & 0xFFFFFFFFFFFFFFFF


def term_vector(term: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic signed hashed vector for a single term (sparse projection)."""
    idx = _h(term) % dim
    sign = 1.0 if _h(term + "|s") % 2 == 0 else -1.0
    vec = [0.0] * dim
    vec[idx] = sign
    return vec


def embed(text: str, idf: dict[str, float] | None = None,
          dim: int = EMBED_DIM) -> list[float]:
    """IDF-weighted sum of term vectors, L2-normalised (zero vector for empty)."""
    vec = [0.0] * dim
    for t in tokenise(text):
        idf_t = (idf or {}).get(t, 1.0)
        idx = _h(t) % dim
        sign = 1.0 if _h(t + "|s") % 2 == 0 else -1.0
        vec[idx] += sign * idf_t
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two embeddings (0.0 for empty/zero vectors)."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
