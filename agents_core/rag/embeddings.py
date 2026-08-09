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
