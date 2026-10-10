import asyncio
import json

import pytest
import httpx

from app.engine import BaseAgent, CodingAgent, DocReviewerAgent, OrchestratorEngine
from app.main import create_app
from app.schemas import ApprovalRequest, CreateRunRequest, FileDiff, RunStatus, TaskType


@pytest.mark.parametrize(
    "mode,names",
    [
        (TaskType.CODE, ["Coder"]),
        (TaskType.REVIEW_DOCS, ["DocReviewer"]),
        (TaskType.PIPELINE, ["Coder", "DocReviewer"]),
    ],
)
async def test_mode_specific_pipelines(mode, names):
    engine = OrchestratorEngine()
    run = engine.create_run(CreateRunRequest(prompt="Add pagination.", pipeline_mode=mode))
    assert run.status is RunStatus.QUEUED
    assert await engine.run_pipeline(run.run_id) is run
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert [step.agent_name for step in run.state_history] == names
    assert len(run.diffs) == len(names)
    assert all(isinstance(diff, FileDiff) for diff in run.diffs)
    assert run.pending_checkpoint is not None


async def test_agents_return_models_and_structured_findings():
    generated = await CodingAgent().execute("Add pagination.", target_files=["app/users.py"])
    assert isinstance(generated["diffs"][0], FileDiff)
    reviewed = await DocReviewerAgent().execute(
        "Document pagination.",
        json.dumps(
            {
                "diffs": [diff.model_dump(mode="json") for diff in generated["diffs"]],
            }
        ),
    )
    assert reviewed["findings"][0]["path"] == "app/users.py"
    assert isinstance(reviewed["diffs"][0], FileDiff)


async def test_context_forwarding_and_multiple_revision_rounds():
    class RecordingReviewer(DocReviewerAgent):
        async def execute(self, prompt, context="", target_files=None):
            self.context = json.loads(context)
            return await super().execute(prompt, context, target_files)

    reviewer = RecordingReviewer()
    engine = OrchestratorEngine(doc_reviewer_agent=reviewer)
    run = engine.create_run(CreateRunRequest(prompt="Add pagination."))
    await engine.run_pipeline(run.run_id)
    assert reviewer.context["code_outputs"]
    assert reviewer.context["diffs"]
    assert run.context_diffs["DocReviewer"]["findings"]
    for feedback in ["Limit to 100.", "Document errors."]:
        old_checkpoint = run.pending_checkpoint.checkpoint_id
        engine.submit_approval(
            run.run_id,
            old_checkpoint,
            ApprovalRequest(
                decision="request_changes",
                feedback=feedback,
            ),
        )
        assert run.status is RunStatus.RUNNING
        assert feedback in run.context_diffs["feedback"]
        await engine.run_pipeline(run.run_id)
        assert run.pending_checkpoint.checkpoint_id != old_checkpoint
        assert len(run.diffs) == 2
        assert feedback in run.diffs[0].patch
    assert reviewer.context["feedback"] == ["Limit to 100.", "Document errors."]
    assert len(run.state_history) == 6


async def test_duplicate_execution_is_serialized():
    class YieldingCoder(CodingAgent):
        async def execute(self, prompt, context="", target_files=None):
            await asyncio.sleep(0)
            return await super().execute(prompt, context, target_files)

    engine = OrchestratorEngine(coding_agent=YieldingCoder())
    run = engine.create_run(CreateRunRequest(prompt="Test"))
    await asyncio.gather(engine.run_pipeline(run.run_id), engine.run_pipeline(run.run_id))
    assert len(run.state_history) == 2


async def test_cancelled_agent_marks_run_failed():
    class CancelledAgent(BaseAgent):
        agent_name = "Cancelled"

        async def execute(self, prompt, context="", target_files=None):
            raise asyncio.CancelledError

    engine = OrchestratorEngine(coding_agent=CancelledAgent())
    run = engine.create_run(CreateRunRequest(prompt="Test"))
    with pytest.raises(asyncio.CancelledError):
        await engine.run_pipeline(run.run_id)
    assert run.status is RunStatus.FAILED
    assert run.current_step is None
    assert run.state_history[-1].status == "failed"


async def test_api_reads_running_state_while_agent_waits():
    started = asyncio.Event()
    release = asyncio.Event()

    class WaitingCoder(CodingAgent):
        async def execute(self, prompt, context="", target_files=None):
            started.set()
            await release.wait()
            return await super().execute(prompt, context, target_files)

    engine = OrchestratorEngine(coding_agent=WaitingCoder())
    run = engine.create_run(CreateRunRequest(prompt="Test"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(engine)),
        base_url="http://test",
    ) as client:
        task = asyncio.create_task(engine.run_pipeline(run.run_id))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            response = await asyncio.wait_for(client.get(f"/v1/runs/{run.run_id}"), timeout=1)
            assert response.json()["status"] == "running"
            assert response.json()["current_step"] == "Coder"
        finally:
            release.set()
            await asyncio.wait_for(task, timeout=1)
