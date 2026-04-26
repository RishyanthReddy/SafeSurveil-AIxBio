from __future__ import annotations

from pathlib import Path
from shutil import which
from typing import Any

from app.evidence import inspect_amrfinderplus_runtime, inspect_mash_runtime
from app.settings import AppSettings


def _configured(value: object | None) -> str:
    return "configured" if value else "missing"


def _required_configured(*values: object | None) -> str:
    return "configured" if all(values) else "missing"


def _safe_path_label(path: Path, *, repo_root: Path) -> str:
    expanded = path.expanduser()
    try:
        return expanded.relative_to(repo_root).as_posix()
    except ValueError:
        return expanded.name


def _executable_status(command_name: str, override: str | None) -> dict[str, str]:
    if override:
        override_path = Path(override).expanduser()
        if override_path.exists():
            return {
                "status": "available",
                "source": "override_path",
                "configured_value": "configured",
            }
        if which(override):
            return {
                "status": "available",
                "source": "override_command",
                "configured_value": "configured",
            }
        return {
            "status": "missing",
            "source": "override",
            "configured_value": "configured",
        }

    return {
        "status": "available" if which(command_name) else "missing",
        "source": "PATH",
        "configured_value": "missing",
    }


def build_runtime_mode_report(settings: AppSettings) -> dict[str, Any]:
    live_mode_blockers: list[str] = []
    if settings.demo_mode:
        live_mode_blockers.append("demo_mode_enabled")
    if settings.use_fixtures:
        live_mode_blockers.append("fixture_mode_enabled")
    if settings.llm.mock_mode:
        live_mode_blockers.append("llm_mock_mode_enabled")

    return {
        "app_env": settings.app_env,
        "backend_mode": "demo" if settings.demo_mode else "persisted",
        "job_data_mode": "demo_seeded" if settings.demo_mode else "persisted_jobs",
        "evidence_mode": "fixture" if settings.use_fixtures else "live",
        "llm_mode": "mock" if settings.llm.mock_mode else "live",
        "acceptance_mode": "live_candidate" if not live_mode_blockers else "mixed_non_live",
        "live_mode_ready": not live_mode_blockers,
        "live_mode_blockers": live_mode_blockers,
    }


def build_api_health_report(settings: AppSettings) -> dict[str, Any]:
    return {
        "status": "ok",
        "runtime": build_runtime_mode_report(settings),
    }


def _overall_status(report: dict[str, Any]) -> str:
    if report["mode"] == "fixture":
        return "fixture"
    if not report["runtime"]["live_mode_ready"]:
        return "degraded"

    api_values = report["external_apis"].values()
    tool_values = report["tools"].values()
    has_missing_required_api = any(item["status"] == "missing" for item in api_values)
    has_missing_tool = any(item["status"] == "missing" for item in tool_values)
    if has_missing_required_api or has_missing_tool:
        return "degraded"
    return "ready"


def build_integration_health_report(settings: AppSettings) -> dict[str, Any]:
    integrations = settings.integrations
    token_exists = integrations.bv_brc_token_path.exists()
    has_bv_brc_credentials = bool(
        integrations.bv_brc_password
        and (integrations.bv_brc_username or integrations.bv_brc_username_alt)
    )
    bv_brc_status = "configured" if token_exists or has_bv_brc_credentials else "missing"
    amrfinder_runtime = inspect_amrfinderplus_runtime(
        executable_override=integrations.amrfinderplus_bin,
        database_dir=integrations.amrfinderplus_db,
    )
    mash_runtime = inspect_mash_runtime(executable_override=integrations.mash_bin)

    report: dict[str, Any] = {
        "status": "pending",
        "mode": "fixture" if settings.use_fixtures or settings.demo_mode else "live",
        "runtime": build_runtime_mode_report(settings),
        "settings": {
            "dataset_root": _safe_path_label(integrations.dataset_root, repo_root=settings.repo_root),
            "log_level": integrations.log_level,
            "http_timeout_seconds": integrations.live_http_timeout_seconds,
            "http_retry_count": integrations.live_http_retry_count,
        },
        "external_apis": {
            "ncbi_datasets": {
                "status": "configured" if integrations.ncbi_datasets_base_url else "missing",
                "base_url": integrations.ncbi_datasets_base_url,
                "api_key": _configured(integrations.ncbi_api_key),
            },
            "bv_brc": {
                "status": bv_brc_status,
                "auth_url": integrations.bv_brc_auth_url,
                "api_base_url": integrations.bv_brc_api_base_url,
                "token_file": "present" if token_exists else "missing",
                "username": _configured(
                    integrations.bv_brc_username or integrations.bv_brc_username_alt
                ),
                "password": _configured(integrations.bv_brc_password),
            },
            "ncbi_pathogen_detection": {
                "status": (
                    "configured"
                    if integrations.ncbi_pathogen_detection_base_url
                    else "missing"
                ),
                "base_url": integrations.ncbi_pathogen_detection_base_url,
            },
            "llm": {
                "status": _required_configured(
                    settings.llm.provider,
                    settings.llm.base_url,
                    settings.llm.api_key,
                    settings.llm.model,
                ),
                "provider": settings.llm.provider,
                "base_url": settings.llm.base_url,
                "model": settings.llm.model,
                "fallback_model": settings.llm.fallback_model,
                "api_key": _configured(settings.llm.api_key),
                "mock_mode": settings.llm.mock_mode,
            },
            "thesys": {
                "status": _required_configured(
                    settings.thesys.api_key,
                    settings.thesys.base_url,
                    settings.thesys.model,
                ),
                "base_url": settings.thesys.base_url,
                "model": settings.thesys.model,
                "api_key": _configured(settings.thesys.api_key),
            },
        },
        "tools": {
            "amrfinderplus": {
                "status": "available" if amrfinder_runtime.status == "ready" else "missing",
                "runtime_status": amrfinder_runtime.status,
                "source": amrfinder_runtime.executable_source,
                "configured_value": _configured(integrations.amrfinderplus_bin),
                "executable_path": (
                    _safe_path_label(amrfinder_runtime.executable_path, repo_root=settings.repo_root)
                    if amrfinder_runtime.executable_path is not None
                    else None
                ),
                "version": amrfinder_runtime.version,
                "database": amrfinder_runtime.database_status,
                "database_path": (
                    _safe_path_label(amrfinder_runtime.database_path, repo_root=settings.repo_root)
                    if amrfinder_runtime.database_path is not None
                    else None
                ),
                "database_version": amrfinder_runtime.database_version,
                "notes": list(amrfinder_runtime.notes),
            },
            "mash": {
                "status": "available" if mash_runtime.status == "ready" else "missing",
                "runtime_status": mash_runtime.status,
                "source": mash_runtime.executable_source,
                "configured_value": _configured(integrations.mash_bin),
                "executable_path": (
                    _safe_path_label(mash_runtime.executable_path, repo_root=settings.repo_root)
                    if mash_runtime.executable_path is not None
                    else None
                ),
                "version": mash_runtime.version,
                "notes": list(mash_runtime.notes),
            },
        },
        "secrets": {
            "redacted": True,
            "values_exposed": False,
        },
    }
    report["status"] = _overall_status(report)
    return report
