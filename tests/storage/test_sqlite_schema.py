from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.contracts import (
    ActionabilityFeatures,
    JobStatus,
    NoveltyAssessment,
    PhenotypePrediction,
    SampleInput,
    TriageDecision,
)
from app.storage import SQLitePersistence


REPO_ROOT = Path(__file__).resolve().parents[2]


def _open_schema_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript((REPO_ROOT / "backend/app/storage/sqlite_schema.sql").read_text(encoding="utf-8"))
    return connection


def _legacy_schema_without_sample_input_json() -> str:
    return (
        (REPO_ROOT / "backend/app/storage/sqlite_schema.sql")
        .read_text(encoding="utf-8")
        .replace("    sample_input_json TEXT NOT NULL,\n", "")
    )


def _legacy_schema_without_actionability_warning_columns() -> str:
    return (
        (REPO_ROOT / "backend/app/storage/sqlite_schema.sql")
        .read_text(encoding="utf-8")
        .replace("    feature_warnings_json TEXT NOT NULL DEFAULT '[]',\n", "")
        .replace("    triage_warnings_json TEXT NOT NULL DEFAULT '[]',\n", "")
    )


def _legacy_schema_without_job_context_unique() -> str:
    return (
        (REPO_ROOT / "backend/app/storage/sqlite_schema.sql")
        .read_text(encoding="utf-8")
        .replace("    UNIQUE (job_id, sample_id, target_drug),\n", "")
    )


def _legacy_schema_with_pre_job_scoped_predictions() -> str:
    schema = (REPO_ROOT / "backend/app/storage/sqlite_schema.sql").read_text(encoding="utf-8")
    start_index = schema.index("CREATE TABLE IF NOT EXISTS predictions (")
    end_index = schema.index("CREATE TABLE IF NOT EXISTS novelty (")
    legacy_predictions_sql = """CREATE TABLE IF NOT EXISTS predictions (
    sample_id TEXT NOT NULL,
    target_drug TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    predicted_phenotype TEXT NOT NULL,
    probability REAL NOT NULL,
    calibration_status TEXT NOT NULL,
    uncertainty_score REAL,
    feature_set_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    split_context TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    PRIMARY KEY (sample_id, target_drug),
    FOREIGN KEY (sample_id, target_drug) REFERENCES samples(sample_id, target_drug)
);

"""
    return schema[:start_index] + legacy_predictions_sql + schema[end_index:]


def _legacy_schema_with_sample_scoped_novelty() -> str:
    schema = (REPO_ROOT / "backend/app/storage/sqlite_schema.sql").read_text(encoding="utf-8")
    start_index = schema.index("CREATE TABLE IF NOT EXISTS novelty (")
    end_index = schema.index("CREATE TABLE IF NOT EXISTS actionability (")
    legacy_novelty_sql = """CREATE TABLE IF NOT EXISTS novelty (
    job_id TEXT,
    sample_id TEXT NOT NULL,
    target_drug TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    reference_snapshot_id TEXT NOT NULL,
    nearest_neighbor_id TEXT,
    nearest_neighbor_distance REAL,
    novelty_score REAL,
    novelty_percentile REAL,
    novelty_bucket TEXT NOT NULL,
    missing_reference INTEGER NOT NULL DEFAULT 0,
    uncertainty_flag INTEGER NOT NULL DEFAULT 0,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    PRIMARY KEY (sample_id, target_drug),
    FOREIGN KEY (sample_id, target_drug) REFERENCES samples(sample_id, target_drug)
);

"""
    return schema[:start_index] + legacy_novelty_sql + schema[end_index:]


def _sample_input_json(target_drug: str = "tetracycline") -> str:
    return json.dumps(
        {
            "sample_id": "sample_001",
            "schema_version": "0.1.0",
            "organism_hint": "e_coli",
            "target_drug": target_drug,
            "fasta_path": "data/fixtures/smoke/sample_001.fasta",
            "fasta_uri": None,
            "metadata": {
                "accession": None,
                "collection_date": None,
                "source": "fixture",
                "country": None,
                "provenance_source": "fixture",
            },
            "created_at": "2026-04-20T00:00:00Z",
        }
    )


