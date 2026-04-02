"""
Query Transformation — LangChain LCEL chain.

rewrite_query: rewrites a natural-language question into a clean retrieval query
via a prompt | ChatGoogleGenerativeAI | StrOutputParser chain.
Returns the original query unchanged if the Gemini API is unavailable or rewriting is disabled.
"""
from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from config import settings

_REWRITE_PROMPT = PromptTemplate.from_template(
    "Rewrite the following user question into a clean, specific search query. "
    "Remove conversational filler, fix typos, expand abbreviations. "
    "Output ONLY the rewritten query — no explanation, no punctuation changes beyond what's needed.\n\n"
    "Original: {question}\n"
    "Rewritten:"
)


def rewrite_query(q: str) -> str:
    """
    Rewrite a natural-language question into a clean retrieval query.
    Falls back to the original on any failure.
    """
    if not settings.enable_query_rewriting:
        return q
    try:
        chain = _REWRITE_PROMPT | ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.google_api_key,
            timeout=15,
        ) | StrOutputParser()
        rewritten = chain.invoke({"question": q}).strip()
        return rewritten if rewritten else q
    except Exception:
        return q
