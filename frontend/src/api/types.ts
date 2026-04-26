export type TriageOutcome = "act" | "review" | "defer_to_lab";
export type SeverityLevel = "low" | "medium" | "high" | "critical";
export type JobState =
  | "queued"
  | "running"
  | "evidence_ready"
  | "decision_ready"
  | "failed"
  | "degraded"
  | "completed";
export type CopilotOutputMode = "live_llm" | "mock" | "cached" | "fallback";
export type ThesysC1RenderStatus = "rendered" | "unavailable" | "error";
export type ArtifactKind =
  | "input_fasta"
  | "mechanistic_evidence"
  | "novelty_summary"
  | "prediction_summary"
  | "decision_object"
  | "copilot_output"
  | "semantic_ui"
  | "plot"
  | "screenshot"
  | "other";
export type BackendMode = "demo" | "persisted";
export type JobDataMode = "demo_seeded" | "persisted_jobs";
export type EvidenceMode = "fixture" | "live";
export type RuntimeAcceptanceMode = "live_candidate" | "mixed_non_live";

export type ContractBase = {
  schema_version?: string;
  created_at?: string;
};

export type RuntimeModeReport = {
  app_env: string;
  backend_mode: BackendMode;
  job_data_mode: JobDataMode;
  evidence_mode: EvidenceMode;
  llm_mode: "mock" | "live";
  acceptance_mode: RuntimeAcceptanceMode;
  live_mode_ready: boolean;
  live_mode_blockers: string[];
};

export type IntegrationHealthValue = string | number | boolean | null | string[];
export type IntegrationHealthEntry = Record<string, IntegrationHealthValue>;

export type AnalyzeJobRequest = {
  sample_id: string;
  organism_hint?: string | null;
  target_drug: string;
  fasta_path?: string | null;
  fasta_uri?: string | null;
  metadata?: {
    accession?: string | null;
    collection_date?: string | null;
    source?: string;
    country?: string | null;
    provenance_source?: string;
  };
};

export type AnalyzeJobResponse = {
  job_id: string;
  status: JobState;
};

export type HealthResponse = {
  status: string;
  runtime: RuntimeModeReport;
};

export type IntegrationHealthResponse = {
  status: string;
  mode: "fixture" | "live";
  runtime: RuntimeModeReport;
  settings: {
    dataset_root: string;
    log_level: string;
    http_timeout_seconds: number;
    http_retry_count: number;
  };
  external_apis: Record<string, IntegrationHealthEntry>;
  tools: Record<string, IntegrationHealthEntry>;
  secrets: {
    redacted: boolean;
    values_exposed: boolean;
  };
};

export type QueueItem = ContractBase & {
  job_id: string;
  sample_id: string;
  target_drug: string;
  triage: TriageOutcome;
  severity: SeverityLevel;
  status: JobState;
  queue_priority: number;
  headline: string;
  rationale_codes: string[];
  updated_at?: string;
};

export type QueueSummaryResponse = ContractBase & {
  items: QueueItem[];
};

export type JobStatus = ContractBase & {
  job_id: string;
  sample_id: string;
  target_drug: string;
  status: JobState;
  current_step?: string | null;
  failure_code?: string | null;
  warnings: string[];
  submitted_at?: string;
  updated_at?: string;
  completed_at?: string | null;
};

export type SampleInput = ContractBase & {
  sample_id: string;
  organism_hint?: string | null;
  target_drug: string;
  fasta_path?: string | null;
  fasta_uri?: string | null;
  metadata?: {
    accession?: string | null;
    collection_date?: string | null;
    source?: string;
    source_context?: string;
    country?: string | null;
    provenance_source?: string;
  };
};

export type AssemblyQC = ContractBase & {
  job_id: string;
  sample_id: string;
  target_drug: string;
  file_valid: boolean;
  sequence_count: number;
  total_bases: number;
  ambiguous_base_fraction: number;
  organism_consistency: string;
  missing_metadata_fields: string[];
  qc_status: string;
  warnings: string[];
};

