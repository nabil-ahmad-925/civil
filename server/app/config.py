from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

 
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    repository: Literal["supabase", "memory"] = "supabase"
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    storage_bucket: str = "manuals"

    cors_origins: str = "http://localhost:3000"
    max_upload_mb: int = 300

    # How headings are found. "openai" asks a model page by page; "regex" is free and offline.
    chunk_planner: Literal["regex", "openai"] = "regex"
    chunk_model: str = "gpt-4.1-mini"
    # Lines of the previous and next page shown with each page, so a heading cut by a page break reads whole.
    chunk_context_lines: int = 6

    # Finds "see Section X" mentions in each node. Always needs OPENAI_API_KEY.
    ref_extractor_model: str = "gpt-4.1-mini"
    ref_extractor_workers: int = 8

    # Resolution of the PNG saved for each figure.
    figure_dpi: int = 150

    # Chat: answers a question from the retrieved sections (infra/lambdas/chat/chat_service.py).
    chat_model: str = "gpt-4.1-mini"
    chat_top_k: int = 5
    # A question about WHERE something appears ("which article mentions ...") needs every place, not the best few.
    chat_locate_top_k: int = 10
    # Below this cosine similarity the best match is not about the question: say "not covered" without asking the model.
    chat_min_score: float = 0.2
    chat_source_chars: int = 3000  # text kept per source in the prompt
    chat_context_chars: int = 24000  # text kept over all sources in the prompt
    chat_history_messages: int = 6
    figure_url_seconds: int = 3600

    embedding_provider: Literal["none", "openai"] = "none"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    openai_api_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


def check_config(s: Settings) -> None:
    """Fail at startup, with a plain message, instead of with a 500 on every request."""
    if s.repository == "supabase" and not (s.supabase_url and s.supabase_service_role_key):
        raise RuntimeError(
            "Supabase is not configured. Create server/.env (copy .env.example) and set SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY, or set REPOSITORY=memory to run without a database."
        )
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is needed in server/.env (cross-references are found with OpenAI).")