def _insert_sample(connection: sqlite3.Connection, target_drug: str = "tetracycline") -> None:
    connection.execute(
        """
        INSERT INTO samples (
            sample_id,
            schema_version,
            organism_hint,
            target_drug,
            source_context,
            provenance_source,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sample_001",
            "0.1.0",
            "e_coli",
            target_drug,
            "fixture",
            "fixture",
            "2026-04-20T00:00:00Z",
        ),
    )


def _insert_prediction(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    target_drug: str,
) -> None:
    connection.execute(
        """
        INSERT INTO predictions (
            job_id,
            sample_id,
            target_drug,
            schema_version,
            predicted_phenotype,
            probability,
            calibration_status,
            feature_set_version,
            model_version,
            split_context,
            input_source_context,
            input_provenance_source,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            "sample_001",
            target_drug,
            "0.1.0",
            "resistant",
            0.83,
            "not_available",
            "baseline_hybrid_v1",
            "baseline_tetracycline_smoke_v1",
            "fixture",
            "fixture",
            "fixture",
            "2026-04-20T00:00:00Z",
        ),
    )


def _insert_novelty(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    target_drug: str,
) -> None:
    connection.execute(
        """
        INSERT INTO novelty (
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
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            "sample_001",
            target_drug,
            "0.1.0",
            "phase3_foundation_2026_04_20",
            "ref_ec_001",
            0.034,
            0.34,
            34.0,
            "elevated",
            "2026-04-20T00:00:00Z",
        ),
    )


def _insert_actionability(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    target_drug: str,
) -> None:
    connection.execute(
        """
        INSERT INTO actionability (
            job_id,
            sample_id,
            target_drug,
            schema_version,
            actionability_score,
            qc_risk,
            novelty_risk,
            metadata_completeness,
            threshold_version,
            triage_decision,
            severity,
            recommended_next_step,
            rationale_codes_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            "sample_001",
            target_drug,
            "0.1.0",
            0.72,
            0.1,
            0.2,
            1.0,
            "threshold_v1",
            "review",
            "medium",
            "review structured evidence in the demo workflow",
            json.dumps(["manual_confirmation_required"]),
            "2026-04-20T00:00:00Z",
        ),
    )


def _insert_job(
    connection: sqlite3.Connection,
    *,
    job_id: str = "job_001",
    target_drug: str = "tetracycline",
) -> None:
    connection.execute(
        """
        INSERT INTO jobs (
            job_id,
            sample_id,
            target_drug,
            sample_input_json,
            schema_version,
            status,
            submitted_at,
            updated_at,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            "sample_001",
            target_drug,
            _sample_input_json(target_drug),
            "0.1.0",
            "decision_ready",
            "2026-04-20T00:00:00Z",
            "2026-04-20T00:00:00Z",
            "2026-04-20T00:00:00Z",
        ),
    )


def _insert_mechanistic_evidence(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    target_drug: str,
) -> None:
    connection.execute(
        """
        INSERT INTO mechanistic_evidence (
            job_id,
            sample_id,
            target_drug,
            schema_version,
            source_tool,
            gene_symbol,
            mechanism_class,
            support_level,
            interpretation,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            "sample_001",
            target_drug,
            "0.1.0",
            "amrfinderplus",
            "tetA",
            "efflux",
            "supported",
            "supporting signal present in normalized output",
            "2026-04-20T00:00:00Z",
        ),
    )


def _insert_assembly_qc(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    target_drug: str,
) -> None:
    connection.execute(
        """
        INSERT INTO assembly_qc (
            job_id,
            sample_id,
            target_drug,
            schema_version,
            file_valid,
            sequence_count,
            total_bases,
            ambiguous_base_fraction,
            organism_consistency,
            qc_status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            "sample_001",
            target_drug,
            "0.1.0",
            1,
            1,
            316,
            0.0,
            "match",
            "pass",
            "2026-04-20T00:00:00Z",
        ),
    )


def _insert_copilot_output(connection: sqlite3.Connection, target_drug: str) -> None:
    connection.execute(
        """
        INSERT INTO copilot_outputs (
            job_id,
            sample_id,
            target_drug,
            schema_version,
            summary,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "job_001",
            "sample_001",
            target_drug,
            "0.1.0",
            "grounded response summary",
            "2026-04-20T00:00:00Z",
        ),
    )


