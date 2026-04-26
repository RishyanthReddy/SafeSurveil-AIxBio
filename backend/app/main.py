from __future__ import annotations

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.queue import router as queue_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="SafeSurveil-AIxBio Backend",
        version="0.1.0",
        description="Local-first backend orchestration API for the SafeSurveil-AIxBio MVP.",
    )
    app.include_router(health_router)
    app.include_router(jobs_router)
    app.include_router(queue_router)
    return app


app = create_app()
