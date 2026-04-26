from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import build_phase6b_acceptance_report  # noqa: E402
from app.settings import load_settings  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Phase 6B live acceptance gate and write a JSON report."
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=None,
        help="Directory for temporary live retrieval, FASTA, and smoke outputs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path for the generated acceptance report JSON.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    work_root = args.work_root or (
        settings.artifact_root / "runs" / "phase6b_acceptance" / timestamp
    )
    output_path = args.output or (work_root / "phase6b_acceptance_report.json")

    report = build_phase6b_acceptance_report(
        settings=settings,
        work_root=work_root,
        output_path=output_path,
    )
    print(json.dumps(report["phase7_gate"], indent=2))
    print(output_path)


if __name__ == "__main__":
    main()
