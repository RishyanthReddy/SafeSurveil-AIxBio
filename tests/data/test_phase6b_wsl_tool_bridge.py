from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_BIN = REPO_ROOT / "tools" / "bin"
pytestmark = pytest.mark.live


def _run_wrapper(wrapper_name: str, *args: str, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["cmd.exe", "/c", str(TOOLS_BIN / wrapper_name), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_live_wsl_bridge_discovers_amrfinder_without_matching_windows_username() -> None:
    completed = _run_wrapper(
        "amrfinderplus.cmd",
        "--version",
        env_overrides={"USERNAME": "phase6b_wrong_windows_user"},
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert completed.stdout.strip() or completed.stderr.strip()


def test_live_wsl_bridge_discovers_mash_without_matching_windows_username() -> None:
    completed = _run_wrapper(
        "mash.cmd",
        "--version",
        env_overrides={"USERNAME": "phase6b_wrong_windows_user"},
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert completed.stdout.strip() or completed.stderr.strip()


def test_live_wsl_bridge_translates_repo_relative_amrfinder_paths() -> None:
    output_dir = REPO_ROOT / "artifacts" / "runs" / "phase6b_wsl_bridge"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "sample_001.amrfinder.tsv"
    if output_path.exists():
        output_path.unlink()

    completed = _run_wrapper(
        "amrfinderplus.cmd",
        "-n",
        "data/fixtures/smoke/sample_001.fasta",
        "-o",
        output_path.relative_to(REPO_ROOT).as_posix(),
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8").strip()
