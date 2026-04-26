from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from app.settings import load_settings

REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_PHASE6B_ENV_VARS = {
    "USE_FIXTURES",
    "DATASET_ROOT",
    "LOG_LEVEL",
    "NCBI_API_KEY",
    "NCBI_DATASETS_BASE_URL",
    "NCBI_PATHOGEN_DETECTION_BASE_URL",
    "LIVE_HTTP_TIMEOUT_SECONDS",
    "LIVE_HTTP_RETRY_COUNT",
    "BV_BRC_AUTH_URL",
    "BV_BRC_API_BASE_URL",
    "BV_BRC_TOKEN_PATH",
    "BV_BRC_USERNAME",
    "BV_BRC_USERNAME_ALT",
    "BV_BRC_PASSWORD",
    "AMRFINDERPLUS_BIN",
    "AMRFINDERPLUS_DB",
    "MASH_BIN",
}

TRACKED_ENV_AND_DOC_FILES = [
    ".env.example",
]

SECRET_NAME_MARKERS = ("API_KEY", "PASSWORD", "SECRET")
ENV_ASSIGNMENT_PATTERN = re.compile(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)$")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _env_template_variables() -> dict[str, str]:
    variables: dict[str, str] = {}
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        match = ENV_ASSIGNMENT_PATTERN.match(line)
        if match:
            variables[match.group(1)] = match.group(2).strip()
    return variables


def test_env_template_declares_phase6b_live_contract() -> None:
    variables = _env_template_variables()

    assert REQUIRED_PHASE6B_ENV_VARS <= set(variables)
    assert variables["USE_FIXTURES"] == "false"
    assert variables["NCBI_DATASETS_BASE_URL"].startswith("https://")
    assert variables["BV_BRC_AUTH_URL"].startswith("https://")
    assert variables["BV_BRC_API_BASE_URL"].startswith("https://")
    assert variables["BV_BRC_TOKEN_PATH"] == "~/.patric_token"
    assert variables["NCBI_API_KEY"] == ""
    assert variables["BV_BRC_PASSWORD"] == ""


def test_secret_bearing_local_files_are_ignored_and_untracked() -> None:
    tracked_result = _git(
        "ls-files",
        ".env",
        ".env.example",
        "BVBRC_Scraping_Guide.md",
        "local.patric_token",
    )
    assert tracked_result.returncode == 0
    tracked = set(tracked_result.stdout.splitlines())

    assert ".env.example" in tracked
    assert ".env" not in tracked
    assert "BVBRC_Scraping_Guide.md" not in tracked
    assert "local.patric_token" not in tracked

    ignored_result = _git(
        "check-ignore",
        ".env",
        "BVBRC_Scraping_Guide.md",
        "local.patric_token",
    )
    assert ignored_result.returncode == 0
    assert set(ignored_result.stdout.splitlines()) == {
        ".env",
        "BVBRC_Scraping_Guide.md",
        "local.patric_token",
    }


@pytest.mark.live
def test_local_dotenv_values_are_loaded_without_tracking_the_file() -> None:
    settings = load_settings()

    assert settings.integrations.ncbi_api_key
    assert settings.integrations.bv_brc_password


def test_tracked_phase6b_files_do_not_assign_secret_values() -> None:
    leaked_assignments: list[str] = []
    for relative_path in TRACKED_ENV_AND_DOC_FILES:
        for line_number, line in enumerate(
            (REPO_ROOT / relative_path).read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            match = ENV_ASSIGNMENT_PATTERN.match(line)
            if not match:
                continue
            name, value = match.group(1), match.group(2).strip()
            if value and any(marker in name for marker in SECRET_NAME_MARKERS):
                leaked_assignments.append(f"{relative_path}:{line_number}:{name}")

    assert leaked_assignments == []
