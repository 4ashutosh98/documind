from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/db.sqlite3"
    upload_dir: str = "./data/uploads"

    # Chunking
    chunk_max_tokens: int = 300
    chunk_overlap_tokens: int = 50
    xlsx_chunk_rows: int = 10
    xlsx_chunk_overlap_rows: int = 2

    # Groq — LLM for chat, enrichment, doc2query, query rewriting (14,400 req/day free tier)
    # Contextual enrichment prepends an LLM-generated 1-2 sentence situational context
    # to each chunk (Anthropic's Contextual Retrieval pattern). Gracefully skipped if API unreachable.
    enable_contextual_enrichment: bool = True
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-8b-instant"

    # Google Gemini — embeddings only (text-embedding-004, 768-dim)
    # Groq has no embeddings API; Gemini embeddings quota is separate from generate_content.
    google_api_key: str = ""
    gemini_embed_model: str = "gemini-embedding-001"

    # Image description (disabled — VLM not available in cloud deployment)
    enable_image_description: bool = False

    # Inactivity cleanup — wipe all data after N minutes of no user activity.
    # Set to 0 to disable. Configurable via INACTIVITY_TIMEOUT_MINUTES env var.
    inactivity_timeout_minutes: int = 30

    # Semantic search + Doc2Query + Query Rewriting
    enable_embeddings: bool = True
    enable_doc2query: bool = True
    doc2query_questions: int = 3
    enable_query_rewriting: bool = True
    chroma_dir: str = "./data/chroma"

    # Verbose pipeline logging — set VERBOSE=false to silence detailed logs
    verbose: bool = True

    class Config:
        env_file = ".env"
        extra = "ignore"  # silently ignore unknown vars in .env (e.g. HF_TOKEN)


settings = Settings()
