from __future__ import annotations

import json

import pytest

from fastapi.testclient import TestClient

from app.integrations import build_integration_health_report
from app.main import create_app
from app.settings import load_settings

pytestmark = pytest.mark.live


def test_live_settings_are_loaded_from_actual_local_environment() -> None:
    settings = load_settings()

    assert settings.app_env
    assert settings.data_root.exists()
    assert settings.integrations.ncbi_api_key
    assert settings.integrations.bv_brc_password
    assert settings.integrations.ncbi_datasets_base_url.startswith("https://")
    assert settings.integrations.ncbi_pathogen_detection_base_url.startswith("https://")
    assert settings.integrations.bv_brc_auth_url.startswith("https://")
    assert settings.integrations.bv_brc_api_base_url.startswith("https://")


def test_live_integration_health_report_uses_actual_local_settings() -> None:
    settings = load_settings()
    report = build_integration_health_report(settings)
    serialized = json.dumps(report, sort_keys=True)

    assert report["mode"] == ("fixture" if settings.use_fixtures or settings.demo_mode else "live")
    assert report["status"] in {"ready", "degraded", "fixture"}
    assert report["runtime"]["app_env"] == settings.app_env
    assert report["runtime"]["backend_mode"] == ("demo" if settings.demo_mode else "persisted")
    assert report["runtime"]["job_data_mode"] == ("demo_seeded" if settings.demo_mode else "persisted_jobs")
    assert report["runtime"]["evidence_mode"] == ("fixture" if settings.use_fixtures else "live")
    assert report["runtime"]["llm_mode"] == ("mock" if settings.llm.mock_mode else "live")
    assert report["runtime"]["live_mode_ready"] is (
        not settings.demo_mode and not settings.use_fixtures and not settings.llm.mock_mode
    )
    assert report["settings"]["dataset_root"]
    assert report["external_apis"]["ncbi_datasets"]["status"] == "configured"
    assert report["external_apis"]["ncbi_datasets"]["api_key"] == "configured"
    assert report["external_apis"]["bv_brc"]["status"] == "configured"
    assert report["external_apis"]["bv_brc"]["password"] == "configured"
    assert report["external_apis"]["ncbi_pathogen_detection"]["status"] == "configured"
    assert (
        report["external_apis"]["ncbi_pathogen_detection"]["base_url"]
        == settings.integrations.ncbi_pathogen_detection_base_url
    )
    assert report["external_apis"]["llm"]["status"] == (
        "configured"
        if settings.llm.provider and settings.llm.base_url and settings.llm.api_key and settings.llm.model
        else "missing"
    )
    assert report["external_apis"]["llm"]["provider"] == settings.llm.provider
    assert report["external_apis"]["llm"]["api_key"] == (
        "configured" if settings.llm.api_key else "missing"
    )
    assert report["external_apis"]["llm"]["mock_mode"] is settings.llm.mock_mode
    assert report["external_apis"]["thesys"]["status"] == (
        "configured"
        if settings.thesys.api_key and settings.thesys.base_url and settings.thesys.model
        else "missing"
    )
    assert report["external_apis"]["thesys"]["base_url"] == settings.thesys.base_url
    assert report["external_apis"]["thesys"]["api_key"] == (
        "configured" if settings.thesys.api_key else "missing"
    )
    assert report["secrets"] == {"redacted": True, "values_exposed": False}
    assert settings.integrations.ncbi_api_key not in serialized
    assert settings.integrations.bv_brc_password not in serialized
    if settings.llm.api_key:
        assert settings.llm.api_key not in serialized
    if settings.thesys.api_key:
        assert settings.thesys.api_key not in serialized


def test_live_integration_health_endpoint_uses_actual_backend_app() -> None:
    settings = load_settings()
    client = TestClient(create_app())
    try:
        response = client.get("/health/integrations")
    finally:
        client.close()

    body = response.json()
    serialized = json.dumps(body, sort_keys=True)
    assert response.status_code == 200
    assert body["mode"] == ("fixture" if settings.use_fixtures or settings.demo_mode else "live")
    assert body["status"] in {"ready", "degraded", "fixture"}
    assert body["runtime"]["app_env"] == settings.app_env
    assert body["runtime"]["backend_mode"] == ("demo" if settings.demo_mode else "persisted")
    assert body["runtime"]["job_data_mode"] == ("demo_seeded" if settings.demo_mode else "persisted_jobs")
    assert body["runtime"]["evidence_mode"] == ("fixture" if settings.use_fixtures else "live")
    assert body["runtime"]["llm_mode"] == ("mock" if settings.llm.mock_mode else "live")
    assert body["external_apis"]["ncbi_datasets"]["api_key"] == "configured"
    assert body["external_apis"]["bv_brc"]["password"] == "configured"
    assert (
        body["external_apis"]["ncbi_pathogen_detection"]["base_url"]
        == settings.integrations.ncbi_pathogen_detection_base_url
    )
    assert body["external_apis"]["llm"]["status"] == (
        "configured"
        if settings.llm.provider and settings.llm.base_url and settings.llm.api_key and settings.llm.model
        else "missing"
    )
    assert body["external_apis"]["llm"]["api_key"] == (
        "configured" if settings.llm.api_key else "missing"
    )
    assert body["external_apis"]["thesys"]["status"] == (
        "configured"
        if settings.thesys.api_key and settings.thesys.base_url and settings.thesys.model
        else "missing"
    )
    assert body["external_apis"]["thesys"]["api_key"] == (
        "configured" if settings.thesys.api_key else "missing"
    )
    assert body["secrets"]["redacted"] is True
    assert settings.integrations.ncbi_api_key not in serialized
    assert settings.integrations.bv_brc_password not in serialized
    if settings.llm.api_key:
        assert settings.llm.api_key not in serialized
    if settings.thesys.api_key:
        assert settings.thesys.api_key not in serialized
