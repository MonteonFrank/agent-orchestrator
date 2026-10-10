import asyncio

import pytest
from fastapi.testclient import TestClient

from app.engine import BaseAgent, OrchestratorEngine
from app.main import create_app
from app.schemas import CreateRunRequest, RunDiffsResponse, TaskType


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


async def gather_pipeline_tasks(application):
    await asyncio.gather(*tuple(application.state.pipeline_tasks))


def wait_for_pipeline_tasks(client):
    assert client.portal is not None
    client.portal.call(gather_pipeline_tasks, client.app)


def create_run(client, pipeline_mode=TaskType.PIPELINE.value):
    response = client.post(
        "/v1/runs",
        json={
            "prompt": "Add pagination.",
            "target_files": ["app/users.py"],
            "pipeline_mode": pipeline_mode,
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["state_history"] == []
    assert response.json()["pending_checkpoint"] is None
    assert response.json()["context_diffs"] == {}
    wait_for_pipeline_tasks(client)
    return response.json()["run_id"]


def test_creation_executes_pipeline_in_background(client):
    run_id = create_run(client)
    response = client.get(f"/v1/runs/{run_id}")
    assert response.status_code == 200
    run = response.json()
    assert run["status"] == "awaiting_approval"
    assert [step["agent_name"] for step in run["state_history"]] == ["Coder", "DocReviewer"]
    assert run["pending_checkpoint"]["checkpoint_id"].startswith("chk_")
    assert run["current_step"] is None


@pytest.mark.parametrize(
    "pipeline_mode,expected_agents",
    [
        (TaskType.CODE.value, ["Coder"]),
        (TaskType.REVIEW_DOCS.value, ["DocReviewer"]),
        (TaskType.PIPELINE.value, ["Coder", "DocReviewer"]),
    ],
)
def test_pipeline_modes(client, pipeline_mode, expected_agents):
    run_id = create_run(client, pipeline_mode)
    run = client.get(f"/v1/runs/{run_id}").json()
    assert run["status"] == "awaiting_approval"
    assert [step["agent_name"] for step in run["state_history"]] == expected_agents
    assert [step["step_index"] for step in run["state_history"]] == list(
        range(len(expected_agents))
    )
    assert all(step["status"] == "completed" for step in run["state_history"])


def test_diffs_include_documentation_context(client):
    run_id = create_run(client)
    response = client.get(f"/v1/runs/{run_id}/diffs")
    assert response.status_code == 200
    assert set(response.json()) == {"run_id", "diffs", "context_diffs"}
    result = RunDiffsResponse.model_validate(response.json())
    assert result.run_id == run_id
    assert [diff.path for diff in result.diffs] == ["app/users.py", "docs/orchestrator-review.md"]
    review = result.context_diffs["DocReviewer"]
    assert "# Documentation Review" in review["feedback"]
    assert review["findings"]
    assert review["findings"][0]["path"] == "app/users.py"


@pytest.mark.parametrize("decision,expected", [("approved", "completed"), ("rejected", "rejected")])
def test_approval_decisions(client, decision, expected):
    run_id = create_run(client)
    checkpoint = client.get(f"/v1/runs/{run_id}").json()["pending_checkpoint"]["checkpoint_id"]
    response = client.post(
        f"/v1/runs/{run_id}/approvals/{checkpoint}", json={"decision": decision, "feedback": None}
    )
    assert response.status_code == 200
    assert response.json()["status"] == expected
    assert response.json()["pending_checkpoint"] is None
    assert client.get(f"/v1/runs/{run_id}").json()["status"] == expected
    assert (
        client.post(
            f"/v1/runs/{run_id}/approvals/{checkpoint}",
            json={"decision": decision, "feedback": None},
        ).status_code
        == 400
    )


def test_requested_changes_resume_execution_and_invalidate_old_checkpoint(client):
    run_id = create_run(client)
    checkpoint = client.get(f"/v1/runs/{run_id}").json()["pending_checkpoint"]["checkpoint_id"]
    response = client.post(
        f"/v1/runs/{run_id}/approvals/{checkpoint}",
        json={
            "decision": "request_changes",
            "feedback": "Cap page size at 100.",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert response.json()["pending_checkpoint"] is None
    assert response.json()["context_diffs"] == {"feedback": ["Cap page size at 100."]}
    wait_for_pipeline_tasks(client)
    run = client.get(f"/v1/runs/{run_id}").json()
    assert run["status"] == "awaiting_approval"
    assert len(run["state_history"]) == 4
    assert run["pending_checkpoint"]["checkpoint_id"] != checkpoint
    proposals = client.get(f"/v1/runs/{run_id}/diffs").json()
    assert len(proposals["diffs"]) == 2
    assert "Cap page size at 100." in proposals["diffs"][0]["patch"]
    assert set(proposals) == {"run_id", "diffs", "context_diffs"}
    assert proposals["context_diffs"]["feedback"] == ["Cap page size at 100."]
    review = proposals["context_diffs"]["DocReviewer"]
    assert "Cap page size at 100." in review["feedback"]
    assert review["findings"]
    stale = client.post(
        f"/v1/runs/{run_id}/approvals/{checkpoint}", json={"decision": "approved", "feedback": None}
    )
    assert stale.status_code == 400
    assert stale.json() == {"detail": "Invalid checkpoint ID"}
    assert client.get(f"/v1/runs/{run_id}").json() == run


@pytest.mark.parametrize(
    "path", ["/v1/runs/missing", "/v1/runs/missing/diffs", "/v1/runs/missing/approvals/missing"]
)
def test_unknown_runs_return_404(client, path):
    response = (
        client.post(path, json={"decision": "approved", "feedback": None})
        if "/approvals/" in path
        else client.get(path)
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Run not found"}


def test_invalid_checkpoint_returns_400_without_changes(client):
    run_id = create_run(client)
    before = client.get(f"/v1/runs/{run_id}").json()
    response = client.post(
        f"/v1/runs/{run_id}/approvals/wrong", json={"decision": "approved", "feedback": None}
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid checkpoint ID"}
    assert client.get(f"/v1/runs/{run_id}").json() == before


def test_approval_before_checkpoint_returns_400():
    engine = OrchestratorEngine()
    run = engine.create_run(CreateRunRequest(prompt="Test"))
    with TestClient(create_app(engine)) as client:
        response = client.post(
            f"/v1/runs/{run.run_id}/approvals/wrong",
            json={"decision": "approved", "feedback": None},
        )
    assert response.status_code == 400
    assert response.json() == {"detail": "Run is not awaiting approval"}
    assert run.status.value == "queued"


def test_queued_diffs_are_empty():
    engine = OrchestratorEngine()
    run = engine.create_run(CreateRunRequest(prompt="Test"))
    with TestClient(create_app(engine)) as client:
        response = client.get(f"/v1/runs/{run.run_id}/diffs")
    assert response.status_code == 200
    assert response.json() == {"run_id": run.run_id, "diffs": [], "context_diffs": {}}


def test_request_validation_remains_422(client):
    assert client.post("/v1/runs", json={}).status_code == 422
    assert (
        client.post("/v1/runs", json={"prompt": "Test", "pipeline_mode": "UNKNOWN"}).status_code
        == 422
    )
    run_id = create_run(client)
    assert (
        client.post(
            f"/v1/runs/{run_id}/approvals/wrong", json={"decision": "maybe", "feedback": None}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("origin", ["http://localhost:3000", "https://untrusted.example"])
def test_cors_allows_any_origin(client, origin):
    headers = {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }
    response = client.options("/v1/runs", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers
    error = client.get("/v1/runs/missing", headers={"Origin": origin})
    assert error.status_code == 404
    assert error.json() == {"detail": "Run not found"}
    assert error.headers["access-control-allow-origin"] == "*"


@pytest.mark.parametrize("method", ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"])
def test_cors_allows_any_method(client, method):
    response = client.options(
        "/v1/runs",
        headers={"Origin": "https://console.example", "Access-Control-Request-Method": method},
    )
    assert response.status_code == 200
    assert method in response.headers["access-control-allow-methods"].split(", ")


@pytest.mark.parametrize("requested_headers", ["content-type", "authorization, x-custom-header"])
def test_cors_allows_any_header(client, requested_headers):
    response = client.options(
        "/v1/runs",
        headers={
            "Origin": "https://console.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": requested_headers,
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-headers"] == requested_headers


class FailingAgent(BaseAgent):
    agent_name = "FailingAgent"

    async def execute(
        self, prompt: str, context: str = "", target_files: list[str] | None = None
    ) -> dict:
        raise RuntimeError("Mock execution failure")


def test_background_agent_failure_is_visible():
    with TestClient(create_app(OrchestratorEngine(coding_agent=FailingAgent()))) as client:
        run_id = create_run(client)
        run = client.get(f"/v1/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert "Mock execution failure" in run["state_history"][0]["output_summary"]


def test_openapi_documents_routes_models_and_status_codes(client):
    schema = client.get("/openapi.json").json()
    assert schema["info"]["version"] == "1.0.0"
    paths = schema["paths"]
    assert "202" in paths["/v1/runs"]["post"]["responses"]
    assert "404" in paths["/v1/runs/{run_id}"]["get"]["responses"]
    assert paths["/v1/runs/{run_id}/diffs"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("RunDiffsResponse")
    assert {"400", "404"}.issubset(
        paths["/v1/runs/{run_id}/approvals/{checkpoint_id}"]["post"]["responses"]
    )
    assert schema["components"]["schemas"]["Run"]["examples"]
    assert schema["components"]["schemas"]["RunDiffsResponse"]["examples"]