export type MechanisticEvidence = ContractBase & {
  job_id: string;
  sample_id: string;
  target_drug: string;
  source_tool: string;
  gene_symbol?: string | null;
  mutation?: string | null;
  mechanism_class: string;
  drug_association: string[];
  support_level: string;
  interpretation: string;
  raw_row_index?: number | null;
  raw_artifact_id?: string | null;
};

export type PhenotypePrediction = ContractBase & {
  job_id: string;
  sample_id: string;
  target_drug: string;
  predicted_phenotype: string;
  probability: number;
  calibration_status: string;
  uncertainty_score?: number | null;
  feature_set_version: string;
  model_version: string;
  model_training_split_context: string;
  input_source_context: string;
  input_provenance_source: string;
  warnings: string[];
};

export type NoveltyAssessment = ContractBase & {
  job_id: string;
  sample_id: string;
  target_drug: string;
  reference_snapshot_id: string;
  nearest_neighbor_id?: string | null;
  nearest_neighbor_distance?: number | null;
  novelty_score?: number | null;
  novelty_percentile?: number | null;
  novelty_bucket: string;
  missing_reference: boolean;
  uncertainty_flag: boolean;
  warnings: string[];
};

export type ActionabilityFeatures = ContractBase & {
  job_id: string;
  sample_id: string;
  target_drug: string;
  actionability_score: number;
  mechanism_concordance?: boolean | null;
  prediction_entropy?: number | null;
  qc_risk: number;
  novelty_risk: number;
  metadata_completeness: number;
  threshold_version: string;
  warnings: string[];
};

export type TriageDecision = ContractBase & {
  job_id: string;
  sample_id: string;
  target_drug: string;
  triage: TriageOutcome;
  severity: SeverityLevel;
  recommended_next_step: string;
  threshold_version: string;
  rationale_codes: string[];
  warnings: string[];
};

export type DecisionObject = ContractBase & {
  job_id?: string | null;
  sample: SampleInput;
  assembly_qc: AssemblyQC;
  mechanistic_evidence: MechanisticEvidence[];
  phenotype_prediction: PhenotypePrediction;
  novelty_assessment: NoveltyAssessment;
  actionability_features: ActionabilityFeatures;
  triage_decision: TriageDecision;
  rationale_codes: string[];
  warnings: string[];
  artifact_manifest_id?: string | null;
  provenance_notes: string[];
};

export type JobDecisionResponse = ContractBase & {
  job_status: JobStatus;
  decision: DecisionObject;
};

export type ArtifactRecord = ContractBase & {
  artifact_id: string;
  job_id: string;
  sample_id: string;
  target_drug: string;
  kind: ArtifactKind;
  path: string;
  media_type: string;
  generated_by: string;
  sha256?: string | null;
  size_bytes?: number | null;
  preview_eligible: boolean;
};

export type ArtifactManifest = ContractBase & {
  job_id: string;
  sample_id: string;
  target_drug: string;
  artifact_root?: string | null;
  artifacts: ArtifactRecord[];
};

export type ArtifactPreviewResponse = ContractBase & {
  job_id: string;
  artifact_id: string;
  media_type: string;
  encoding: string;
  content: string;
  truncated: boolean;
  size_bytes?: number | null;
};

export type MetricDatum = ContractBase & {
  key: string;
  label: string;
  value: string | number | boolean;
  unit?: string | null;
};

export type DecisionCardBlock = ContractBase & {
  title: string;
  triage_decision: TriageOutcome;
  severity: SeverityLevel;
  summary: string;
  metrics: MetricDatum[];
};

export type EvidenceTableRow = ContractBase & {
  row_id: string;
  label: string;
  cells: Record<string, string | number | boolean | null>;
  evidence_id?: string | null;
};

export type EvidenceTableBlock = ContractBase & {
  title: string;
  columns: string[];
  rows: EvidenceTableRow[];
};

export type RiskChartPoint = ContractBase & {
  label: string;
  value: number;
  evidence_id?: string | null;
};

export type RiskChartBlock = ContractBase & {
  chart_id: string;
  title: string;
  chart_type: "bar" | "line" | "area" | "radial";
  points: RiskChartPoint[];
};

export type SafetyProfileAxis = ContractBase & {
  label: string;
  value: number;
};

