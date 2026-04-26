from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_persistence, get_settings
from app.demo_data import demo_queue_items
from app.contracts import JobState, QueueSummaryResponse, TriageOutcome
from app.settings import AppSettings
from app.storage import SQLitePersistence


router = APIRouter(prefix="/queue", tags=["queue"])


@router.get("", response_model=QueueSummaryResponse)
def get_queue_summary(
    triage: TriageOutcome | None = None,
    status: JobState | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    persistence: SQLitePersistence = Depends(get_persistence),
    settings: AppSettings = Depends(get_settings),
) -> QueueSummaryResponse:
    persisted_items = persistence.list_queue_items(
        triage=None if triage is None else triage.value,
        status=None if status is None else status.value,
        limit=limit,
    )
    if persisted_items or not settings.demo_mode:
        return QueueSummaryResponse(items=persisted_items)

    items = demo_queue_items()
    if triage is not None:
        items = [item for item in items if item.triage == triage]
    if status is not None:
        items = [item for item in items if item.status == status]
    return QueueSummaryResponse(items=items[:limit])
