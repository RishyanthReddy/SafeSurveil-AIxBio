from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


DEFAULT_NCBI_DATASETS_BASE_URL = "https://api.ncbi.nlm.nih.gov/datasets/v2"
DEFAULT_NCBI_PATHOGEN_DETECTION_BASE_URL = "https://ftp.ncbi.nlm.nih.gov/pathogen"
LEGACY_NCBI_PATHOGEN_DETECTION_BASE_URL = "https://www.ncbi.nlm.nih.gov/pathogens"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _env(local_env: dict[str, str], name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is not None:
        return value
    return local_env.get(name, default)


def _as_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_positive_int(value: str | None, *, default: int, name: str) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _optional_env(local_env: dict[str, str], name: str) -> str | None:
    value = _env(local_env, name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _repo_relative_path(repo_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return repo_root / path


def _normalize_ncbi_pathogen_detection_base_url(value: str | None) -> str:
    normalized = (value or DEFAULT_NCBI_PATHOGEN_DETECTION_BASE_URL).strip().rstrip("/")
    if not normalized:
        return DEFAULT_NCBI_PATHOGEN_DETECTION_BASE_URL
    if normalized == LEGACY_NCBI_PATHOGEN_DETECTION_BASE_URL:
        return DEFAULT_NCBI_PATHOGEN_DETECTION_BASE_URL
    return normalized


@dataclass(frozen=True)
class IntegrationSettings:
    dataset_root: Path = Path("data")
    log_level: str = "INFO"
    ncbi_api_key: str | None = None
    ncbi_datasets_base_url: str = DEFAULT_NCBI_DATASETS_BASE_URL
    ncbi_pathogen_detection_base_url: str = DEFAULT_NCBI_PATHOGEN_DETECTION_BASE_URL
    live_http_timeout_seconds: int = 30
    live_http_retry_count: int = 2
    bv_brc_username: str | None = None
    bv_brc_username_alt: str | None = None
    bv_brc_password: str | None = None
    bv_brc_auth_url: str = "https://user.patricbrc.org/authenticate"
    bv_brc_api_base_url: str = "https://www.patricbrc.org/api"
    bv_brc_token_path: Path = Path("~/.patric_token")
    amrfinderplus_bin: str | None = None
    amrfinderplus_db: Path | None = None
    mash_bin: str | None = None


@dataclass(frozen=True)
class LLMSettings:
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    fallback_model: str | None = "inclusionai/ling-2.6-flash:free"
    reasoning_enabled: bool = False
    mock_mode: bool = False
    timeout_seconds: int = 30
    retry_count: int = 2


@dataclass(frozen=True)
class ThesysSettings:
    api_key: str | None = None
    base_url: str = "https://api.thesys.dev/v1/embed"
    model: str = "c1/anthropic/claude-sonnet-4/v-20251230"
    timeout_seconds: int = 30
    retry_count: int = 2


@dataclass(frozen=True)
class AppSettings:
    app_env: str
    repo_root: Path
    artifact_root: Path
    sqlite_db_path: Path
    use_fixtures: bool
    demo_mode: bool
    data_root: Path = Path("data")
    log_level: str = "INFO"
    integrations: IntegrationSettings = field(default_factory=IntegrationSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    thesys: ThesysSettings = field(default_factory=ThesysSettings)


def load_settings() -> AppSettings:
    repo_root = _repo_root()
    local_env = _load_env_file(repo_root / ".env")
    artifact_root = _repo_relative_path(
        repo_root,
        _env(local_env, "ARTIFACT_ROOT", "artifacts") or "artifacts",
    )
    sqlite_db_path = _repo_relative_path(
        repo_root,
        _env(local_env, "SQLITE_DB_PATH", "data/safesurveil.sqlite")
        or "data/safesurveil.sqlite"
    )
    data_root = _repo_relative_path(
        repo_root,
        _env(local_env, "DATASET_ROOT", "data") or "data",
    )
    log_level = (_env(local_env, "LOG_LEVEL", "INFO") or "INFO").strip().upper() or "INFO"
    amrfinderplus_db = _optional_env(local_env, "AMRFINDERPLUS_DB")
    integrations = IntegrationSettings(
        dataset_root=data_root,
        log_level=log_level,
        ncbi_api_key=_optional_env(local_env, "NCBI_API_KEY"),
        ncbi_datasets_base_url=(
            _env(
                local_env,
                "NCBI_DATASETS_BASE_URL",
                DEFAULT_NCBI_DATASETS_BASE_URL,
            )
            or DEFAULT_NCBI_DATASETS_BASE_URL
        ).strip(),
        ncbi_pathogen_detection_base_url=_normalize_ncbi_pathogen_detection_base_url(
            _env(
                local_env,
                "NCBI_PATHOGEN_DETECTION_BASE_URL",
                DEFAULT_NCBI_PATHOGEN_DETECTION_BASE_URL,
            )
        ),
        live_http_timeout_seconds=_as_positive_int(
            _env(local_env, "LIVE_HTTP_TIMEOUT_SECONDS"),
            default=30,
            name="LIVE_HTTP_TIMEOUT_SECONDS",
        ),
        live_http_retry_count=_as_positive_int(
            _env(local_env, "LIVE_HTTP_RETRY_COUNT"),
            default=2,
            name="LIVE_HTTP_RETRY_COUNT",
        ),
        bv_brc_username=_optional_env(local_env, "BV_BRC_USERNAME"),
        bv_brc_username_alt=_optional_env(local_env, "BV_BRC_USERNAME_ALT"),
        bv_brc_password=_optional_env(local_env, "BV_BRC_PASSWORD"),
        bv_brc_auth_url=(
            _env(
                local_env,
                "BV_BRC_AUTH_URL",
                "https://user.patricbrc.org/authenticate",
            )
            or "https://user.patricbrc.org/authenticate"
        ).strip(),
        bv_brc_api_base_url=(
            _env(
                local_env,
                "BV_BRC_API_BASE_URL",
                "https://www.patricbrc.org/api",
            )
            or "https://www.patricbrc.org/api"
        ).strip(),
        bv_brc_token_path=Path(
            _env(local_env, "BV_BRC_TOKEN_PATH", "~/.patric_token") or "~/.patric_token"
        ).expanduser(),
        amrfinderplus_bin=_optional_env(local_env, "AMRFINDERPLUS_BIN"),
        amrfinderplus_db=Path(amrfinderplus_db).expanduser() if amrfinderplus_db else None,
        mash_bin=_optional_env(local_env, "MASH_BIN"),
    )
    llm_settings = LLMSettings(
        provider=_optional_env(local_env, "LLM_PROVIDER"),
        base_url=_optional_env(local_env, "LLM_BASE_URL"),
        api_key=_optional_env(local_env, "LLM_API_KEY"),
        model=_optional_env(local_env, "LLM_MODEL"),
        fallback_model=(
            _optional_env(local_env, "LLM_FALLBACK_MODEL")
            or "inclusionai/ling-2.6-flash:free"
        ),
        reasoning_enabled=_as_bool(
            _env(local_env, "LLM_REASONING_ENABLED"),
            default=False,
        ),
        mock_mode=_as_bool(_env(local_env, "LLM_MOCK_MODE"), default=False),
        timeout_seconds=integrations.live_http_timeout_seconds,
        retry_count=integrations.live_http_retry_count,
    )
    thesys_settings = ThesysSettings(
        api_key=_optional_env(local_env, "THESYS_API_KEY"),
        base_url=(
            _env(local_env, "THESYS_BASE_URL", "https://api.thesys.dev/v1/embed")
            or "https://api.thesys.dev/v1/embed"
        ).strip(),
        model=(
            _env(
                local_env,
                "THESYS_MODEL",
                "c1/anthropic/claude-sonnet-4/v-20251230",
            )
            or "c1/anthropic/claude-sonnet-4/v-20251230"
        ).strip(),
        timeout_seconds=integrations.live_http_timeout_seconds,
        retry_count=integrations.live_http_retry_count,
    )
    return AppSettings(
        app_env=_env(local_env, "APP_ENV", "local") or "local",
        repo_root=repo_root,
        artifact_root=artifact_root,
        sqlite_db_path=sqlite_db_path,
        use_fixtures=_as_bool(_env(local_env, "USE_FIXTURES"), default=False),
        demo_mode=_as_bool(_env(local_env, "DEMO_MODE"), default=False),
        data_root=data_root,
        log_level=log_level,
        integrations=integrations,
        llm=llm_settings,
        thesys=thesys_settings,
    )
