from __future__ import annotations

from functools import lru_cache

from fastapi import Depends

from app.llm import LLMClient, build_llm_client
from app.services import AnalysisService, CopilotService
from app.settings import AppSettings, load_settings
from app.storage import SQLitePersistence


@lru_cache
def get_settings() -> AppSettings:
    return load_settings()


@lru_cache
def get_persistence() -> SQLitePersistence:
    settings = get_settings()
    return SQLitePersistence(settings.sqlite_db_path, repo_root=settings.repo_root)


@lru_cache
def get_llm_client() -> LLMClient:
    return build_llm_client(get_settings())


def get_analysis_service() -> AnalysisService:
    return AnalysisService(settings=get_settings(), persistence=get_persistence())


def get_copilot_service(
    settings: AppSettings = Depends(get_settings),
    persistence: SQLitePersistence = Depends(get_persistence),
) -> CopilotService:
    return CopilotService(
        settings=settings,
        persistence=persistence,
        llm_client_factory=lambda: build_llm_client(settings),
    )
