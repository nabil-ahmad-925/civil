from functools import lru_cache

from .config import get_settings
from .ingestion.chunker import RegexPlanner
from .ingestion.crossrefs import OpenAIRefExtractor, RefExtractor
from .ingestion.embeddings import Embedder, build_embedder
from .ingestion.planner import NodePlanner, OpenAIPlanner
from .repository.base import Repository
from .repository.memory import MemoryRepository
from .repository.supabase_repo import SupabaseRepository


@lru_cache
def get_repo() -> Repository:
    s = get_settings()
    if s.repository == "memory":
        return MemoryRepository(s.keyword_backend)
    return SupabaseRepository(s.supabase_url, s.supabase_service_role_key, s.storage_bucket, s.keyword_backend, s.paradedb_url)


@lru_cache
def get_embedder() -> Embedder | None:
    return build_embedder(get_settings())


@lru_cache
def get_planner() -> NodePlanner:
    s = get_settings()
    if s.chunk_planner == "openai":
        return OpenAIPlanner(s.openai_api_key, s.chunk_model)
    return RegexPlanner()


@lru_cache
def get_ref_extractor() -> RefExtractor:
    s = get_settings()
    return OpenAIRefExtractor(s.openai_api_key, s.ref_extractor_model)