export type SafetyProfileBlock = ContractBase & {
  title: string;
  axes: SafetyProfileAxis[];
};

export type QueueBlock = ContractBase & {
  title: string;
  items: QueueItem[];
};

export type SemanticUIObject = ContractBase & {
  decision_card?: DecisionCardBlock | null;
  evidence_table?: EvidenceTableBlock | null;
  risk_charts: RiskChartBlock[];
  safety_profile?: SafetyProfileBlock | null;
  queue_block?: QueueBlock | null;
  notes: string[];
};

export type CopilotOutputOrigin = ContractBase & {
  mode: CopilotOutputMode;
  provider?: string | null;
  detail?: string | null;
};

export type CopilotAnswerBlock = ContractBase & {
  block_id: string;
  block_type: "summary" | "bullets" | "next_steps" | "refusal";
  title?: string | null;
  content: string;
  cited_evidence_ids: string[];
};

export type CopilotResponse = ContractBase & {
  job_id: string;
  sample_id: string;
  target_drug: string;
  summary?: string | null;
  next_steps: string[];
  refusal_required: boolean;
  refusal_reason?: string | null;
  cited_evidence_ids: string[];
  answer_blocks: CopilotAnswerBlock[];
  semantic_ui?: SemanticUIObject | null;
  warnings: string[];
};

export type JobCopilotResponse = ContractBase & {
  job_status: JobStatus;
  output_origin: CopilotOutputOrigin;
  copilot: CopilotResponse;
};

export type JobSemanticUIResponse = ContractBase & {
  job_id: string;
  output_origin: CopilotOutputOrigin;
  semantic_ui: SemanticUIObject;
};

export type JobThesysC1Response = ContractBase & {
  job_id: string;
  status: ThesysC1RenderStatus;
  output_origin: CopilotOutputOrigin;
  semantic_ui: SemanticUIObject;
  c1_response?: string | null;
  model?: string | null;
  reason?: string | null;
  fallback_required: boolean;
};

export type ExecutionGateDecision = "allow" | "review" | "block";
export type ExecutionGateCheckStatus = "pass" | "warn" | "fail";
export type ExecutionGateCheckCategory =
  | "identity"
  | "evidence_coverage"
  | "numeric_consistency"
  | "citation_validity"
  | "policy_alignment"
  | "reasoning_trace"
  | "artifact_coverage"
  | "safety_boundary";
export type ExecutionGateIssueSeverity = "info" | "warning" | "blocking";

export type ReasoningTraceStepType =
  | "sample_context"
  | "phenotype_prediction"
  | "mechanistic_evidence"
  | "mechanism_drug_interpretation"
  | "novelty_lineage_shift"
  | "qc_metadata_limitations"
  | "actionability_policy"
  | "final_triage";
export type ReasoningTraceStepStatus = "grounded" | "caveated";
export type ReasoningTraceCaveatSeverity = "info" | "warning" | "limitation";

export type ReasoningTraceSourceRef = ContractBase & {
  evidence_id: string;
  source_type: string;
  label?: string | null;
  detail?: string | null;
};

export type ReasoningTraceCaveat = ContractBase & {
  caveat_id: string;
  severity: ReasoningTraceCaveatSeverity;
  title: string;
  detail: string;
  evidence_refs: string[];
};

export type ReasoningTraceStep = ContractBase & {
  step_number: number;
  step_type: ReasoningTraceStepType;
  title: string;
  text: string;
  status: ReasoningTraceStepStatus;
  evidence_refs: ReasoningTraceSourceRef[];
  caveat_ids: string[];
};

export type ReasoningTraceCoverage = ContractBase & {
  required_step_types: ReasoningTraceStepType[];
  present_step_types: ReasoningTraceStepType[];
  missing_step_types: ReasoningTraceStepType[];
  required_steps: number;
  present_steps: number;
  coverage_ratio: number;
};

export type ReasoningTrace = ContractBase & {
  job_id: string;
  sample_id: string;
  target_drug: string;
  decision: TriageOutcome;
  severity: SeverityLevel;
  summary: string;
  steps: ReasoningTraceStep[];
  coverage: ReasoningTraceCoverage;
  caveats: ReasoningTraceCaveat[];
  metadata: Record<string, unknown>;
};

