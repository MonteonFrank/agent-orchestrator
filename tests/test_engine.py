import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import engine as engine_module
from app.engine import BaseAgent, CodingAgent, DocReviewerAgent, OrchestratorEngine
from app.schemas import ApprovalRequest, CreateRunRequest, FileDiff, RunStatus, TaskType


@pytest.fixture(autouse=True)
def offline_agents(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.mark.parametrize("agent_type", [CodingAgent, DocReviewerAgent])
async def test_agents_use_async_gemini_and_parse_proposals(monkeypatch, agent_type):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    review = agent_type is DocReviewerAgent
    path = "docs/api.md" if review else "app/users.py"
    payload = {
        "output_summary": "Updated pagination.",
        "files": [{"path": path, "status": "modified", "original": "old\n", "proposed": "new\n"}],
        "feedback": "Documented pagination.",
        "findings": [{"path": path, "message": "Document page limits."}],
    }
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(payload))
    )
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(engine_module.genai, "Client", factory)
    result = await agent_type().execute(
        "Add pagination.", json.dumps({"feedback": "Cap at 100."}), [path]
    )
    factory.assert_called_once_with()
    call = client.aio.models.generate_content.await_args.kwargs
    assert call["model"] == "gemini-2.5-flash"
    assert call["config"]["system_instruction"] == (
        "You are an expert Technical Writer and API Reviewer. Review documentation and specs against code context."
        if review else
        "You are an expert Software Engineer. Implement coding tasks and return structured outputs."
    )
    assert call["config"]["response_mime_type"] == "application/json"
    assert json.loads(call["contents"])["context"]["feedback"] == "Cap at 100."
    assert result["output_summary"] == "Updated pagination."
    assert isinstance(result["diffs"][0], FileDiff)
    assert "-old\n+new\n" in result["diffs"][0].patch
    if review:
        assert result["feedback"] == payload["feedback"]
        assert result["findings"] == payload["findings"]
    else:
        assert result["code_outputs"] == {path: "new\n"}


@pytest.mark.parametrize("agent_type", [CodingAgent, DocReviewerAgent])
@pytest.mark.parametrize("failure", ["initialization", "api", "invalid_json", "invalid_schema"])
async def test_gemini_failures_fall_back_to_mocks(monkeypatch, agent_type, failure):
    expected = await agent_type().execute("Add pagination.")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("API unavailable") if failure == "api" else None,
        return_value=SimpleNamespace(text="not JSON" if failure == "invalid_json" else "{}"),
    )
    factory = MagicMock(
        side_effect=ValueError("Missing credentials") if failure == "initialization" else None,
        return_value=client,
    )
    monkeypatch.setattr(engine_module.genai, "Client", factory)
    assert await agent_type().execute("Add pagination.") == expected


async def test_missing_key_does_not_initialize_gemini(monkeypatch):
    factory = MagicMock(side_effect=AssertionError("Must not initialize without a key"))
    monkeypatch.setattr(engine_module.genai, "Client", factory)
    await CodingAgent().execute("Add pagination.")
    await DocReviewerAgent().execute("Add pagination.")
    factory.assert_not_called()


async def ready_run(engine):
    run = engine.create_run(
        CreateRunRequest(prompt="Add pagination.", target_files=["app/users.py"])
    )
    assert await engine.run_pipeline(run.run_id) is run
    return run


def test_base_agent_is_abstract():
    with pytest.raises(TypeError):
        BaseAgent()


async def test_coding_agent_generates_structured_code_and_diffs():
    result = await CodingAgent().execute("Add pagination.")
    assert all(isinstance(diff, FileDiff) for diff in result["diffs"])
    diff = FileDiff.model_validate(result["diffs"][0])
    assert diff.path == "app/generated_task.py"
    assert diff.status == "added"
    assert "--- /dev/null" in diff.patch
    assert "Add pagination." in diff.patch
    compile(result["code_outputs"][diff.path], diff.path, "exec")


async def test_coding_agent_accepts_existing_code_context():
    result = await CodingAgent().execute(
        "Replace the old function.", "def old_function():\n    pass\n"
    )
    diff = FileDiff.model_validate(result["diffs"][0])
    assert diff.status == "modified"
    assert "-def old_function():" in diff.patch
    assert "+def generated_task():" in diff.patch


