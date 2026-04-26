from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.data import run_live_manifest_retrieval  # noqa: E402
from app.settings import load_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve planned public manifest rows using live BV-BRC and NCBI services.",
    )
    parser.add_argument(
        "--seed-manifest",
        type=Path,
        default=REPO_ROOT / "data/accessions/seed_accession_manifest.csv",
    )
    parser.add_argument(
        "--output-manifest",
        type=Path,
        default=REPO_ROOT / "data/downloads/live_accession_manifest.csv",
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=REPO_ROOT / "artifacts/runs/live_retrieval_summary.json",
    )
    parser.add_argument(
        "--record-id",
        action="append",
        dest="record_ids",
        help="Resolve only the specified record_id. Can be passed multiple times.",
    )
    args = parser.parse_args()

    summary = run_live_manifest_retrieval(
        settings=load_settings(),
        seed_manifest_path=args.seed_manifest,
        output_manifest_path=args.output_manifest,
        summary_output_path=args.summary_json,
        record_ids=set(args.record_ids or []),
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