def _insert_artifact(connection: sqlite3.Connection, target_drug: str) -> None:
    connection.execute(
        """
        INSERT INTO artifacts (
            artifact_id,
            job_id,
            sample_id,
            target_drug,
            schema_version,
            kind,
            path,
            media_type,
            generated_by,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"artifact_{target_drug}",
            "job_001",
            "sample_001",
            target_drug,
            "0.1.0",
            "decision_object",
            "artifacts/demo/decision.json",
            "application/json",
            "test_runner",
            "2026-04-20T00:00:00Z",
        ),
    )


def _insert_queue_item(connection: sqlite3.Connection, target_drug: str) -> None:
    connection.execute(
        """
        INSERT INTO queue_items (
            job_id,
            sample_id,
            target_drug,
            schema_version,
            triage,
            severity,
            status,
            queue_priority,
            headline,
            updated_at,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "job_001",
            "sample_001",
            target_drug,
            "0.1.0",
            "review",
            "medium",
            "decision_ready",
            10,
            "case awaiting analyst review",
            "2026-04-20T00:00:00Z",
            "2026-04-20T00:00:00Z",
        ),
    )


def test_samples_are_keyed_by_sample_and_target_drug() -> None:
    connection = _open_schema_connection()
    _insert_sample(connection, "tetracycline")
    _insert_sample(connection, "ampicillin")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_sample(connection, "tetracycline")

    columns = {
        row[1]: row[5]
        for row in connection.execute("PRAGMA table_info(samples)").fetchall()
    }
    assert columns["sample_id"] == 1
    assert columns["target_drug"] == 2


def test_predictions_are_keyed_by_job_and_allow_reruns() -> None:
    connection = _open_schema_connection()
    _insert_sample(connection, "tetracycline")
    _insert_job(connection, job_id="job_001", target_drug="tetracycline")
    _insert_job(connection, job_id="job_002", target_drug="tetracycline")

    _insert_prediction(connection, job_id="job_001", target_drug="tetracycline")
    _insert_prediction(connection, job_id="job_002", target_drug="tetracycline")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_prediction(connection, job_id="job_001", target_drug="tetracycline")

    columns = {
        row[1]: row[5]
        for row in connection.execute("PRAGMA table_info(predictions)").fetchall()
    }
    assert columns["job_id"] == 1
    assert columns["sample_id"] == 0
    assert columns["target_drug"] == 0
    assert "input_source_context" in columns
    assert "input_provenance_source" in columns

    with pytest.raises(sqlite3.IntegrityError):
        _insert_prediction(connection, job_id="job_003", target_drug="ciprofloxacin")


def test_jobs_persist_sample_input_snapshot_column() -> None:
    connection = _open_schema_connection()
    columns = {
        row[1]: row[2]
        for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
    }

    assert "sample_input_json" in columns
    assert columns["sample_input_json"].upper() == "TEXT"


def test_sqlite_persistence_migrates_legacy_jobs_table_to_add_sample_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_phase6.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_legacy_schema_without_sample_input_json())
    connection.execute(
        """
        INSERT INTO samples (
            sample_id,
            schema_version,
            organism_hint,
            target_drug,
            source_context,
            provenance_source,
            fasta_path,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sample_001",
            "0.1.0",
            "e_coli",
            "tetracycline",
            "fixture",
            "fixture",
            "data/fixtures/smoke/sample_001.fasta",
            "2026-04-20T00:00:00Z",
        ),
    )
    connection.execute(
        """
        INSERT INTO jobs (
            job_id,
            sample_id,
            target_drug,
            schema_version,
            status,
            submitted_at,
            updated_at,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "job_legacy_001",
            "sample_001",
            "tetracycline",
            "0.1.0",
            "decision_ready",
            "2026-04-20T00:00:00Z",
            "2026-04-20T00:00:00Z",
            "2026-04-20T00:00:00Z",
        ),
    )
    connection.commit()
    connection.close()

    persistence = SQLitePersistence(db_path, repo_root=REPO_ROOT)
    with persistence.connect() as migrated_connection:
        migrated_row = migrated_connection.execute(
            "SELECT sample_input_json FROM jobs WHERE job_id = ?",
            ("job_legacy_001",),
        ).fetchone()

    assert migrated_row is not None
    assert migrated_row["sample_input_json"]

    sample = SampleInput.model_validate(
        {
            "sample_id": "sample_001",
            "organism_hint": "e_coli",
            "target_drug": "tetracycline",
            "fasta_path": "data/fixtures/smoke/sample_001.fasta",
            "metadata": {
                "source": "fixture",
                "provenance_source": "fixture",
            },
        }
    )
    persistence.upsert_sample(sample)
    persistence.create_job(
        JobStatus(
            job_id="job_new_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            status="queued",
        ),
        sample=sample,
    )