async def test_coding_agent_uses_targets_and_feedback():
    result = await CodingAgent().execute(
        "Add pagination.",
        json.dumps(
            {
                "target_files": ["app/users.py", "tests/test_users.py", "app/users.py"],
                "feedback": "Cap the page size at 100.",
            }
        ),
    )
    assert [diff.path for diff in result["diffs"]] == ["app/users.py", "tests/test_users.py"]
    assert all(diff.status == "modified" for diff in result["diffs"])
    assert "Cap the page size at 100." in result["diffs"][0].patch


async def test_reviewer_produces_markdown_and_document_proposals():
    code_result = await CodingAgent().execute("Add pagination.")
    result = await DocReviewerAgent().execute(
        "Add pagination.",
        json.dumps([diff.model_dump(mode="json") for diff in code_result["diffs"]]),
    )
    assert "# Documentation Review" in result["feedback"]
    assert "app/generated_task.py" in result["feedback"]
    assert "OpenAPI" in result["feedback"]
    assert "not been inspected" in result["feedback"]
    assert all(isinstance(diff, FileDiff) for diff in result["diffs"])
    proposal = FileDiff.model_validate(result["diffs"][0])
    assert proposal.path.endswith(".md")
    assert "+# Documentation Review" in proposal.patch


@pytest.mark.parametrize(
    "mode,agent_names,paths",
    [
        (TaskType.CODE, ["Coder"], ["app/users.py"]),
        (TaskType.REVIEW_DOCS, ["DocReviewer"], ["docs/orchestrator-review.md"]),
        (
            TaskType.PIPELINE,
            ["Coder", "DocReviewer"],
            ["app/users.py", "docs/orchestrator-review.md"],
        ),
    ],
)
async def test_run_executes_one_agent_per_step_and_waits_for_approval(mode, agent_names, paths):
    engine = OrchestratorEngine()
    request = CreateRunRequest(
        prompt="Add pagination.", pipeline_mode=mode, target_files=["app/users.py"]
    )
    run = engine.create_run(request)
    assert engine.runs[run.run_id] is run
    assert run.status is RunStatus.QUEUED
    assert run.current_step is None
    assert run.state_history == []
    assert run.pending_checkpoint is None
    request.target_files.append("unexpected.py")
    for step_index, agent_name in enumerate(agent_names):
        assert await engine.step_run(run.run_id) is run
        assert run.state_history[-1].agent_name == agent_name
        assert [step.agent_name for step in run.state_history] == agent_names[: step_index + 1]
        assert [diff.path for diff in run.diffs] == paths[: step_index + 1]
        if step_index + 1 < len(agent_names):
            assert run.status is RunStatus.RUNNING
            assert run.current_step == agent_names[step_index + 1]
            assert run.pending_checkpoint is None
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert run.current_step is None
    assert run.pending_checkpoint is not None
    assert [step.agent_name for step in run.state_history] == agent_names
    assert [step.step_index for step in run.state_history] == list(range(len(agent_names)))
    assert len(run.diffs) == len(paths)
    before = run.model_dump()
    assert (await engine.step_run(run.run_id)).model_dump() == before


@pytest.mark.parametrize(
    "decision,status", [("approved", RunStatus.COMPLETED), ("rejected", RunStatus.REJECTED)]
)
async def test_approval_and_rejection_are_terminal(decision, status):
    engine = OrchestratorEngine()
    run = await ready_run(engine)
    checkpoint_id = run.pending_checkpoint.checkpoint_id
    assert (
        engine.submit_approval(
            run.run_id, checkpoint_id, ApprovalRequest(decision=decision, feedback=None)
        )
        is run
    )
    assert run.status is status
    assert run.pending_checkpoint is None
    assert run.current_step is None
    before = run.model_dump()
    assert (await engine.step_run(run.run_id)).model_dump() == before
    with pytest.raises(ValueError, match="not awaiting"):
        engine.submit_approval(
            run.run_id, checkpoint_id, ApprovalRequest(decision=decision, feedback=None)
        )


