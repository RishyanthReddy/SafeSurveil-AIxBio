"""Evidence-layer helpers for validation, tool wrappers, and normalization."""

from .amrfinder import (
    AMRFinderRuntimeInfo,
    AMRFinderExecutionPlan,
    build_amrfinderplus_runtime_metadata,
    build_amrfinderplus_command,
    execute_amrfinderplus,
    inspect_amrfinderplus_runtime,
    normalize_amrfinderplus_output,
    plan_amrfinderplus_execution,
    write_amrfinderplus_runtime_metadata,
)
from .failures import (
    EvidenceFailure,
    EvidenceFailureCode,
    build_fixture_fallback_failure,
    build_parse_failure,
    build_tool_missing_failure,
    classify_validation_failure,
)
from .manifest import build_evidence_artifact_manifest
from .concordance import (
    MechanismConcordanceClassification,
    MechanismConcordanceResult,
    assess_mechanism_concordance,
)
from .mash import (
    MashRuntimeInfo,
    MashQueryPlan,
    MashReferencePlan,
    build_mash_runtime_metadata,
    build_mash_dist_command,
    build_mash_sketch_command,
    execute_mash_query_workflow,
    execute_mash_reference_workflow,
    inspect_mash_runtime,
    parse_mash_distance_output,
    plan_mash_query_workflow,
    plan_mash_reference_workflow,
    write_mash_runtime_metadata,
)
from .smoke import EvidenceSmokeResult, run_evidence_smoke
from .validation import (
    ALLOWED_FASTA_SUFFIXES,
    DEFAULT_MAX_FASTA_BYTES,
    validate_sample_for_evidence,
)

__all__ = [
    "ALLOWED_FASTA_SUFFIXES",
    "AMRFinderExecutionPlan",
    "AMRFinderRuntimeInfo",
    "DEFAULT_MAX_FASTA_BYTES",
    "EvidenceFailure",
    "EvidenceFailureCode",
    "EvidenceSmokeResult",
    "MashReferencePlan",
    "MashQueryPlan",
    "MashRuntimeInfo",
    "MechanismConcordanceClassification",
    "MechanismConcordanceResult",
    "assess_mechanism_concordance",
    "build_evidence_artifact_manifest",
    "build_fixture_fallback_failure",
    "build_mash_dist_command",
    "build_mash_runtime_metadata",
    "build_parse_failure",
    "build_tool_missing_failure",
    "build_amrfinderplus_command",
    "build_amrfinderplus_runtime_metadata",
    "build_mash_sketch_command",
    "classify_validation_failure",
    "execute_amrfinderplus",
    "execute_mash_query_workflow",
    "execute_mash_reference_workflow",
    "inspect_amrfinderplus_runtime",
    "normalize_amrfinderplus_output",
    "parse_mash_distance_output",
    "plan_amrfinderplus_execution",
    "plan_mash_query_workflow",
    "plan_mash_reference_workflow",
    "run_evidence_smoke",
    "validate_sample_for_evidence",
    "write_amrfinderplus_runtime_metadata",
    "inspect_mash_runtime",
    "write_mash_runtime_metadata",
]
