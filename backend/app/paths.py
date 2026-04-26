from __future__ import annotations

from pathlib import Path


def resolve_local_path(value: str | Path, *, repo_root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        if any(part == ".." for part in path.parts):
            raise ValueError("Local file paths must not include parent traversal.")
        path = repo_root / path
    return path.resolve(strict=False)


def serialize_local_path(path: Path, *, repo_root: Path) -> str:
    resolved = path.resolve(strict=False)
    repo_root_resolved = repo_root.resolve(strict=False)
    try:
        return resolved.relative_to(repo_root_resolved).as_posix()
    except ValueError:
        return resolved.as_posix()


def path_is_within(path: Path, *, root: Path) -> bool:
    resolved_path = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
        return True
    except ValueError:
        return False


def display_path(path: Path, *, repo_root: Path) -> str:
    resolved = path.resolve(strict=False)
    repo_root_resolved = repo_root.resolve(strict=False)
    try:
        return resolved.relative_to(repo_root_resolved).as_posix()
    except ValueError:
        return resolved.as_posix()