async def test_requested_changes_restart_pipeline_and_replace_proposals():
    engine = OrchestratorEngine()
    run = await ready_run(engine)
    previous_checkpoint = run.pending_checkpoint.checkpoint_id
    engine.submit_approval(
        run.run_id,
        previous_checkpoint,
        ApprovalRequest(
            decision="request_changes",
            feedback="Cap page size at 100.",
        ),
    )
    assert run.status is RunStatus.RUNNING
    assert run.current_step == "Coder"
    assert run.pending_checkpoint is None
    assert run.context_diffs == {"feedback": ["Cap page size at 100."]}
    run.context_diffs["feedback"].append("Unrequested mutation.")
    await engine.step_run(run.run_id)
    assert len(run.diffs) == 1
    assert "Cap page size at 100." in run.diffs[0].patch
    assert "Unrequested mutation." not in run.diffs[0].patch
    assert run.context_diffs["feedback"] == ["Cap page size at 100."]
    await engine.step_run(run.run_id)
    assert len(run.diffs) == 2
    assert "Cap page size at 100." in run.diffs[1].patch
    assert "Unrequested mutation." not in run.diffs[1].patch
    assert [step.step_index for step in run.state_history] == [0, 1, 2, 3]
    assert run.pending_checkpoint.checkpoint_id != previous_checkpoint
    before = run.model_dump()
    with pytest.raises(ValueError, match="does not match"):
        engine.submit_approval(
            run.run_id, previous_checkpoint, ApprovalRequest(decision="approved", feedback=None)
        )
    assert run.model_dump() == before


async def test_invalid_approvals_do_not_mutate_run():
    engine = OrchestratorEngine()
    run = engine.create_run(CreateRunRequest(prompt="Test"))
    approval = ApprovalRequest(decision="approved", feedback=None)
    before = run.model_dump()
    with pytest.raises(ValueError, match="not awaiting"):
        engine.submit_approval(run.run_id, "wrong", approval)
    assert run.model_dump() == before
    await engine.step_run(run.run_id)
    await engine.step_run(run.run_id)
    before = run.model_dump()
    with pytest.raises(ValueError, match="does not match"):
        engine.submit_approval(run.run_id, "wrong", approval)
    assert run.model_dump() == before


async def test_unknown_run_ids_raise_key_error():
    engine = OrchestratorEngine()
    with pytest.raises(KeyError):
        await engine.step_run("missing")
    with pytest.raises(KeyError):
        engine.submit_approval(
            "missing", "missing", ApprovalRequest(decision="approved", feedback=None)
        )


async def test_runs_are_independent():
    engine = OrchestratorEngine()
    first = await ready_run(engine)
    second = engine.create_run(CreateRunRequest(prompt="Another task."))
    assert first.run_id != second.run_id
    await engine.step_run(second.run_id)
    assert first.status is RunStatus.AWAITING_APPROVAL
    assert len(first.state_history) == 2
    assert second.status is RunStatus.RUNNING
    assert len(second.state_history) == 1


class FailingAgent(BaseAgent):
    agent_name = "FailingAgent"

    async def execute(
        self, prompt: str, context: str = "", target_files: list[str] | None = None
    ) -> dict:
        raise RuntimeError("Mock agent failed")


class InvalidResultAgent(BaseAgent):
    agent_name = "InvalidResultAgent"

    async def execute(
        self, prompt: str, context: str = "", target_files: list[str] | None = None
    ) -> dict:
        return {
            "diffs": [{"path": "bad.py", "status": "invalid", "patch": ""}],
            "output_summary": "Bad result",
        }


@pytest.mark.parametrize("agent", [FailingAgent(), InvalidResultAgent()])
@pytest.mark.parametrize("failing_step", [0, 1])
async def test_agent_errors_fail_run_without_partial_diff_updates(agent, failing_step):
    engine = OrchestratorEngine(
        coding_agent=agent if failing_step == 0 else None,
        doc_reviewer_agent=agent if failing_step == 1 else None,
    )
    run = engine.create_run(CreateRunRequest(prompt="Test"))
    if failing_step == 1:
        await engine.step_run(run.run_id)
    before_diffs = [diff.model_dump() for diff in run.diffs]
    await engine.step_run(run.run_id)
    assert run.status is RunStatus.FAILED
    assert run.current_step is None
    assert run.pending_checkpoint is None
    assert run.state_history[-1].status == "failed"
    assert run.state_history[-1].agent_name == agent.agent_name
    expected_error = (
        "RuntimeError: Mock agent failed" if isinstance(agent, FailingAgent) else "ValidationError"
    )
    assert expected_error in run.state_history[-1].output_summary
    assert [diff.model_dump() for diff in run.diffs] == before_diffs
    before = run.model_dump()
    assert (await engine.step_run(run.run_id)).model_dump() == before
