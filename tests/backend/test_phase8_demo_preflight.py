from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.dependencies import get_persistence, get_settings
from app.main import create_app
from app.settings import AppSettings
from app.storage import SQLitePersistence


REPO_ROOT = Path(__file__).resolve().parents[2]


def _build_demo_client(tmp_path: Path) -> TestClient:
    settings = AppSettings(
        app_env="test",
        repo_root=REPO_ROOT,
        artifact_root=tmp_path / "artifacts",
        sqlite_db_path=tmp_path / "phase8.sqlite",
        use_fixtures=True,
        demo_mode=True,
    )
    persistence = SQLitePersistence(settings.sqlite_db_path, repo_root=settings.repo_root)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_persistence] = lambda: persistence
    return TestClient(app)


def test_phase8_demo_queue_rows_resolve_to_case_endpoints(tmp_path: Path) -> None:
    client = _build_demo_client(tmp_path)
    try:
        queue_response = client.get("/queue")
        assert queue_response.status_code == 200
        queue_items = queue_response.json()["items"]

        assert [item["triage"] for item in queue_items] == ["act", "review", "defer_to_lab"]

        for item in queue_items:
            job_id = item["job_id"]

            status_response = client.get(f"/jobs/{job_id}/status")
            decision_response = client.get(f"/jobs/{job_id}/decision")
            artifacts_response = client.get(f"/jobs/{job_id}/artifacts")
            semantic_ui_response = client.get(f"/jobs/{job_id}/semantic-ui")
            c1_response = client.get(f"/jobs/{job_id}/semantic-ui/c1")
            explanation_response = client.get(f"/jobs/{job_id}/copilot/explanation")
            answer_response = client.get(
                f"/jobs/{job_id}/copilot/answer",
                params={"question": "Why is this case prioritized?"},
            )
            queue_summary_response = client.get(f"/jobs/{job_id}/copilot/queue-summary")

            assert status_response.status_code == 200
            assert decision_response.status_code == 200
            assert artifacts_response.status_code == 200
            assert semantic_ui_response.status_code == 200
            assert c1_response.status_code == 200
            assert explanation_response.status_code == 200
            assert answer_response.status_code == 200
            assert queue_summary_response.status_code == 200

            status_payload = status_response.json()
            decision_payload = decision_response.json()
            artifacts_payload = artifacts_response.json()
            semantic_ui_payload = semantic_ui_response.json()
            c1_payload = c1_response.json()

            assert status_payload["job_id"] == job_id
            assert decision_payload["decision"]["job_id"] == job_id
            assert artifacts_payload["artifacts"]
            assert all(artifact["preview_eligible"] for artifact in artifacts_payload["artifacts"])
            assert semantic_ui_payload["semantic_ui"]["queue_block"]["items"][0]["job_id"] == job_id
            assert c1_payload["job_id"] == job_id
            assert c1_payload["status"] == "unavailable"
            assert c1_payload["fallback_required"] is True
            assert c1_payload["c1_response"] is None
            assert c1_payload["semantic_ui"]["queue_block"]["items"][0]["job_id"] == job_id
            for artifact in artifacts_payload["artifacts"]:
                preview_response = client.get(
                    f"/jobs/{job_id}/artifacts/{artifact['artifact_id']}/preview",
                    params={"max_bytes": 512},
                )
                assert preview_response.status_code == 200
                preview_payload = preview_response.json()
                assert preview_payload["artifact_id"] == artifact["artifact_id"]
                assert preview_payload["content"]
                assert preview_payload["encoding"] == "utf-8"

            for copilot_response in (
                explanation_response.json(),
                answer_response.json(),
                queue_summary_response.json(),
            ):
                assert copilot_response["job_status"]["job_id"] == job_id
                assert copilot_response["output_origin"]["mode"] == "fallback"
                assert copilot_response["output_origin"]["provider"] == "demo_fixture"
                assert copilot_response["copilot"]["cited_evidence_ids"]
    finally:
        client.close()
