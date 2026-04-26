from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.contracts import SampleInput  # noqa: E402
from app.evidence import run_evidence_smoke  # noqa: E402
from app.settings import load_settings  # noqa: E402


def load_smoke_sample() -> SampleInput:
    payload = json.loads((REPO_ROOT / "data/fixtures/smoke/sample_001.metadata.json").read_text(encoding="utf-8"))
    return SampleInput(
        sample_id=payload["sample_id"],
        organism_hint=payload["organism_hint"],
        target_drug=payload["target_drug"],
        fasta_path=payload["fasta_path"],
        metadata=payload["metadata"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 4 evidence smoke workflow.")
    parser.add_argument(
        "--output-dir",
        default="artifacts/demo/evidence_smoke",
        help="Directory where smoke outputs should be written.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Attempt live execution instead of explicit fixture mode.",
    )
    args = parser.parse_args()

    result = run_evidence_smoke(
        load_smoke_sample(),
        output_dir=REPO_ROOT / args.output_dir,
        fixture_mode=not args.live,
        repo_root=REPO_ROOT,
        settings=load_settings(),
    )
    print(
        json.dumps(
            {
                "sample_id": result.sample_id,
                "job_id": result.job_id,
                "mode": result.mode,
                "qc_path": str(result.qc_path),
                "mechanistic_json_path": str(result.mechanistic_json_path),
                "novelty_json_path": str(result.novelty_json_path),
                "manifest_path": str(result.manifest_path),
                "failure_codes": [failure.code.value for failure in result.failures],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
