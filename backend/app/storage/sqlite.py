from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import posixpath
import sqlite3
from typing import Iterator

from app.contracts import (
    ActionabilityFeatures,
    ArtifactKind,
    ArtifactManifest,
    ArtifactRecord,
    AssemblyQC,
    DecisionObject,
    JobDecisionResponse,
    JobState,
    JobStatus,
    MechanisticEvidence,
    NoveltyAssessment,
    PhenotypePrediction,
    QueueItem,
    RationaleCode,
    SampleInput,
    SampleMetadata,
    TriageDecision,
)
from app.contracts.common import SCHEMA_VERSION
from app.prediction.actionability import build_decision_provenance_notes


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json_dumps(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _normalize_display_path(path: str) -> str:
    return path.replace("\\", "/")


def _is_absolute_display_path(path: str) -> bool:
    normalized = _normalize_display_path(path)
    return normalized.startswith(("/", "//")) or (
        len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/"
    )


def _candidate_artifact_roots(path: str) -> tuple[str, ...]:
    normalized = _normalize_display_path(path)
    current = posixpath.dirname(normalized)
    candidates: list[str] = []
    while current and current not in {"", "."}:
        if current not in candidates:
            candidates.append(current)
        parent = posixpath.dirname(current)
        if parent == current or parent in {"", "."}:
            break
        current = parent
    return tuple(candidates)


def _path_matches_candidate(path: str, candidate: str) -> bool:
    normalized_path = _normalize_display_path(path)
    normalized_candidate = _normalize_display_path(candidate).rstrip("/")
    if not normalized_candidate:
        return False
    return normalized_path == normalized_candidate or normalized_path.startswith(
        f"{normalized_candidate}/"
    )


def _artifact_root_sort_key(candidate: str, normalized_paths: list[str]) -> tuple[int, int, int, int, int]:
    normalized_candidate = _normalize_display_path(candidate)
    support_count = sum(
        1 for path in normalized_paths if _path_matches_candidate(path, normalized_candidate)
    )
    artifact_priority = 1 if normalized_candidate.startswith("artifacts/") else 0
    absolute_priority = 1 if _is_absolute_display_path(normalized_candidate) else 0
    depth = len([segment for segment in normalized_candidate.rstrip("/").split("/") if segment])
    return (
        support_count,
        artifact_priority,
        absolute_priority,
        depth,
        len(normalized_candidate),
    )


def _display_artifact_root(paths: list[str]) -> str | None:
    normalized_paths = [_normalize_display_path(path) for path in paths if path]
    if not normalized_paths:
        return None

    candidate_roots = {
        candidate
        for path in normalized_paths
        for candidate in _candidate_artifact_roots(path)
    }
    if not candidate_roots:
        return None

    best_root = max(
        candidate_roots,
        key=lambda candidate: _artifact_root_sort_key(candidate, normalized_paths),
    )
    if _artifact_root_sort_key(best_root, normalized_paths)[0] == 0:
        return None
    return best_root


class SQLitePersistence:
    def __init__(self, db_path: Path, *, repo_root: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.repo_root = repo_root or _repo_root()
        self._initialize()

    def _initialize(self) -> None:
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                (self.repo_root / "backend/app/storage/sqlite_schema.sql").read_text(encoding="utf-8")
            )
            self._migrate_schema(connection)

    def _migrate_schema(self, connection: sqlite3.Connection) -> None:
        self._ensure_job_context_parent_key(connection)
        self._ensure_job_scoped_output_tables(connection)
        self._ensure_table_columns(
            connection,
            table_name="actionability",
            column_definitions=(
                "feature_warnings_json TEXT NOT NULL DEFAULT '[]'",
                "triage_warnings_json TEXT NOT NULL DEFAULT '[]'",
            ),
        )
        self._ensure_table_columns(
            connection,
            table_name="predictions",
            column_definitions=(
                "input_source_context TEXT",
                "input_provenance_source TEXT",
            ),
        )
        self._ensure_table_columns(
            connection,
            table_name="copilot_outputs",
            column_definitions=("answer_blocks_json TEXT NOT NULL DEFAULT '[]'",),
        )
        self._ensure_job_sample_snapshots(connection)
        connection.executescript(self._current_index_schema_sql())

    def _sqlite_schema_text(self) -> str:
        return (self.repo_root / "backend/app/storage/sqlite_schema.sql").read_text(encoding="utf-8")

    def _current_table_schema_sql(self, table_name: str) -> str:
        schema_text = self._sqlite_schema_text()
        start_marker = f"CREATE TABLE IF NOT EXISTS {table_name} ("
        start_index = schema_text.index(start_marker)
        end_index = schema_text.index(";\n", start_index) + 2
        return schema_text[start_index:end_index]

    def _current_index_schema_sql(self) -> str:
        return "\n".join(
            line
            for line in self._sqlite_schema_text().splitlines()
            if line.startswith("CREATE INDEX IF NOT EXISTS ")
        )

    def _table_column_names(self, connection: sqlite3.Connection, table_name: str) -> tuple[str, ...]:
        quoted_table_name = _quote_identifier(table_name)
        rows = sorted(
            connection.execute(f"PRAGMA table_info({quoted_table_name})").fetchall(),
            key=lambda row: row["cid"],
        )
        return tuple(row["name"] for row in rows)

    def _table_columns(self, connection: sqlite3.Connection, table_name: str) -> set[str]:
        return set(self._table_column_names(connection, table_name))

    def _table_exists(self, connection: sqlite3.Connection, table_name: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _has_unique_index(
        self,
        connection: sqlite3.Connection,
        *,
        table_name: str,
        columns: tuple[str, ...],
    ) -> bool:
        quoted_table_name = _quote_identifier(table_name)
        for index_row in connection.execute(f"PRAGMA index_list({quoted_table_name})").fetchall():
            if not index_row["unique"]:
                continue
            quoted_index_name = _quote_identifier(index_row["name"])
            index_columns = tuple(
                row["name"]
                for row in sorted(
                    connection.execute(f"PRAGMA index_info({quoted_index_name})").fetchall(),
                    key=lambda row: row["seqno"],
                )
            )
            if index_columns == columns:
                return True
        return False

    def _ensure_job_context_parent_key(self, connection: sqlite3.Connection) -> None:
        expected_columns = ("job_id", "sample_id", "target_drug")
        job_columns = self._table_columns(connection, "jobs")
        if not set(expected_columns).issubset(job_columns):
            return
        if self._has_unique_index(connection, table_name="jobs", columns=expected_columns):
            return
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_job_sample_target_unique "
            "ON jobs (job_id, sample_id, target_drug)"
        )

    def _table_primary_key_columns(self, connection: sqlite3.Connection, table_name: str) -> tuple[str, ...]:
        quoted_table_name = _quote_identifier(table_name)
        rows = connection.execute(f"PRAGMA table_info({quoted_table_name})").fetchall()
        primary_key_rows = sorted((row for row in rows if row["pk"]), key=lambda row: row["pk"])
        return tuple(row["name"] for row in primary_key_rows)

    def _has_job_context_foreign_key(self, connection: sqlite3.Connection, table_name: str) -> bool:
        quoted_table_name = _quote_identifier(table_name)
        foreign_key_rows = connection.execute(f"PRAGMA foreign_key_list({quoted_table_name})").fetchall()
        grouped_rows: dict[int, list[sqlite3.Row]] = {}
        for row in foreign_key_rows:
            grouped_rows.setdefault(row["id"], []).append(row)

        expected_columns = ("job_id", "sample_id", "target_drug")
        for rows in grouped_rows.values():
            ordered_rows = sorted(rows, key=lambda row: row["seq"])
            if ordered_rows[0]["table"] != "jobs":
                continue
            from_columns = tuple(row["from"] for row in ordered_rows)
            to_columns = tuple(row["to"] for row in ordered_rows)
            if from_columns == expected_columns and to_columns == expected_columns:
                return True
        return False

    def _legacy_backup_table_name(self, connection: sqlite3.Connection, table_name: str) -> str:
        base_name = f"{table_name}_legacy_pre_job_scope"
        if not self._table_exists(connection, base_name):
            return base_name
        index = 1
        while self._table_exists(connection, f"{base_name}_{index}"):
            index += 1
        return f"{base_name}_{index}"

    def _resolve_legacy_job_context(
        self,
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        available_columns: set[str],
    ) -> dict[str, str] | None:
        legacy_job_id = row["job_id"] if "job_id" in available_columns and row["job_id"] else None
        legacy_sample_id = (
            row["sample_id"] if "sample_id" in available_columns and row["sample_id"] else None
        )
        legacy_target_drug = (
            row["target_drug"] if "target_drug" in available_columns and row["target_drug"] else None
        )

        if legacy_job_id:
            job_row = connection.execute(
                "SELECT job_id, sample_id, target_drug FROM jobs WHERE job_id = ?",
                (legacy_job_id,),
            ).fetchone()
            if job_row is not None:
                if legacy_sample_id is not None and legacy_sample_id != job_row["sample_id"]:
                    return None
                if legacy_target_drug is not None and legacy_target_drug != job_row["target_drug"]:
                    return None
                return {
                    "job_id": job_row["job_id"],
                    "sample_id": job_row["sample_id"],
                    "target_drug": job_row["target_drug"],
                }

        if legacy_sample_id is None or legacy_target_drug is None:
            return None

        matching_jobs = connection.execute(
            """
            SELECT job_id, sample_id, target_drug
            FROM jobs
            WHERE sample_id = ? AND target_drug = ?
            ORDER BY created_at ASC, job_id ASC
            """,
            (legacy_sample_id, legacy_target_drug),
        ).fetchall()
        if len(matching_jobs) == 1:
            job_row = matching_jobs[0]
            return {
                "job_id": job_row["job_id"],
                "sample_id": job_row["sample_id"],
                "target_drug": job_row["target_drug"],
            }
        return None

    def _restore_job_scoped_output_rows(
        self,
        connection: sqlite3.Connection,
        *,
        table_name: str,
        backup_table_name: str,
    ) -> None:
        backup_columns = self._table_columns(connection, backup_table_name)
        if not backup_columns:
            return
        current_columns = self._table_column_names(connection, table_name)
        quoted_backup_table_name = _quote_identifier(backup_table_name)
        quoted_table_name = _quote_identifier(table_name)
        rows = connection.execute(f"SELECT * FROM {quoted_backup_table_name}").fetchall()
        unmigrated_rows = 0
        insertion_error: str | None = None

        for row in rows:
            context = self._resolve_legacy_job_context(
                connection,
                row=row,
                available_columns=backup_columns,
            )
            if context is None:
                unmigrated_rows += 1
                continue

            payload: dict[str, object] = {}
            for column_name in current_columns:
                if column_name in {"job_id", "sample_id", "target_drug"}:
                    payload[column_name] = context[column_name]
                    continue
                if column_name not in backup_columns:
                    continue
                value = row[column_name]
                if value is not None:
                    payload[column_name] = value

            insert_columns = tuple(payload)
            placeholders = ", ".join("?" for _ in insert_columns)
            quoted_insert_columns = ", ".join(_quote_identifier(column) for column in insert_columns)
            try:
                connection.execute(
                    f"INSERT OR REPLACE INTO {quoted_table_name} ({quoted_insert_columns}) "
                    f"VALUES ({placeholders})",
                    tuple(payload[column] for column in insert_columns),
                )
            except sqlite3.IntegrityError as exc:
                insertion_error = str(exc)
                unmigrated_rows += 1

        if unmigrated_rows:
            detail = f"{unmigrated_rows} row(s) from {backup_table_name}"
            if insertion_error:
                detail = f"{detail} could not be inserted ({insertion_error})"
            raise RuntimeError(
                "SQLite migration could not safely preserve legacy "
                f"{table_name} outputs. Export the backup table or reset the local "
                f"database before retrying: {detail}."
            )

    def _ensure_job_scoped_output_tables(self, connection: sqlite3.Connection) -> None:
        required_context_columns = {"job_id", "sample_id", "target_drug"}
        table_shapes = {
            "assembly_qc": {
                "primary_key": ("job_id",),
                "indexes": ("idx_assembly_qc_sample_target",),
            },
            "mechanistic_evidence": {
                "primary_key": ("id",),
                "indexes": ("idx_mechanistic_evidence_job_sample_target",),
            },
            "predictions": {
                "primary_key": ("job_id",),
                "indexes": ("idx_predictions_sample_target",),
            },
            "novelty": {
                "primary_key": ("job_id",),
                "indexes": ("idx_novelty_sample_target",),
            },
            "actionability": {
                "primary_key": ("job_id",),
                "indexes": ("idx_actionability_sample_target",),
            },
            "copilot_outputs": {
                "primary_key": ("job_id",),
                "indexes": (),
            },
            "artifacts": {
                "primary_key": ("artifact_id",),
                "indexes": (),
            },
            "queue_items": {
                "primary_key": ("job_id",),
                "indexes": ("idx_queue_items_priority",),
            },
        }
        for table_name, expected_shape in table_shapes.items():
            columns = self._table_columns(connection, table_name)
            if not columns:
                continue
            primary_key_columns = self._table_primary_key_columns(connection, table_name)
            is_current_shape = (
                required_context_columns.issubset(columns)
                and primary_key_columns == expected_shape["primary_key"]
                and self._has_job_context_foreign_key(connection, table_name)
            )
            if is_current_shape:
                continue

            backup_table_name = self._legacy_backup_table_name(connection, table_name)
            for index_name in expected_shape["indexes"]:
                connection.execute(f"DROP INDEX IF EXISTS {_quote_identifier(index_name)}")
            connection.execute(
                f"ALTER TABLE {_quote_identifier(table_name)} RENAME TO {_quote_identifier(backup_table_name)}"
            )
            connection.executescript(self._current_table_schema_sql(table_name))
            self._restore_job_scoped_output_rows(
                connection,
                table_name=table_name,
                backup_table_name=backup_table_name,
            )

    def _ensure_table_columns(
        self,
        connection: sqlite3.Connection,
        *,
        table_name: str,
        column_definitions: tuple[str, ...],
    ) -> None:
        columns = self._table_columns(connection, table_name)
        if not columns:
            return
        for definition in column_definitions:
            column_name = definition.split(maxsplit=1)[0]
            if column_name not in columns:
                connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {definition}")

    def _ensure_job_sample_snapshots(self, connection: sqlite3.Connection) -> None:
        job_columns = self._table_columns(connection, "jobs")
        if not job_columns:
            return
        if "sample_input_json" not in job_columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN sample_input_json TEXT")

        ambiguous_backfills = connection.execute(
            """
            SELECT sample_id, target_drug, COUNT(*) AS job_count
            FROM jobs
            GROUP BY sample_id, target_drug
            HAVING COUNT(*) > 1
               AND SUM(
                    CASE
                        WHEN sample_input_json IS NULL OR TRIM(sample_input_json) = '' THEN 1
                        ELSE 0
                    END
               ) > 0
            """
        ).fetchall()
        if ambiguous_backfills:
            formatted_pairs = ", ".join(
                f"{row['sample_id']}/{row['target_drug']}"
                for row in ambiguous_backfills[:5]
            )
            if len(ambiguous_backfills) > 5:
                formatted_pairs = f"{formatted_pairs}, ..."
            raise RuntimeError(
                "SQLite migration could not safely backfill per-job sample snapshots for rerun "
                "histories. Reset the local database before retrying: "
                f"{formatted_pairs}."
            )

        legacy_rows = connection.execute(
            """
            SELECT
                j.job_id,
                j.sample_id,
                j.target_drug,
                j.schema_version AS job_schema_version,
                j.created_at AS job_created_at,
                j.sample_input_json,
                s.schema_version AS sample_schema_version,
                s.organism_hint,
                s.accession,
                s.collection_date,
                s.source_context,
                s.country,
                s.provenance_source,
                s.fasta_path,
                s.fasta_uri,
                s.created_at AS sample_created_at
            FROM jobs j
            LEFT JOIN samples s
                ON s.sample_id = j.sample_id
               AND s.target_drug = j.target_drug
            WHERE j.sample_input_json IS NULL OR TRIM(j.sample_input_json) = ''
            """
        ).fetchall()
        for row in legacy_rows:
            sample_payload_json = self._legacy_sample_input_json(row)
            if sample_payload_json is None:
                continue
            connection.execute(
                "UPDATE jobs SET sample_input_json = ? WHERE job_id = ?",
                (sample_payload_json, row["job_id"]),
            )

    def _legacy_sample_input_json(self, row: sqlite3.Row) -> str | None:
        if not row["fasta_path"] and not row["fasta_uri"]:
            return None
        sample = SampleInput(
            sample_id=row["sample_id"],
            organism_hint=row["organism_hint"],
            target_drug=row["target_drug"],
            fasta_path=row["fasta_path"],
            fasta_uri=row["fasta_uri"],
            metadata=SampleMetadata(
                accession=row["accession"],
                collection_date=row["collection_date"],
                source_context=row["source_context"],
                country=row["country"],
                provenance_source=row["provenance_source"],
                schema_version=row["sample_schema_version"] or row["job_schema_version"],
                created_at=row["sample_created_at"] or row["job_created_at"] or _now_iso(),
            ),
            schema_version=row["sample_schema_version"] or row["job_schema_version"],
            created_at=row["sample_created_at"] or row["job_created_at"] or _now_iso(),
        )
        return sample.model_dump_json()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def upsert_sample(self, sample: SampleInput) -> None:
        payload = sample.model_dump(mode="json")
        metadata = payload["metadata"]
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO samples (
                    sample_id,
                    schema_version,
                    organism_hint,
                    target_drug,
                    accession,
                    collection_date,
                    source_context,
                    country,
                    provenance_source,
                    fasta_path,
                    fasta_uri,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["sample_id"],
                    payload["schema_version"],
                    payload["organism_hint"],
                    payload["target_drug"],
                    metadata["accession"],
                    metadata["collection_date"],
                    metadata["source_context"],
                    metadata["country"],
                    metadata["provenance_source"],
                    payload["fasta_path"],
                    payload["fasta_uri"],
                    payload["created_at"],
                ),
            )

    def create_job(self, job_status: JobStatus, *, sample: SampleInput) -> None:
        payload = job_status.model_dump(mode="json")
        sample_payload_json = sample.model_dump_json()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id,
                    sample_id,
                    target_drug,
                    sample_input_json,
                    schema_version,
                    status,
                    current_step,
                    failure_code,
                    warnings_json,
                    submitted_at,
                    updated_at,
                    completed_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["job_id"],
                    payload["sample_id"],
                    payload["target_drug"],
                    sample_payload_json,
                    payload["schema_version"],
                    payload["status"],
                    payload["current_step"],
                    payload["failure_code"],
                    _json_dumps(payload["warnings"]),
                    payload["submitted_at"],
                    payload["updated_at"],
                    payload["completed_at"],
                    payload["created_at"],
                ),
            )

    def update_job_status(self, job_status: JobStatus) -> None:
        payload = job_status.model_dump(mode="json")
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET
                    status = ?,
                    current_step = ?,
                    failure_code = ?,
                    warnings_json = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE job_id = ?
                """,
                (
                    payload["status"],
                    payload["current_step"],
                    payload["failure_code"],
                    _json_dumps(payload["warnings"]),
                    payload["updated_at"],
                    payload["completed_at"],
                    payload["job_id"],
                ),
            )

    def save_assembly_qc(self, *, job_id: str, target_drug: str, qc: AssemblyQC) -> None:
        payload = qc.model_dump(mode="json")
        if payload["job_id"] != job_id or payload["target_drug"] != target_drug:
            raise ValueError("Assembly QC context must match the persisted job_id and target_drug.")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO assembly_qc (
                    job_id,
                    sample_id,
                    target_drug,
                    schema_version,
                    file_valid,
                    sequence_count,
                    total_bases,
                    ambiguous_base_fraction,
                    organism_consistency,
                    missing_metadata_fields_json,
                    qc_status,
                    warnings_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["job_id"],
                    payload["sample_id"],
                    payload["target_drug"],
                    payload["schema_version"],
                    int(payload["file_valid"]),
                    payload["sequence_count"],
                    payload["total_bases"],
                    payload["ambiguous_base_fraction"],
                    payload["organism_consistency"],
                    _json_dumps(payload["missing_metadata_fields"]),
                    payload["qc_status"],
                    _json_dumps(payload["warnings"]),
                    payload["created_at"],
                ),
            )

    def replace_mechanistic_evidence(self, rows: list[MechanisticEvidence]) -> None:
        if not rows:
            return
        with self.connect() as connection:
            connection.execute("DELETE FROM mechanistic_evidence WHERE job_id = ?", (rows[0].job_id,))
            connection.executemany(
                """
                INSERT INTO mechanistic_evidence (
                    job_id,
                    sample_id,
                    target_drug,
                    schema_version,
                    source_tool,
                    gene_symbol,
                    mutation,
                    mechanism_class,
                    drug_association_json,
                    support_level,
                    interpretation,
                    raw_row_index,
                    raw_artifact_id,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        payload["job_id"],
                        payload["sample_id"],
                        payload["target_drug"],
                        payload["schema_version"],
                        payload["source_tool"],
                        payload["gene_symbol"],
                        payload["mutation"],
                        payload["mechanism_class"],
                        _json_dumps(payload["drug_association"]),
                        payload["support_level"],
                        payload["interpretation"],
                        payload["raw_row_index"],
                        payload["raw_artifact_id"],
                        payload["created_at"],
                    )
                    for payload in (row.model_dump(mode="json") for row in rows)
                ],
            )

    def save_prediction(self, prediction: PhenotypePrediction) -> None:
        payload = prediction.model_dump(mode="json")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO predictions (
                    job_id,
                    sample_id,
                    target_drug,
                    schema_version,
                    predicted_phenotype,
                    probability,
                    calibration_status,
                    uncertainty_score,
                    feature_set_version,
                    model_version,
                    split_context,
                    input_source_context,
                    input_provenance_source,
                    warnings_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["job_id"],
                    payload["sample_id"],
                    payload["target_drug"],
                    payload["schema_version"],
                    payload["predicted_phenotype"],
                    payload["probability"],
                    payload["calibration_status"],
                    payload["uncertainty_score"],
                    payload["feature_set_version"],
                    payload["model_version"],
                    payload["model_training_split_context"],
                    payload["input_source_context"],
                    payload["input_provenance_source"],
                    _json_dumps(payload["warnings"]),
                    payload["created_at"],
                ),
            )

    def save_novelty(self, novelty: NoveltyAssessment) -> None:
        payload = novelty.model_dump(mode="json")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO novelty (
                    job_id,
                    sample_id,
                    target_drug,
                    schema_version,
                    reference_snapshot_id,
                    nearest_neighbor_id,
                    nearest_neighbor_distance,
                    novelty_score,
                    novelty_percentile,
                    novelty_bucket,
                    missing_reference,
                    uncertainty_flag,
                    warnings_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["job_id"],
                    payload["sample_id"],
                    payload["target_drug"],
                    payload["schema_version"],
                    payload["reference_snapshot_id"],
                    payload["nearest_neighbor_id"],
                    payload["nearest_neighbor_distance"],
                    payload["novelty_score"],
                    payload["novelty_percentile"],
                    payload["novelty_bucket"],
                    int(payload["missing_reference"]),
                    int(payload["uncertainty_flag"]),
                    _json_dumps(payload["warnings"]),
                    payload["created_at"],
                ),
            )

    def save_actionability(
        self,
        *,
        features: ActionabilityFeatures,
        triage: TriageDecision,
        decision_warnings: list[str],
    ) -> None:
        feature_payload = features.model_dump(mode="json")
        triage_payload = triage.model_dump(mode="json")
        context_fields = ("job_id", "sample_id", "target_drug", "threshold_version")
        mismatched_fields = [
            field_name
            for field_name in context_fields
            if feature_payload[field_name] != triage_payload[field_name]
        ]
        if mismatched_fields:
            raise ValueError(
                "Actionability triage context must match actionability feature context "
                f"for {', '.join(mismatched_fields)}."
            )
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO actionability (
                    job_id,
                    sample_id,
                    target_drug,
                    schema_version,
                    actionability_score,
                    mechanism_concordance,
                    prediction_entropy,
                    qc_risk,
                    novelty_risk,
                    metadata_completeness,
                    threshold_version,
                    triage_decision,
                    severity,
                    recommended_next_step,
                    rationale_codes_json,
                    warnings_json,
                    feature_warnings_json,
                    triage_warnings_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feature_payload["job_id"],
                    feature_payload["sample_id"],
                    feature_payload["target_drug"],
                    feature_payload["schema_version"],
                    feature_payload["actionability_score"],
                    None if feature_payload["mechanism_concordance"] is None else int(feature_payload["mechanism_concordance"]),
                    feature_payload["prediction_entropy"],
                    feature_payload["qc_risk"],
                    feature_payload["novelty_risk"],
                    feature_payload["metadata_completeness"],
                    feature_payload["threshold_version"],
                    triage_payload["triage"],
                    triage_payload["severity"],
                    triage_payload["recommended_next_step"],
                    _json_dumps(triage_payload["rationale_codes"]),
                    _json_dumps(decision_warnings),
                    _json_dumps(feature_payload["warnings"]),
                    _json_dumps(triage_payload["warnings"]),
                    feature_payload["created_at"],
                ),
            )

    def save_artifacts(self, records: list[ArtifactRecord]) -> None:
        if not records:
            return
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO artifacts (
                    artifact_id,
                    job_id,
                    sample_id,
                    target_drug,
                    schema_version,
                    kind,
                    path,
                    media_type,
                    generated_by,
                    sha256,
                    size_bytes,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        payload["artifact_id"],
                        payload["job_id"],
                        payload["sample_id"],
                        payload["target_drug"],
                        payload["schema_version"],
                        payload["kind"],
                        payload["path"],
                        payload["media_type"],
                        payload["generated_by"],
                        payload["sha256"],
                        payload["size_bytes"],
                        payload["created_at"],
                    )
                    for payload in (record.model_dump(mode="json") for record in records)
                ],
            )

    def save_queue_item(self, queue_item: QueueItem) -> None:
        payload = queue_item.model_dump(mode="json")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO queue_items (
                    job_id,
                    sample_id,
                    target_drug,
                    schema_version,
                    triage,
                    severity,
                    status,
                    queue_priority,
                    headline,
                    rationale_codes_json,
                    updated_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["job_id"],
                    payload["sample_id"],
                    payload["target_drug"],
                    payload["schema_version"],
                    payload["triage"],
                    payload["severity"],
                    payload["status"],
                    payload["queue_priority"],
                    payload["headline"],
                    _json_dumps(payload["rationale_codes"]),
                    payload["updated_at"],
                    payload["created_at"],
                ),
            )

    def get_job_status(self, job_id: str) -> JobStatus | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return JobStatus(
            job_id=row["job_id"],
            sample_id=row["sample_id"],
            target_drug=row["target_drug"],
            status=row["status"],
            current_step=row["current_step"],
            failure_code=row["failure_code"],
            warnings=json.loads(row["warnings_json"]),
            submitted_at=row["submitted_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            schema_version=row["schema_version"],
            created_at=row["created_at"],
        )

    def get_job_artifact_manifest(self, job_id: str) -> ArtifactManifest | None:
        with self.connect() as connection:
            job_row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if job_row is None:
                return None
            artifact_rows = connection.execute(
                "SELECT * FROM artifacts WHERE job_id = ? ORDER BY created_at ASC, artifact_id ASC",
                (job_id,),
            ).fetchall()

        if not artifact_rows:
            return None

        artifacts = [
            ArtifactRecord(
                artifact_id=row["artifact_id"],
                job_id=row["job_id"],
                sample_id=row["sample_id"],
                target_drug=row["target_drug"],
                kind=row["kind"],
                path=row["path"],
                media_type=row["media_type"],
                generated_by=row["generated_by"],
                sha256=row["sha256"],
                size_bytes=row["size_bytes"],
                schema_version=row["schema_version"],
                created_at=row["created_at"],
            )
            for row in artifact_rows
        ]
        artifact_root = _display_artifact_root(
            [artifact.path for artifact in artifacts if artifact.kind != ArtifactKind.INPUT_FASTA]
        )
        return ArtifactManifest(
            job_id=job_row["job_id"],
            sample_id=job_row["sample_id"],
            target_drug=job_row["target_drug"],
            artifact_root=artifact_root,
            artifacts=artifacts,
            schema_version=job_row["schema_version"],
            created_at=artifact_rows[0]["created_at"],
        )

    def list_queue_items(
        self,
        *,
        triage: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[QueueItem]:
        clauses: list[str] = []
        params: list[object] = []
        if triage is not None:
            clauses.append("triage = ?")
            params.append(triage)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        query = (
            "SELECT * FROM queue_items"
            f"{where_clause} "
            "ORDER BY queue_priority ASC, updated_at DESC, created_at DESC "
            "LIMIT ?"
        )
        with self.connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()

        return [
            QueueItem(
                job_id=row["job_id"],
                sample_id=row["sample_id"],
                target_drug=row["target_drug"],
                triage=row["triage"],
                severity=row["severity"],
                status=row["status"],
                queue_priority=row["queue_priority"],
                headline=row["headline"],
                rationale_codes=json.loads(row["rationale_codes_json"]),
                updated_at=row["updated_at"],
                schema_version=row["schema_version"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_queue_item(self, job_id: str) -> QueueItem | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM queue_items WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return QueueItem(
            job_id=row["job_id"],
            sample_id=row["sample_id"],
            target_drug=row["target_drug"],
            triage=row["triage"],
            severity=row["severity"],
            status=row["status"],
            queue_priority=row["queue_priority"],
            headline=row["headline"],
            rationale_codes=json.loads(row["rationale_codes_json"]),
            updated_at=row["updated_at"],
            schema_version=row["schema_version"],
            created_at=row["created_at"],
        )

    def get_decision(self, job_id: str) -> DecisionObject | None:
        with self.connect() as connection:
            job_row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if job_row is None:
                return None

            qc_row = connection.execute("SELECT * FROM assembly_qc WHERE job_id = ?", (job_id,)).fetchone()
            prediction_row = connection.execute("SELECT * FROM predictions WHERE job_id = ?", (job_id,)).fetchone()
            novelty_row = connection.execute("SELECT * FROM novelty WHERE job_id = ?", (job_id,)).fetchone()
            actionability_row = connection.execute("SELECT * FROM actionability WHERE job_id = ?", (job_id,)).fetchone()
            evidence_rows = connection.execute(
                "SELECT * FROM mechanistic_evidence WHERE job_id = ? ORDER BY raw_row_index, id",
                (job_id,),
            ).fetchall()

        if any(row is None for row in (qc_row, prediction_row, novelty_row, actionability_row)):
            return None

        sample = SampleInput.model_validate_json(job_row["sample_input_json"])
        qc = AssemblyQC(
            job_id=qc_row["job_id"],
            sample_id=qc_row["sample_id"],
            target_drug=qc_row["target_drug"],
            file_valid=bool(qc_row["file_valid"]),
            sequence_count=qc_row["sequence_count"],
            total_bases=qc_row["total_bases"],
            ambiguous_base_fraction=qc_row["ambiguous_base_fraction"],
            organism_consistency=qc_row["organism_consistency"],
            missing_metadata_fields=json.loads(qc_row["missing_metadata_fields_json"]),
            qc_status=qc_row["qc_status"],
            warnings=json.loads(qc_row["warnings_json"]),
            schema_version=qc_row["schema_version"],
            created_at=qc_row["created_at"],
        )
        evidence = [
            MechanisticEvidence(
                job_id=row["job_id"],
                sample_id=row["sample_id"],
                target_drug=row["target_drug"],
                source_tool=row["source_tool"],
                gene_symbol=row["gene_symbol"],
                mutation=row["mutation"],
                mechanism_class=row["mechanism_class"],
                drug_association=json.loads(row["drug_association_json"]),
                support_level=row["support_level"],
                interpretation=row["interpretation"],
                raw_row_index=row["raw_row_index"],
                raw_artifact_id=row["raw_artifact_id"],
                schema_version=row["schema_version"],
                created_at=row["created_at"],
            )
            for row in evidence_rows
        ]
        prediction = PhenotypePrediction(
            job_id=prediction_row["job_id"],
            sample_id=prediction_row["sample_id"],
            target_drug=prediction_row["target_drug"],
            predicted_phenotype=prediction_row["predicted_phenotype"],
            probability=prediction_row["probability"],
            calibration_status=prediction_row["calibration_status"],
            uncertainty_score=prediction_row["uncertainty_score"],
            feature_set_version=prediction_row["feature_set_version"],
            model_version=prediction_row["model_version"],
            model_training_split_context=prediction_row["split_context"],
            input_source_context=prediction_row["input_source_context"] or sample.metadata.source_context,
            input_provenance_source=(
                prediction_row["input_provenance_source"] or sample.metadata.provenance_source
            ),
            warnings=json.loads(prediction_row["warnings_json"]),
            schema_version=prediction_row["schema_version"],
            created_at=prediction_row["created_at"],
        )
        novelty = NoveltyAssessment(
            job_id=novelty_row["job_id"],
            sample_id=novelty_row["sample_id"],
            target_drug=novelty_row["target_drug"],
            reference_snapshot_id=novelty_row["reference_snapshot_id"],
            nearest_neighbor_id=novelty_row["nearest_neighbor_id"],
            nearest_neighbor_distance=novelty_row["nearest_neighbor_distance"],
            novelty_score=novelty_row["novelty_score"],
            novelty_percentile=novelty_row["novelty_percentile"],
            novelty_bucket=novelty_row["novelty_bucket"],
            missing_reference=bool(novelty_row["missing_reference"]),
            uncertainty_flag=bool(novelty_row["uncertainty_flag"]),
            warnings=json.loads(novelty_row["warnings_json"]),
            schema_version=novelty_row["schema_version"],
            created_at=novelty_row["created_at"],
        )
        features = ActionabilityFeatures(
            job_id=actionability_row["job_id"],
            sample_id=actionability_row["sample_id"],
            target_drug=actionability_row["target_drug"],
            actionability_score=actionability_row["actionability_score"],
            mechanism_concordance=(
                None
                if actionability_row["mechanism_concordance"] is None
                else bool(actionability_row["mechanism_concordance"])
            ),
            prediction_entropy=actionability_row["prediction_entropy"],
            qc_risk=actionability_row["qc_risk"],
            novelty_risk=actionability_row["novelty_risk"],
            metadata_completeness=actionability_row["metadata_completeness"],
            threshold_version=actionability_row["threshold_version"],
            warnings=json.loads(actionability_row["feature_warnings_json"]),
            schema_version=actionability_row["schema_version"],
            created_at=actionability_row["created_at"],
        )
        triage = TriageDecision(
            job_id=actionability_row["job_id"],
            sample_id=actionability_row["sample_id"],
            target_drug=actionability_row["target_drug"],
            triage=actionability_row["triage_decision"],
            severity=actionability_row["severity"],
            recommended_next_step=actionability_row["recommended_next_step"],
            threshold_version=actionability_row["threshold_version"],
            rationale_codes=json.loads(actionability_row["rationale_codes_json"]),
            warnings=json.loads(actionability_row["triage_warnings_json"]),
            schema_version=actionability_row["schema_version"],
            created_at=actionability_row["created_at"],
        )
        return DecisionObject(
            job_id=job_row["job_id"],
            sample=sample,
            assembly_qc=qc,
            mechanistic_evidence=evidence,
            phenotype_prediction=prediction,
            novelty_assessment=novelty,
            actionability_features=features,
            triage_decision=triage,
            rationale_codes=[RationaleCode(code) for code in json.loads(actionability_row["rationale_codes_json"])],
            warnings=json.loads(actionability_row["warnings_json"]),
            artifact_manifest_id=None,
            provenance_notes=build_decision_provenance_notes(sample=sample, prediction=prediction),
            schema_version=SCHEMA_VERSION,
        )

    def get_job_decision_response(self, job_id: str) -> JobDecisionResponse | None:
        job_status = self.get_job_status(job_id)
        decision = self.get_decision(job_id)
        if job_status is None or decision is None:
            return None
        return JobDecisionResponse(job_status=job_status, decision=decision)
