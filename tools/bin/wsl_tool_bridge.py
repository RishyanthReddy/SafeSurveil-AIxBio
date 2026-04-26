from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
WINDOWS_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/].*")
URI_SCHEME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
WINDOWS_RELATIVE_PATH_HINTS = {
    ".fa",
    ".fna",
    ".fasta",
    ".fas",
    ".fsa",
    ".msh",
    ".tsv",
    ".json",
    ".txt",
}


@lru_cache(maxsize=None)
def _linux_executable(tool: str) -> str:
    mapping = {
        "amrfinder": "amrfinder",
        "mash": "mash",
    }
    try:
        command_name = mapping[tool]
    except KeyError as exc:
        raise RuntimeError(f"Unsupported WSL bioinformatics tool: {tool}") from exc

    probe = subprocess.run(
        [
            "wsl.exe",
            "--exec",
            "sh",
            "-lc",
            f'PATH="$HOME/.local/bin:$PATH"; command -v -- {command_name}',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    executable_paths = probe.stdout.strip().splitlines()
    if probe.returncode != 0 or not executable_paths:
        details = probe.stderr.strip() or f"{command_name} is not available inside WSL."
        raise RuntimeError(details)
    return executable_paths[-1].strip()


def _windows_to_wsl_path(path: Path) -> str:
    resolved = path.resolve(strict=False)
    drive = resolved.drive.rstrip(":").lower()
    if not drive:
        raise RuntimeError(f"Cannot translate non-drive path into WSL format: {resolved}")
    remainder = resolved.as_posix()[2:]
    return f"/mnt/{drive}{remainder}"


def _looks_like_relative_path(argument: str) -> bool:
    if not argument or argument.startswith("-") or URI_SCHEME_PATTERN.match(argument):
        return False
    if "/" in argument or "\\" in argument or argument.startswith("."):
        return True
    return Path(argument).suffix.lower() in WINDOWS_RELATIVE_PATH_HINTS


def _candidate_windows_path(argument: str) -> Path | None:
    if WINDOWS_PATH_PATTERN.match(argument):
        return Path(argument).expanduser().resolve(strict=False)
    if argument.startswith("/"):
        return None
    if not _looks_like_relative_path(argument):
        return None

    relative_path = Path(argument).expanduser()
    for base in (Path.cwd(), REPO_ROOT):
        candidate = (base / relative_path).resolve(strict=False)
        if candidate.exists() or candidate.parent.exists():
            return candidate
    return None


def _translate_argument(argument: str) -> str:
    candidate = _candidate_windows_path(argument)
    if candidate is None:
        return argument
    return _windows_to_wsl_path(candidate)


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: wsl_tool_bridge.py <amrfinder|mash> [args...]")

    tool = sys.argv[1]
    translated_args = [_translate_argument(argument) for argument in sys.argv[2:]]
    command = ["wsl.exe", "--exec", _linux_executable(tool), *translated_args]
    completed = subprocess.run(command)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