export type EvidenceGraphNodeClass =
  | "sample"
  | "organism"
  | "drug"
  | "gene"
  | "mechanism"
  | "phenotype_prediction"
  | "novelty"
  | "quality_control"
  | "actionability"
  | "rationale"
  | "decision"
  | "artifact"
  | "citation"
  | "copilot"
  | "execution_gate"
  | "reasoning_trace"
  | "policy"
  | "warning";

export type EvidenceGraphEdgeClass =
  | "has_context"
  | "targets"
  | "predicts"
  | "detects"
  | "associated_with"
  | "supports"
  | "informs"
  | "constrains"
  | "triages_as"
  | "cites"
  | "generated_artifact"
  | "verified_by"
  | "explains"
  | "caveats"
  | "linked_to";

export type EvidenceGraphClusterClass =
  | "case_context"
  | "mechanistic_evidence"
  | "risk_signals"
  | "policy_and_triage"
  | "ai_sidecars"
  | "audit";

export type EvidenceGraphStyleTone =
  | "neutral"
  | "sample"
  | "evidence"
  | "risk"
  | "policy"
  | "ai"
  | "gate"
  | "decision"
  | "caveat";

export type EvidenceGraphDetailField = ContractBase & {
  key: string;
  label: string;
  value?: string | number | boolean | null;
  value_kind: string;
};

export type EvidenceGraphStyleHint = ContractBase & {
  tone: EvidenceGraphStyleTone;
  color_token?: string | null;
  icon?: string | null;
  importance: number;
};

export type EvidenceGraphNode = ContractBase & {
  node_id: string;
  node_class: EvidenceGraphNodeClass;
  label: string;
  summary: string;
  details: EvidenceGraphDetailField[];
  evidence_refs: string[];
  artifact_refs: string[];
  style: EvidenceGraphStyleHint;
};

export type EvidenceGraphEdge = ContractBase & {
  edge_id: string;
  edge_class: EvidenceGraphEdgeClass;
  source: string;
  target: string;
  label: string;
  summary?: string | null;
  evidence_refs: string[];
  weight: number;
  style: EvidenceGraphStyleHint;
};

export type EvidenceGraphCluster = ContractBase & {
  cluster_id: string;
  cluster_class: EvidenceGraphClusterClass;
  label: string;
  summary: string;
  node_ids: string[];
  style: EvidenceGraphStyleHint;
};

export type EvidenceGraphStats = ContractBase & {
  node_count: number;
  edge_count: number;
  cluster_count: number;
  evidence_nodes: number;
  citation_nodes: number;
  artifact_nodes: number;
  linked_artifact_nodes: number;
  artifact_linkage_ratio: number;
  linked_citation_nodes: number;
  citation_linkage_ratio: number;
  warning_nodes: number;
  connected_component_count: number;
  weakly_connected: boolean;
  isolated_node_count: number;
  isolated_node_ids: string[];
  required_node_classes: EvidenceGraphNodeClass[];
  present_node_classes: EvidenceGraphNodeClass[];
  missing_node_classes: EvidenceGraphNodeClass[];
  completeness_ratio: number;
};

export type EvidenceGraph = ContractBase & {
  job_id: string;
  sample_id: string;
  target_drug: string;
  nodes: EvidenceGraphNode[];
  edges: EvidenceGraphEdge[];
  clusters: EvidenceGraphCluster[];
  stats: EvidenceGraphStats;
  metadata: Record<string, unknown>;
};

export type ExecutionGateCheck = ContractBase & {
  check_id: string;
  category: ExecutionGateCheckCategory;
  status: ExecutionGateCheckStatus;
  title: string;
  detail: string;
  evidence_refs: string[];
  observed_value?: string | number | boolean | null;
  expected_value?: string | number | boolean | null;
};

export type EvidenceCoverageSummary = ContractBase & {
  required_evidence_ids: string[];
  covered_evidence_ids: string[];
  missing_evidence_ids: string[];
  coverage_ratio: number;
};

