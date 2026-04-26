# SafeSurveil-AIxBio

SafeSurveil-AIxBio is a defensive antimicrobial-resistance (AMR) surveillance prototype built for the Apart Research AIxBio Hackathon 2026. It turns a genome-derived case into an evidence-grounded triage workflow with visible provenance, local evidence artifacts, a runtime execution gate, deterministic reasoning trace, evidence graph, grounded AI sidecars, and a React operator dashboard.

This repository is a research and hackathon artifact. It is **not** a clinical diagnostic, treatment recommender, prescribing system, or validated AMR predictor.

## What It Demonstrates

- Live-public-data workflow for a narrow `E. coli` plus tetracycline surveillance case.
- Local-first evidence generation with FASTA validation, AMRFinderPlus normalization, Mash novelty/runtime checks, artifact manifests, and SHA-256 provenance.
- Persisted decision objects with phenotype risk, QC risk, novelty risk, actionability score, rationale codes, triage, and recommended next step.
- Grounded OpenRouter-style LLM copilot routes with citation, identity, numeric, queue-context, and refusal validation.
- Backend-owned Thesys C1 semantic rendering with a React fallback that preserves grounded decision content.
- V2 trust layer: `ALLOW` / `REVIEW` / `BLOCK` execution gate, deterministic biological reasoning trace, typed evidence graph, audit fingerprint, and V2 audit page.
- React frontend for readiness, analysis submission, analyst queue, case detail, fallback renderer, and evaluation/audit closure.

## Final Hackathon Proof

The final recorded V2 proof used frontend-created job `job_20260425201118267288` and sample `frontend_live_final_20260425201057`.

| Check | Result |
| --- | --- |
| Runtime mode | Live candidate, live evidence, live LLM |
| OpenRouter proof | `output_origin.mode=live_llm` |
| Thesys C1 proof | `status=rendered`, `fallback_required=false` |
| Execution gate | `allow`, 0 failed checks, 0 warning checks |
| Evidence graph | 54 nodes, 101 typed edges |
| Curl matrix | 26 passed, 0 warnings, 0 failures |

These are engineering verification results, not clinical validation results.

## Repository Layout

```text
backend/      FastAPI service, contracts, orchestration, evidence builders, LLM validation, V2 verifier/trace/graph/audit
frontend/     React + Vite operator dashboard and Thesys/React rendering boundary
data/         Public-safe fixtures, accessions, smoke data, and snapshot policy artifacts
scripts/      Reproducible smoke, live retrieval, OpenAPI, and V2 curl-matrix runners
tests/        Backend, contract, data, evidence, prediction, storage, and script regression tests
tools/        Local tool wrappers used by the demo environment
reports/      Hackathon report source/PDF, architecture figure, and literature audit
```

## Quick Start

Use Python 3.11+ and Node 20+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
cd frontend
npm install
cd ..
```

Copy `.env.example` to `.env` and configure only the integrations you intend to exercise. Do not commit `.env`.

Start the backend:

```powershell
uvicorn backend.app.main:app --host 127.0.0.1 --port 8001
```

Start the frontend:

```powershell
cd frontend
npm run dev -- --host 127.0.0.1 --port 4173
```

Open `http://127.0.0.1:4173/`.

## Test Commands

Deterministic backend regression:

```powershell
python -m pytest -q
```

V2 focused checks:

```powershell
python -m pytest testsackend	est_v2_verification_endpoint.py testsackend	est_v2_reasoning_trace_endpoint.py testsackend	est_v2_evidence_graph_endpoint.py testsackend	est_v2_audit_endpoint.py tests\scripts	est_v2_curl_matrix.py -q
```

Frontend build/typecheck:

```powershell
cd frontend
npm run typecheck
npm run build
```

Curl matrix against a running backend:

```powershell
python scriptsun_v2_curl_matrix.py --job-id <job_id> --provider-proof --include-frontend-proxy
```

`--provider-proof` intentionally triggers live OpenRouter and Thesys calls when configured.

## Safety and Dual-Use Boundary

SafeSurveil-AIxBio is designed for defensive analyst review. It does not provide autonomous treatment advice, organism engineering guidance, or operational deployment recommendations. The demo scope is intentionally narrow and discloses key limits: fixture-trained smoke baseline, small organism/drug scope, local tooling assumptions, non-prospective validation, and dependence on public database/tool coverage.

Generated language is treated as a sidecar. The persisted decision object, evidence IDs, artifact hashes, execution gate, and human analyst remain upstream of any copilot or renderer output.

## Report

The hackathon report PDF is included at:

```text
reports/latex/build/safesurveil_aixbio_report.pdf
```

The editable architecture diagram is included at:

```text
reports/figures/safesurveil_architecture.drawio
```

## License

This project is released under the MIT License. See [LICENSE](./LICENSE).
