from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/db.sqlite3"
    upload_dir: str = "./data/uploads"

    # Chunking
    chunk_max_tokens: int = 300
    chunk_overlap_tokens: int = 50
    xlsx_chunk_rows: int = 10
    xlsx_chunk_overlap_rows: int = 2

    # Google Gemini — chat + enrichment LLM
    # Contextual enrichment prepends an LLM-generated 1-2 sentence situational context
    # to each chunk (Anthropic's Contextual Retrieval pattern). Gracefully skipped if API unreachable.
    enable_contextual_enrichment: bool = True
    google_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # Google Gemini — embeddings (text-embedding-004, 768-dim, same as nomic-embed-text)
    gemini_embed_model: str = "models/text-embedding-004"

    # Image description (disabled — VLM not available in cloud deployment)
    enable_image_description: bool = False

    # Semantic search + Doc2Query + Query Rewriting
    enable_embeddings: bool = True
    enable_doc2query: bool = True
    doc2query_questions: int = 3
    enable_query_rewriting: bool = True
    chroma_dir: str = "./data/chroma"

    class Config:
        env_file = ".env"


settings = Settings()