export type NumericConsistencySummary = ContractBase & {
  checked_fields: string[];
  matched_fields: string[];
  mismatched_fields: string[];
  tolerance: number;
  consistency_ratio: number;
};

export type CitationValiditySummary = ContractBase & {
  allowed_evidence_ids: string[];
  cited_evidence_ids: string[];
  invalid_evidence_ids: string[];
  missing_required_evidence_ids: string[];
  validity_ratio: number;
};

export type PolicyAlignmentSummary = ContractBase & {
  policy_version: string;
  triage_matches_decision: boolean;
  severity_matches_decision: boolean;
  next_step_matches_decision: boolean;
  unsafe_claims_detected: boolean;
  notes: string[];
};

export type ExecutionGateIssue = ContractBase & {
  issue_id: string;
  category: ExecutionGateCheckCategory;
  severity: ExecutionGateIssueSeverity;
  title: string;
  detail: string;
  evidence_refs: string[];
};

export type ExecutionGateReport = ContractBase & {
  job_id: string;
  sample_id: string;
  decision: TriageOutcome;
  severity: SeverityLevel;
  gate_decision: ExecutionGateDecision;
  summary: string;
  checks: ExecutionGateCheck[];
  evidence_coverage: EvidenceCoverageSummary;
  numeric_consistency: NumericConsistencySummary;
  citation_validity: CitationValiditySummary;
  policy_alignment: PolicyAlignmentSummary;
  issues: ExecutionGateIssue[];
  policy_hash: string;
  audit_fingerprint: string;
  metadata: Record<string, unknown>;
};

export type V2AuditStatus = "pass" | "warn" | "fail" | "pending";
export type V2AuditSectionId =
  | "runtime_mode"
  | "live_input_proof"
  | "predictive_baseline"
  | "openrouter_proof"
  | "thesys_proof"
  | "execution_gate"
  | "reasoning_trace"
  | "evidence_graph"
  | "artifact_coverage";
export type V2AuditBaselineProvenance =
  | "fixture_trained_smoke"
  | "public_snapshot"
  | "live_trained"
  | "unknown";

export type V2AuditProvenance = ContractBase & {
  job_id: string;
  sample_id: string;
  target_drug: string;
  input_provenance: string;
  source_context: string;
  split_context: string;
  baseline_provenance: V2AuditBaselineProvenance;
  live_input: boolean;
  fixture_trained_baseline: boolean;
  provenance_split_label: string;
  detail: string;
};

export type V2AuditCheck = ContractBase & {
  check_id: string;
  section_id: V2AuditSectionId;
  status: V2AuditStatus;
  title: string;
  detail: string;
  evidence_refs: string[];
  endpoint?: string | null;
  observed_value?: string | number | boolean | null;
  expected_value?: string | number | boolean | null;
  blocking: boolean;
};

export type V2AuditSection = ContractBase & {
  section_id: V2AuditSectionId;
  status: V2AuditStatus;
  title: string;
  summary: string;
  checks: V2AuditCheck[];
  evidence_refs: string[];
};

export type V2AuditSummary = ContractBase & {
  overall_status: V2AuditStatus;
  section_count: number;
  total_checks: number;
  passing_checks: number;
  warning_checks: number;
  failed_checks: number;
  pending_checks: number;
  live_ready: boolean;
  provider_proof_required: boolean;
};

export type V2AuditBundle = ContractBase & {
  job_id: string;
  sample_id: string;
  target_drug: string;
  provenance: V2AuditProvenance;
  summary: V2AuditSummary;
  sections: V2AuditSection[];
  metadata: Record<string, unknown>;
};

export type CaseBundle = {
  status: JobStatus;
  decision: JobDecisionResponse;
  artifacts: ArtifactManifest;
  semanticUi: JobSemanticUIResponse;
  explanation: JobCopilotResponse;
  queueSummary: JobCopilotResponse;
  verification: ExecutionGateReport | null;
  reasoningTrace: ReasoningTrace | null;
  evidenceGraph: EvidenceGraph | null;
  verificationError?: string;
  reasoningTraceError?: string;
  evidenceGraphError?: string;
};
