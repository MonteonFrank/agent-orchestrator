from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas import (
    ApprovalRequest,
    Checkpoint,
    CreateRunRequest,
    FileDiff,
    Run,
    RunDiffsResponse,
    RunStatus,
    StepState,
    TaskType,
)


def test_request_defaults_are_independent():
    first = CreateRunRequest(prompt="Add pagination.")
    second = CreateRunRequest(prompt="Review API documentation.")
    first.target_files.append("app/routes/users.py")
    assert first.pipeline_mode is TaskType.PIPELINE
    assert second.target_files == []


@pytest.mark.parametrize("model", [CreateRunRequest, FileDiff, StepState, Checkpoint, Run, ApprovalRequest, RunDiffsResponse])
def test_documented_examples_validate(model):
    schema = model.model_json_schema()
    assert schema["examples"]
    for example in schema["examples"]:
        instance = model.model_validate(example)
        assert instance.model_dump(mode="json") == example
    assert all(field.get("description") for field in schema["properties"].values())


def test_enum_contracts():
    assert [mode.value for mode in TaskType] == ["code", "review_docs", "code_and_review"]
    assert [state.value for state in RunStatus] == [
        "queued", "running", "awaiting_approval", "completed", "failed", "rejected",
    ]


@pytest.mark.parametrize("status", ["modified", "added", "deleted"])
def test_allowed_file_statuses(status):
    assert FileDiff(path="README.md", status=status, patch="").status == status


@pytest.mark.parametrize("decision", ["approved", "rejected", "request_changes"])
def test_allowed_approval_decisions(decision):
    assert ApprovalRequest(decision=decision, feedback=None).decision == decision


@pytest.mark.parametrize("model,payload", [
    (CreateRunRequest, {"prompt": "Test", "pipeline_mode": "UNKNOWN"}),
    (FileDiff, {"path": "README.md", "status": "renamed", "patch": ""}),
    (ApprovalRequest, {"decision": "maybe", "feedback": None}),
])
def test_invalid_choices_are_rejected(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_optional_fields_default_to_none():
    now = datetime.now(timezone.utc)
    run = Run(
        run_id="run_8f3a2b19", status=RunStatus.QUEUED, created_at=now, updated_at=now,
    )
    assert run.current_step is None
    assert run.pending_checkpoint is None
    assert ApprovalRequest(decision="approved").feedback is None
    with pytest.raises(ValidationError):
        Run(run_id="run_8f3a2b19", status=RunStatus.QUEUED, state_history=[], diffs=[])


def test_models_integrate_with_fastapi_openapi():
    schema_app = FastAPI()

    @schema_app.post("/runs", response_model=Run)
    def create_run(request: CreateRunRequest) -> Run:
        return Run.model_validate(Run.model_json_schema()["examples"][0])

    @schema_app.post("/approvals", response_model=ApprovalRequest)
    def approve(request: ApprovalRequest) -> ApprovalRequest:
        return request

    schemas = schema_app.openapi()["components"]["schemas"]
    for model in [CreateRunRequest, FileDiff, StepState, Checkpoint, Run, ApprovalRequest]:
        assert schemas[model.__name__]["examples"] == model.model_json_schema()["examples"]


def test_health_and_documentation():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").json()["info"]["title"] == "Agent Orchestrator API"