def test_sqlite_persistence_rejects_ambiguous_legacy_sample_snapshot_backfill(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_phase6_reruns.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_legacy_schema_without_sample_input_json())
    connection.execute(
        """
        INSERT INTO samples (
            sample_id,
            schema_version,
            organism_hint,
            target_drug,
            source_context,
            provenance_source,
            fasta_path,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sample_001",
            "0.1.0",
            "e_coli",
            "tetracycline",
            "fixture",
            "fixture",
            "data/fixtures/smoke/sample_001.fasta",
            "2026-04-20T00:00:00Z",
        ),
    )
    connection.executemany(
        """
        INSERT INTO jobs (
            job_id,
            sample_id,
            target_drug,
            schema_version,
            status,
            submitted_at,
            updated_at,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                "job_legacy_001",
                "sample_001",
                "tetracycline",
                "0.1.0",
                "decision_ready",
                "2026-04-20T00:00:00Z",
                "2026-04-20T00:00:00Z",
                "2026-04-20T00:00:00Z",
            ),
            (
                "job_legacy_002",
                "sample_001",
                "tetracycline",
                "0.1.0",
                "decision_ready",
                "2026-04-21T00:00:00Z",
                "2026-04-21T00:00:00Z",
                "2026-04-21T00:00:00Z",
            ),
        ),
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="sample snapshots"):
        SQLitePersistence(db_path, repo_root=REPO_ROOT)


def test_sqlite_persistence_migrates_legacy_actionability_warning_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_actionability.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_legacy_schema_without_actionability_warning_columns())
    connection.execute(
        """
        INSERT INTO samples (
            sample_id,
            schema_version,
            organism_hint,
            target_drug,
            source_context,
            provenance_source,
            fasta_path,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sample_001",
            "0.1.0",
            "e_coli",
            "tetracycline",
            "fixture",
            "fixture",
            "data/fixtures/smoke/sample_001.fasta",
            "2026-04-20T00:00:00Z",
        ),
    )
    connection.execute(
        """
        INSERT INTO jobs (
            job_id,
            sample_id,
            target_drug,
            sample_input_json,
            schema_version,
            status,
            submitted_at,
            updated_at,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "job_001",
            "sample_001",
            "tetracycline",
            _sample_input_json(),
            "0.1.0",
            "decision_ready",
            "2026-04-20T00:00:00Z",
            "2026-04-20T00:00:00Z",
            "2026-04-20T00:00:00Z",
        ),
    )
    connection.commit()
    connection.close()

    persistence = SQLitePersistence(db_path, repo_root=REPO_ROOT)
    features = ActionabilityFeatures(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        actionability_score=0.8,
        mechanism_concordance=True,
        prediction_entropy=0.2,
        qc_risk=0.1,
        novelty_risk=0.2,
        metadata_completeness=1.0,
        threshold_version="threshold_v1",
        warnings=[],
    )
    triage = TriageDecision(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        triage="review",
        severity="medium",
        recommended_next_step="review evidence manually",
        threshold_version="threshold_v1",
        rationale_codes=["manual_confirmation_required"],
        warnings=[],
    )
    persistence.save_actionability(features=features, triage=triage, decision_warnings=[])

    with persistence.connect() as migrated_connection:
        row = migrated_connection.execute(
            """
            SELECT feature_warnings_json, triage_warnings_json
            FROM actionability
            WHERE job_id = ?
            """,
            ("job_001",),
        ).fetchone()

    assert row is not None
    assert row["feature_warnings_json"] == "[]"
    assert row["triage_warnings_json"] == "[]"


