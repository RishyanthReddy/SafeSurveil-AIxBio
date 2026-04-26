from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.data import prepare_live_sample_input  # noqa: E402
from app.settings import load_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a live SampleInput from a generated live accession manifest row."
    )
    parser.add_argument(
        "--manifest",
        default="data/downloads/live_accession_manifest.csv",
        help="Generated manifest CSV produced by the live retrieval workflow.",
    )
    parser.add_argument(
        "--record-id",
        required=True,
        help="Manifest record_id to convert into a live SampleInput.",
    )
    parser.add_argument(
        "--output-json",
        default="artifacts/runs/live_sample_input.json",
        help="Where to write the generated SampleInput JSON.",
    )
    args = parser.parse_args()

    preparation = prepare_live_sample_input(
        settings=load_settings(),
        manifest_path=REPO_ROOT / args.manifest,
        record_id=args.record_id,
        output_json_path=REPO_ROOT / args.output_json,
    )
    print(
        json.dumps(
            {
                "record_id": preparation.record_id,
                "sample_id": preparation.sample.sample_id,
                "target_drug": preparation.sample.target_drug,
                "assembly_accession": preparation.assembly_accession,
                "biosample_accession": preparation.biosample_accession,
                "sample_json_path": str(REPO_ROOT / args.output_json),
                "fasta_path": preparation.sample.fasta_path,
                "package_path": str(preparation.genome_package_path),
                "package_sha256": preparation.genome_package_sha256,
                "extracted_fasta_sha256": preparation.extracted_fasta_sha256,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
