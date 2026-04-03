"""
Query Transformation — LangChain LCEL chain.

rewrite_query: rewrites a natural-language question into a clean retrieval query
via a prompt | ChatGroq | StrOutputParser chain.
Returns the original query unchanged if the Groq API is unavailable or rewriting is disabled.
"""
from __future__ import annotations

import logging

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_groq import ChatGroq

from config import settings

_log = logging.getLogger(__name__)

_REWRITE_PROMPT = PromptTemplate.from_template(
    "Rewrite the following user question into a clean, specific search query. "
    "Remove conversational filler, fix typos, expand abbreviations. "
    "Output ONLY the rewritten query — no explanation, no punctuation changes beyond what's needed.\n\n"
    "Original: {question}\n"
    "Rewritten:"
)


def rewrite_query(q: str, groq_api_key: str = "") -> str:
    """
    Rewrite a natural-language question into a clean retrieval query.
    Falls back to the original on any failure.
    groq_api_key: user-provided key from request header (overrides server key if set).
    """
    if not settings.enable_query_rewriting:
        _log.info("[query_rewriter] disabled — using original: %r", q)
        return q
    try:
        resolved_key = groq_api_key or settings.groq_api_key
        chain = _REWRITE_PROMPT | ChatGroq(
            model=settings.groq_model,
            groq_api_key=resolved_key,
        ) | StrOutputParser()
        rewritten = chain.invoke({"question": q}).strip()
        if rewritten:
            _log.info("[query_rewriter] %r → %r", q, rewritten)
            return rewritten
        return q
    except Exception as exc:
        _log.warning("[query_rewriter] failed (%s) — using original", exc)
        return q