def test_sqlite_persistence_migrates_missing_job_context_parent_key(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_jobs_parent_key.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_legacy_schema_without_job_context_unique())
    _insert_sample(connection, "tetracycline")
    _insert_job(connection, job_id="job_001", target_drug="tetracycline")
    connection.commit()
    connection.close()

    persistence = SQLitePersistence(db_path, repo_root=REPO_ROOT)
    prediction = PhenotypePrediction(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        predicted_phenotype="resistant",
        probability=0.83,
        calibration_status="not_available",
        uncertainty_score=None,
        feature_set_version="baseline_hybrid_v1",
        model_version="baseline_tetracycline_smoke_v1",
        model_training_split_context="fixture",
        input_source_context="fixture",
        input_provenance_source="fixture",
        warnings=[],
    )
    persistence.save_prediction(prediction)

    with persistence.connect() as migrated_connection:
        row = migrated_connection.execute(
            "SELECT job_id FROM predictions WHERE job_id = ?",
            ("job_001",),
        ).fetchone()

    assert row is not None
    assert row["job_id"] == "job_001"


def test_sqlite_persistence_reconstructs_prediction_provenance_from_saved_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "prediction_provenance.sqlite"
    persistence = SQLitePersistence(db_path, repo_root=REPO_ROOT)

    with persistence.connect() as connection:
        _insert_sample(connection, "tetracycline")
        _insert_job(connection, job_id="job_001", target_drug="tetracycline")
        _insert_assembly_qc(connection, job_id="job_001", target_drug="tetracycline")
        _insert_mechanistic_evidence(connection, job_id="job_001", target_drug="tetracycline")
        _insert_prediction(connection, job_id="job_001", target_drug="tetracycline")
        _insert_novelty(connection, job_id="job_001", target_drug="tetracycline")
        _insert_actionability(connection, job_id="job_001", target_drug="tetracycline")
        connection.commit()

    decision = persistence.get_decision("job_001")

    assert decision is not None
    assert decision.phenotype_prediction.model_training_split_context.value == "fixture"
    assert decision.phenotype_prediction.input_source_context.value == "fixture"
    assert decision.phenotype_prediction.input_provenance_source.value == "fixture"
    assert "analysis_input_fixture_fasta_path" in decision.provenance_notes
    assert "prediction_model_training_split_context_fixture" in decision.provenance_notes
    assert "prediction_model_fixture_backed_baseline" in decision.provenance_notes


def test_sqlite_persistence_rebuilds_pre_job_scoped_prediction_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_prediction_scope.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_legacy_schema_with_pre_job_scoped_predictions())
    connection.execute(
        """
        INSERT INTO samples (
            sample_id,
            schema_version,
            organism_hint,
            target_drug,
            source_context,
            provenance_source,
            fasta_path,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sample_001",
            "0.1.0",
            "e_coli",
            "tetracycline",
            "fixture",
            "fixture",
            "data/fixtures/smoke/sample_001.fasta",
            "2026-04-20T00:00:00Z",
        ),
    )
    connection.execute(
        """
        INSERT INTO jobs (
            job_id,
            sample_id,
            target_drug,
            sample_input_json,
            schema_version,
            status,
            submitted_at,
            updated_at,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "job_001",
            "sample_001",
            "tetracycline",
            _sample_input_json(),
            "0.1.0",
            "decision_ready",
            "2026-04-20T00:00:00Z",
            "2026-04-20T00:00:00Z",
            "2026-04-20T00:00:00Z",
        ),
    )
    connection.execute(
        """
        INSERT INTO predictions (
            sample_id,
            target_drug,
            schema_version,
            predicted_phenotype,
            probability,
            calibration_status,
            feature_set_version,
            model_version,
            split_context,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sample_001",
            "tetracycline",
            "0.1.0",
            "resistant",
            0.83,
            "not_available",
            "baseline_hybrid_v1",
            "baseline_tetracycline_smoke_v1",
            "fixture",
            "2026-04-20T00:00:00Z",
        ),
    )
    connection.commit()
    connection.close()

    persistence = SQLitePersistence(db_path, repo_root=REPO_ROOT)

    with persistence.connect() as migrated_connection:
        backup_table = migrated_connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name LIKE 'predictions_legacy_pre_job_scope%'
            """
        ).fetchone()
        current_row = migrated_connection.execute(
            """
            SELECT job_id, sample_id, target_drug, predicted_phenotype
            FROM predictions
            WHERE job_id = ?
            """,
            ("job_001",),
        ).fetchone()

    assert backup_table is not None
    assert current_row is not None
    assert current_row["job_id"] == "job_001"
    assert current_row["sample_id"] == "sample_001"
    assert current_row["target_drug"] == "tetracycline"
    assert current_row["predicted_phenotype"] == "resistant"


