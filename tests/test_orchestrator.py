"""Async HTTP integration tests for code-and-review orchestration."""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app.main import create_app
from app.schemas import Run, RunDiffsResponse, RunStatus, TaskType

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    application = create_app()
    transport = httpx.ASGITransport(app=application)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as async_client:
            yield async_client


async def wait_for_approval(client: httpx.AsyncClient, run_id: str) -> Run:
    last_status: RunStatus | None = None
    try:
        async with asyncio.timeout(2):
            while True:
                response = await client.get(f"/v1/runs/{run_id}")
                assert response.status_code == 200, response.text
                run = Run.model_validate(response.json())
                assert run.run_id == run_id
                last_status = run.status
                if run.status == RunStatus.AWAITING_APPROVAL:
                    return run
                assert run.status in (RunStatus.QUEUED, RunStatus.RUNNING), response.text
                await asyncio.sleep(0.01)
    except TimeoutError:
        pytest.fail(f"Run {run_id} did not reach awaiting_approval; last status: {last_status}.")


async def create_code_and_review_run(client: httpx.AsyncClient) -> Run:
    response = await client.post(
        "/v1/runs",
        json={
            "prompt": "Add auth middleware and update docs",
            "pipeline_mode": TaskType.PIPELINE.value,
            "target_files": ["app/routes/users.py"],
        },
    )
    assert response.status_code == 202, response.text
    initial = Run.model_validate(response.json())
    assert initial.status == RunStatus.QUEUED
    assert initial.pending_checkpoint is None
    assert initial.state_history == []
    assert initial.context_diffs == {}

    run = await wait_for_approval(client, initial.run_id)
    assert run.run_id == initial.run_id
    assert run.status == RunStatus.AWAITING_APPROVAL
    assert run.pending_checkpoint is not None
    assert [step.agent_name for step in run.state_history] == ["Coder", "DocReviewer"]
    assert [step.step_index for step in run.state_history] == [0, 1]
    assert all(step.status == "completed" for step in run.state_history)
    assert all(step.output_summary for step in run.state_history)
    return run


async def test_health_check(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_full_pipeline_approval_flow(client: httpx.AsyncClient) -> None:
    run = await create_code_and_review_run(client)
    assert run.pending_checkpoint is not None

    diffs_response = await client.get(f"/v1/runs/{run.run_id}/diffs")
    assert diffs_response.status_code == 200, diffs_response.text
    assert set(diffs_response.json()) == {"run_id", "diffs", "context_diffs"}
    proposals = RunDiffsResponse.model_validate(diffs_response.json())
    assert proposals.run_id == run.run_id
    assert proposals.diffs
    assert all(diff.patch.strip() for diff in proposals.diffs)
    assert all("--- " in diff.patch and "+++ " in diff.patch for diff in proposals.diffs)
    assert "app/routes/users.py" in {diff.path for diff in proposals.diffs}
    assert any(diff.path.endswith(".md") for diff in proposals.diffs)
    review = proposals.context_diffs["DocReviewer"]
    assert "# Documentation Review" in review["feedback"]
    assert review["findings"]
    assert review["findings"][0]["path"] == "app/routes/users.py"

    approval_response = await client.post(
        f"/v1/runs/{run.run_id}/approvals/{run.pending_checkpoint.checkpoint_id}",
        json={"decision": "approved"},
    )
    assert approval_response.status_code == 200, approval_response.text
    approved = Run.model_validate(approval_response.json())
    assert approved.status == RunStatus.COMPLETED
    assert approved.pending_checkpoint is None
    assert approved.current_step is None
    assert approved.state_history == run.state_history
    assert approved.diffs == proposals.diffs

    final_response = await client.get(f"/v1/runs/{run.run_id}")
    assert final_response.status_code == 200, final_response.text
    assert Run.model_validate(final_response.json()) == approved


async def test_rejection_flow(client: httpx.AsyncClient) -> None:
    run = await create_code_and_review_run(client)
    assert run.pending_checkpoint is not None

    rejection_response = await client.post(
        f"/v1/runs/{run.run_id}/approvals/{run.pending_checkpoint.checkpoint_id}",
        json={"decision": "rejected", "feedback": "The proposed API change is out of scope."},
    )
    assert rejection_response.status_code == 200, rejection_response.text
    rejected = Run.model_validate(rejection_response.json())
    assert rejected.status == RunStatus.REJECTED
    assert rejected.pending_checkpoint is None
    assert rejected.current_step is None
    assert rejected.state_history == run.state_history
    assert rejected.diffs == run.diffs

    final_response = await client.get(f"/v1/runs/{run.run_id}")
    assert final_response.status_code == 200, final_response.text
    assert Run.model_validate(final_response.json()) == rejected


async def test_invalid_run_or_checkpoint_returns_404(client: httpx.AsyncClient) -> None:
    missing = await client.get("/v1/runs/invalid_id")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Run not found"}

    run = await create_code_and_review_run(client)
    invalid = await client.post(
        f"/v1/runs/{run.run_id}/approvals/invalid_chk",
        json={"decision": "approved"},
    )
    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "Invalid checkpoint ID"}
    unchanged = await client.get(f"/v1/runs/{run.run_id}")
    assert unchanged.status_code == 200
    assert Run.model_validate(unchanged.json()) == run
