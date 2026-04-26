from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.dependencies import get_settings
from app.integrations import build_api_health_report, build_integration_health_report
from app.settings import AppSettings


router = APIRouter(tags=["health"])


@router.get("/health")
def health_check(
    settings: AppSettings = Depends(get_settings),
) -> dict[str, Any]:
    return build_api_health_report(settings)


@router.get("/health/integrations")
def integration_health_check(
    settings: AppSettings = Depends(get_settings),
) -> dict[str, Any]:
    return build_integration_health_report(settings)