def test_sqlite_persistence_rebuilds_sample_scoped_output_table_shapes(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_novelty_scope.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_legacy_schema_with_sample_scoped_novelty())
    _insert_sample(connection, "tetracycline")
    _insert_job(connection, job_id="job_001", target_drug="tetracycline")
    _insert_job(connection, job_id="job_002", target_drug="tetracycline")
    connection.execute(
        """
        INSERT INTO novelty (
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
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "job_001",
            "sample_001",
            "tetracycline",
            "0.1.0",
            "phase3_foundation_2026_04_20",
            "ref_ec_001",
            0.034,
            0.34,
            34.0,
            "elevated",
            "2026-04-20T00:00:00Z",
        ),
    )
    connection.commit()
    connection.close()

    persistence = SQLitePersistence(db_path, repo_root=REPO_ROOT)
    persistence.save_novelty(
        NoveltyAssessment(
            job_id="job_002",
            sample_id="sample_001",
            target_drug="tetracycline",
            reference_snapshot_id="phase3_foundation_2026_04_20",
            nearest_neighbor_id="ref_ec_001",
            nearest_neighbor_distance=0.034,
            novelty_score=0.34,
            novelty_percentile=34.0,
            novelty_bucket="elevated",
        )
    )

    with persistence.connect() as migrated_connection:
        backup_table = migrated_connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name LIKE 'novelty_legacy_pre_job_scope%'
            """
        ).fetchone()
        primary_key_columns = {
            row["name"]: row["pk"]
            for row in migrated_connection.execute("PRAGMA table_info(novelty)").fetchall()
        }
        migrated_job_001_row = migrated_connection.execute(
            "SELECT job_id FROM novelty WHERE job_id = ?",
            ("job_001",),
        ).fetchone()
        row_count = migrated_connection.execute("SELECT COUNT(*) AS count FROM novelty").fetchone()

    assert backup_table is not None
    assert primary_key_columns["job_id"] == 1
    assert primary_key_columns["sample_id"] == 0
    assert primary_key_columns["target_drug"] == 0
    assert migrated_job_001_row is not None
    assert row_count["count"] == 2


def test_sqlite_persistence_rejects_mismatched_actionability_context(tmp_path: Path) -> None:
    db_path = tmp_path / "actionability_context.sqlite"
    persistence = SQLitePersistence(db_path, repo_root=REPO_ROOT)
    sample = SampleInput.model_validate(
        {
            "sample_id": "sample_001",
            "organism_hint": "e_coli",
            "target_drug": "tetracycline",
            "fasta_path": "data/fixtures/smoke/sample_001.fasta",
            "metadata": {
                "source": "fixture",
                "provenance_source": "fixture",
            },
        }
    )
    persistence.upsert_sample(sample)
    persistence.create_job(
        JobStatus(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            status="queued",
        ),
        sample=sample,
    )

    features = ActionabilityFeatures(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        actionability_score=0.8,
        mechanism_concordance=True,
        prediction_entropy=0.2,
        qc_risk=0.1,
        novelty_risk=0.2,
        metadata_completeness=1.0,
        threshold_version="threshold_v1",
        warnings=[],
    )
    triage = TriageDecision(
        job_id="job_002",
        sample_id="sample_001",
        target_drug="tetracycline",
        triage="review",
        severity="medium",
        recommended_next_step="review evidence manually",
        threshold_version="threshold_v1",
        rationale_codes=["manual_confirmation_required"],
        warnings=[],
    )

    with pytest.raises(ValueError, match="Actionability triage context must match"):
        persistence.save_actionability(features=features, triage=triage, decision_warnings=[])


