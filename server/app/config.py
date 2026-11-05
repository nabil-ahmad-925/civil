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
    # Resolution of the PNG saved for each table.
    table_dpi: int = 150

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
    # Optional. When set, POST /api/chat needs it in the x-api-key header (the Next.js server sends CHAT_API_KEY from
    # client/.env.local). Empty means open, which is only safe while the server listens on your own machine.
    chat_api_key: str = ""

    # Who answers the keyword search: "fts" is the full-text function in Supabase (sql/004_hybrid_search.sql); "paradedb" is
    # BM25 on a separate ParadeDB database (sql/paradedb/, needs PARADEDB_URL), because Supabase has no pg_search.
    keyword_backend: Literal["fts", "paradedb"] = "fts"
    # Postgres connection string of that database, e.g. postgresql://user:password@host:5432/nodes_search?sslmode=require
    paradedb_url: str = ""
    # The smallest keyword score that counts as evidence a section holds the question's words. "fts" scores run from 0 to 2;
    # BM25 scores (averaged over the question's words) have no upper bound, so they have their own value. 4.0 keeps the best
    # hit of every real question tried on the HDM and PPM (5 to 16) and drops most off-topic ones (under 4). It was measured
    # with the in-memory BM25; ParadeDB's own scores for the same questions were within a few percent of it.
    keyword_min_score_fts: float = 0.3
    keyword_min_score_bm25: float = 4.0

    # Drawings: a vision model reads each sheet (app/drawings/). Needs OPENAI_API_KEY.
    drawing_vision_model: str = "gpt-6-astra"
    # Empty leaves the model's default. Otherwise low, medium, high...
    drawing_reasoning_effort: str = ""
    # A picture over this many patches of 32x32 px is shrunk first. OpenAI's own limit is 30,000 (it rejects more, it does not
    # shrink), but measured on an 8000 x 6000 sheet with gpt-6-astra, positions were within 18 px of 8000 up to 25,000
    # patches and off by up to 400 px at 30,000, which cuts crops in the wrong place. Measure again before raising it.
    drawing_max_patches: int = 20000
    # A feature and its notes are read from one crop unless that crop's long side would be over this.
    drawing_max_crop_px: int = 2400
    # A sheet whose long side is under this gets a warning: small text will not be readable.
    drawing_min_long_side_px: int = 2000
    drawing_workers: int = 4
    # Off: only the whole-sheet pass finds things. On: overlapping tiles also look for what it missed, at one more call
    # per tile. Turn it on only if scoring on real drawings shows the whole-sheet pass misses too much.
    drawing_tile_sweep: bool = False
    drawing_tile_px: int = 2000
    drawing_tile_overlap: float = 0.15
    # For the cost estimate only. Check these against OpenAI's price list.
    drawing_input_usd_per_m: float = 10.0
    drawing_output_usd_per_m: float = 50.0

    embedding_provider: Literal["none", "openai"] = "none"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    openai_api_key: str = ""

    @property
    def keyword_min_score(self) -> float:
        return self.keyword_min_score_bm25 if self.keyword_backend == "paradedb" else self.keyword_min_score_fts

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
