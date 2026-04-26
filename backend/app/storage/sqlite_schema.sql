CREATE TABLE IF NOT EXISTS samples (
    sample_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    organism_hint TEXT,
    target_drug TEXT NOT NULL,
    accession TEXT,
    collection_date TEXT,
    source_context TEXT NOT NULL,
    country TEXT,
    provenance_source TEXT NOT NULL,
    fasta_path TEXT,
    fasta_uri TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (sample_id, target_drug)
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    target_drug TEXT NOT NULL,
    sample_input_json TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step TEXT,
    failure_code TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    submitted_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (job_id, sample_id, target_drug),
    FOREIGN KEY (sample_id, target_drug) REFERENCES samples(sample_id, target_drug)
);

CREATE TABLE IF NOT EXISTS mechanistic_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    target_drug TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    source_tool TEXT NOT NULL,
    gene_symbol TEXT,
    mutation TEXT,
    mechanism_class TEXT NOT NULL,
    drug_association_json TEXT NOT NULL DEFAULT '[]',
    support_level TEXT NOT NULL,
    interpretation TEXT NOT NULL,
    raw_row_index INTEGER,
    raw_artifact_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id, sample_id, target_drug) REFERENCES jobs(job_id, sample_id, target_drug),
    FOREIGN KEY (sample_id, target_drug) REFERENCES samples(sample_id, target_drug)
);

CREATE TABLE IF NOT EXISTS assembly_qc (
    job_id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    target_drug TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    file_valid INTEGER NOT NULL,
    sequence_count INTEGER NOT NULL,
    total_bases INTEGER NOT NULL,
    ambiguous_base_fraction REAL NOT NULL,
    organism_consistency TEXT NOT NULL,
    missing_metadata_fields_json TEXT NOT NULL DEFAULT '[]',
    qc_status TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id, sample_id, target_drug) REFERENCES jobs(job_id, sample_id, target_drug),
    FOREIGN KEY (sample_id, target_drug) REFERENCES samples(sample_id, target_drug)
);

CREATE TABLE IF NOT EXISTS predictions (
    job_id TEXT PRIMARY KEY,
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
    input_source_context TEXT,
    input_provenance_source TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id, sample_id, target_drug) REFERENCES jobs(job_id, sample_id, target_drug),
    FOREIGN KEY (sample_id, target_drug) REFERENCES samples(sample_id, target_drug)
);

CREATE TABLE IF NOT EXISTS novelty (
    job_id TEXT PRIMARY KEY,
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
    FOREIGN KEY (job_id, sample_id, target_drug) REFERENCES jobs(job_id, sample_id, target_drug),
    FOREIGN KEY (sample_id, target_drug) REFERENCES samples(sample_id, target_drug)
);

CREATE TABLE IF NOT EXISTS actionability (
    job_id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    target_drug TEXT NOT NULL,
    actionability_score REAL NOT NULL,
    mechanism_concordance INTEGER,
    prediction_entropy REAL,
    qc_risk REAL NOT NULL,
    novelty_risk REAL NOT NULL,
    metadata_completeness REAL NOT NULL,
    threshold_version TEXT NOT NULL,
    triage_decision TEXT NOT NULL,
    severity TEXT NOT NULL,
    recommended_next_step TEXT NOT NULL,
    rationale_codes_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    feature_warnings_json TEXT NOT NULL DEFAULT '[]',
    triage_warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id, sample_id, target_drug) REFERENCES jobs(job_id, sample_id, target_drug),
    FOREIGN KEY (sample_id, target_drug) REFERENCES samples(sample_id, target_drug)
);

CREATE TABLE IF NOT EXISTS copilot_outputs (
    job_id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    target_drug TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    refusal_required INTEGER NOT NULL DEFAULT 0,
    refusal_reason TEXT,
    summary TEXT,
    next_steps_json TEXT NOT NULL DEFAULT '[]',
    cited_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    answer_blocks_json TEXT NOT NULL DEFAULT '[]',
    semantic_ui_json TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id, sample_id, target_drug) REFERENCES jobs(job_id, sample_id, target_drug),
    FOREIGN KEY (sample_id, target_drug) REFERENCES samples(sample_id, target_drug)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    target_drug TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    generated_by TEXT NOT NULL,
    sha256 TEXT,
    size_bytes INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id, sample_id, target_drug) REFERENCES jobs(job_id, sample_id, target_drug),
    FOREIGN KEY (sample_id, target_drug) REFERENCES samples(sample_id, target_drug)
);

CREATE TABLE IF NOT EXISTS queue_items (
    job_id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    target_drug TEXT NOT NULL,
    triage TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    queue_priority INTEGER NOT NULL,
    headline TEXT NOT NULL,
    rationale_codes_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id, sample_id, target_drug) REFERENCES jobs(job_id, sample_id, target_drug),
    FOREIGN KEY (sample_id, target_drug) REFERENCES samples(sample_id, target_drug)
);

CREATE INDEX IF NOT EXISTS idx_jobs_sample_target ON jobs (sample_id, target_drug);
CREATE INDEX IF NOT EXISTS idx_mechanistic_evidence_job_sample_target ON mechanistic_evidence (job_id, sample_id, target_drug);
CREATE INDEX IF NOT EXISTS idx_assembly_qc_sample_target ON assembly_qc (sample_id, target_drug);
CREATE INDEX IF NOT EXISTS idx_predictions_sample_target ON predictions (sample_id, target_drug);
CREATE INDEX IF NOT EXISTS idx_novelty_sample_target ON novelty (sample_id, target_drug);
CREATE INDEX IF NOT EXISTS idx_actionability_sample_target ON actionability (sample_id, target_drug);
CREATE INDEX IF NOT EXISTS idx_queue_items_priority ON queue_items (queue_priority, updated_at DESC);