def test_novelty_is_keyed_by_job_and_allows_reruns() -> None:
    connection = _open_schema_connection()
    _insert_sample(connection, "tetracycline")
    _insert_job(connection, job_id="job_001", target_drug="tetracycline")
    _insert_job(connection, job_id="job_002", target_drug="tetracycline")

    _insert_novelty(connection, job_id="job_001", target_drug="tetracycline")
    _insert_novelty(connection, job_id="job_002", target_drug="tetracycline")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_novelty(connection, job_id="job_001", target_drug="tetracycline")

    columns = {
        row[1]: row[5]
        for row in connection.execute("PRAGMA table_info(novelty)").fetchall()
    }
    assert columns["job_id"] == 1
    assert columns["sample_id"] == 0
    assert columns["target_drug"] == 0

    with pytest.raises(sqlite3.IntegrityError):
        _insert_novelty(connection, job_id="job_003", target_drug="ciprofloxacin")


def test_actionability_is_keyed_by_job_and_allows_reruns() -> None:
    connection = _open_schema_connection()
    _insert_sample(connection, "tetracycline")
    _insert_job(connection, job_id="job_001", target_drug="tetracycline")
    _insert_job(connection, job_id="job_002", target_drug="tetracycline")

    _insert_actionability(connection, job_id="job_001", target_drug="tetracycline")
    _insert_actionability(connection, job_id="job_002", target_drug="tetracycline")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_actionability(connection, job_id="job_001", target_drug="tetracycline")

    columns = {
        row[1]: row[5]
        for row in connection.execute("PRAGMA table_info(actionability)").fetchall()
    }
    assert columns["job_id"] == 1
    assert columns["sample_id"] == 0
    assert columns["target_drug"] == 0

    with pytest.raises(sqlite3.IntegrityError):
        _insert_actionability(connection, job_id="job_003", target_drug="ciprofloxacin")


def test_mechanistic_evidence_is_scoped_to_job_context() -> None:
    connection = _open_schema_connection()
    _insert_sample(connection, "tetracycline")
    _insert_job(connection, job_id="job_001", target_drug="tetracycline")
    _insert_job(connection, job_id="job_002", target_drug="tetracycline")

    _insert_mechanistic_evidence(connection, job_id="job_001", target_drug="tetracycline")
    _insert_mechanistic_evidence(connection, job_id="job_002", target_drug="tetracycline")

    columns = {
        row[1]: row[2]
        for row in connection.execute("PRAGMA table_info(mechanistic_evidence)").fetchall()
    }
    assert columns["job_id"].upper() == "TEXT"

    with pytest.raises(sqlite3.IntegrityError):
        _insert_mechanistic_evidence(connection, job_id="job_003", target_drug="tetracycline")


def test_assembly_qc_is_keyed_by_job_and_bound_to_matching_job_context() -> None:
    connection = _open_schema_connection()
    _insert_sample(connection, "tetracycline")
    _insert_job(connection, job_id="job_001", target_drug="tetracycline")

    _insert_assembly_qc(connection, job_id="job_001", target_drug="tetracycline")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_assembly_qc(connection, job_id="job_001", target_drug="tetracycline")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_assembly_qc(connection, job_id="job_002", target_drug="tetracycline")


def test_job_owned_rows_are_bound_to_matching_sample_context() -> None:
    connection = _open_schema_connection()
    _insert_sample(connection, "tetracycline")
    _insert_sample(connection, "ampicillin")
    _insert_job(connection, job_id="job_001", target_drug="tetracycline")

    _insert_copilot_output(connection, "tetracycline")
    _insert_artifact(connection, "tetracycline")
    _insert_queue_item(connection, "tetracycline")

    for insert_child in (_insert_copilot_output, _insert_artifact, _insert_queue_item):
        with pytest.raises(sqlite3.IntegrityError):
            insert_child(connection, "ampicillin")


def test_copilot_outputs_persist_structured_answer_blocks() -> None:
    connection = _open_schema_connection()
    columns = {
        row[1]: row[2]
        for row in connection.execute("PRAGMA table_info(copilot_outputs)").fetchall()
    }

    assert "answer_blocks_json" in columns
    assert columns["answer_blocks_json"].upper() == "TEXT"


def test_actionability_persists_feature_and_triage_warning_columns() -> None:
    connection = _open_schema_connection()
    columns = {
        row[1]: row[2]
        for row in connection.execute("PRAGMA table_info(actionability)").fetchall()
    }

    assert "feature_warnings_json" in columns
    assert "triage_warnings_json" in columns
    assert columns["feature_warnings_json"].upper() == "TEXT"
    assert columns["triage_warnings_json"].upper() == "TEXT"
