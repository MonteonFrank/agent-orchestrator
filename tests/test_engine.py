import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app import engine as engine_module
from app.engine import BaseAgent, CodingAgent, DocReviewerAgent, OrchestratorEngine
from app.schemas import ApprovalRequest, CreateRunRequest, FileDiff, RunStatus, TaskType


@pytest.fixture(autouse=True)
def offline_agents(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.mark.parametrize("agent_type", [CodingAgent, DocReviewerAgent])
async def test_agents_use_async_gemini_and_parse_proposals(monkeypatch, capsys, agent_type):
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
    client.models.generate_content = MagicMock(
        return_value=SimpleNamespace(text=json.dumps(payload))
    )
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(engine_module.genai, "Client", factory)
    result = await agent_type().execute(
        "Add pagination.", json.dumps({"feedback": "Cap at 100."}), [path]
    )
    factory.assert_called_once_with(api_key="test-key")
    output = capsys.readouterr().out
    assert "DEBUG GEMINI SUCCESS:" in output
    assert "DEBUG GEMINI ERROR:" not in output
    assert "test-key" not in output
    call = client.models.generate_content.call_args.kwargs
    assert call["model"] == "gemini-3.8-flash"
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
async def test_gemini_failures_fall_back_to_mocks(monkeypatch, capsys, agent_type, failure):
    expected = await agent_type().execute("Add pagination.")
    capsys.readouterr()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = MagicMock()
    client.models.generate_content = MagicMock(
        side_effect=RuntimeError("API failure") if failure == "api" else None,
        return_value=SimpleNamespace(text="not JSON" if failure == "invalid_json" else "{}"),
    )
    factory = MagicMock(
        side_effect=ValueError("Missing credentials") if failure == "initialization" else None,
        return_value=client,
    )
    monkeypatch.setattr(engine_module.genai, "Client", factory)
    assert await agent_type().execute("Add pagination.") == expected
    output = capsys.readouterr().out
    assert "DEBUG GEMINI ERROR:" in output
    assert "DEBUG GEMINI SUCCESS:" not in output


@pytest.mark.parametrize("agent_type", [CodingAgent, DocReviewerAgent])
@pytest.mark.parametrize("recover", [False, True])
@pytest.mark.parametrize("error_kind", ["api_error", "message", "status_code"])
async def test_gemini_retries_unavailable_with_backoff(
    monkeypatch, capsys, agent_type, recover, error_kind
):
    from google.genai.errors import APIError

    expected = await agent_type().execute("Add pagination.")
    capsys.readouterr()
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")
    if error_kind == "api_error":
        error = APIError(503, {"error": {"message": "Service busy", "status": "UNAVAILABLE"}})
    else:
        error = RuntimeError("UNAVAILABLE test-secret-key" if error_kind == "message" else "Service busy")
        if error_kind == "status_code":
            error.status_code = 503
    response = SimpleNamespace(text=json.dumps({
        "output_summary": "Recovered Gemini proposal.",
        "feedback": "# Recovered review\n",
        "files": [],
    }))
    client = MagicMock()
    client.models.generate_content = MagicMock(
        side_effect=[error, error, error, response if recover else error]
    )
    monkeypatch.setattr(engine_module.genai, "Client", MagicMock(return_value=client))
    wait = AsyncMock()
    monkeypatch.setattr(engine_module.asyncio, "sleep", wait)
    result = await agent_type().execute("Add pagination.")
    assert client.models.generate_content.call_count == 4
    assert wait.await_args_list == [call(2), call(4), call(8)]
    output = capsys.readouterr().out
    assert "test-secret-key" not in output
    if recover:
        assert result["output_summary"] == "Recovered Gemini proposal."
        assert "DEBUG GEMINI SUCCESS:" in output
        assert output.count("DEBUG GEMINI ERROR:") == 3
    else:
        assert result == expected
        assert "DEBUG GEMINI SUCCESS:" not in output
        assert output.count("DEBUG GEMINI ERROR:") == 4


async def test_gemini_does_not_retry_invalid_credentials(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = MagicMock()
    client.models.generate_content = MagicMock(side_effect=RuntimeError("401 Invalid key"))
    monkeypatch.setattr(engine_module.genai, "Client", MagicMock(return_value=client))
    wait = AsyncMock()
    monkeypatch.setattr(engine_module.asyncio, "sleep", wait)
    await CodingAgent().execute("Add pagination.")
    client.models.generate_content.assert_called_once()
    wait.assert_not_awaited()


@pytest.mark.parametrize("agent_type", [CodingAgent, DocReviewerAgent])
async def test_gemini_error_output_redacts_api_key(monkeypatch, capsys, agent_type):
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setattr(
        engine_module.genai, "Client",
        MagicMock(side_effect=ValueError("Invalid API key: test-secret-key")),
    )
    await agent_type().execute("Add pagination.")
    output = capsys.readouterr().out
    assert "Invalid API key: [REDACTED]" in output
    assert "test-secret-key" not in output


async def test_missing_key_does_not_initialize_gemini(monkeypatch, capsys):
    factory = MagicMock(side_effect=AssertionError("Must not initialize without a key"))
    monkeypatch.setattr(engine_module.genai, "Client", factory)
    await CodingAgent().execute("Add pagination.")
    await DocReviewerAgent().execute("Add pagination.")
    factory.assert_not_called()
    assert capsys.readouterr().out.count("GEMINI_API_KEY is missing") == 2


async def test_engine_coding_entry_point_returns_structured_proposals():
    result = await OrchestratorEngine().execute_coding_agent("Add pagination.")
    assert isinstance(result["diffs"][0], FileDiff)
    assert result["output_summary"]


@pytest.mark.parametrize("review", [False, True])
async def test_engine_reads_current_key_and_creates_client_per_call(monkeypatch, capsys, review):
    clients = []

    def create_client(**kwargs):
        client = MagicMock()
        client.models.generate_content = MagicMock(return_value=SimpleNamespace(
            text=json.dumps({
                "output_summary": "Gemini proposal.",
                "feedback": "# Gemini review\n",
                "files": [],
            })
        ))
        clients.append(client)
        return client

    factory = MagicMock(side_effect=create_client)
    monkeypatch.setattr(engine_module.genai, "Client", factory)
    engine = OrchestratorEngine()
    factory.assert_not_called()

    async def execute():
        if review:
            return await engine.execute_doc_reviewer_agent("Review task.", "Code summary.")
        return await engine.execute_coding_agent("Implement task.")

    await execute()
    factory.assert_not_called()
    for api_key in ["first-test-key", "second-test-key"]:
        monkeypatch.setenv("GEMINI_API_KEY", api_key)
        result = await execute()
        factory.assert_called_with(api_key=api_key)
        if review:
            assert result == "# Gemini review\n"
        else:
            assert result["output_summary"] == "Gemini proposal."
    assert factory.call_count == 2
    assert clients[0] is not clients[1]
    for client in clients:
        client.models.generate_content.assert_called_once()
    monkeypatch.delenv("GEMINI_API_KEY")
    await execute()
    assert factory.call_count == 2
    output = capsys.readouterr().out
    assert output.count("DEBUG GEMINI SUCCESS:") == 2
    assert output.count("GEMINI_API_KEY is missing") == 2
    assert "first-test-key" not in output
    assert "second-test-key" not in output


@pytest.mark.parametrize("use_gemini", [False, True])
async def test_engine_reviewer_entry_point_preserves_code_summary(monkeypatch, use_gemini):
    code_summary = "Added GET /healthz returning a status object."
    if use_gemini:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        client = MagicMock()
        client.models.generate_content = MagicMock(return_value=SimpleNamespace(
            text=json.dumps({
                "output_summary": "Reviewed health endpoint.",
                "feedback": "# Review\n\nDocument GET /healthz in OpenAPI.\n",
                "files": [],
            })
        ))
        monkeypatch.setattr(engine_module.genai, "Client", MagicMock(return_value=client))
    review = await OrchestratorEngine().execute_doc_reviewer_agent(
        "Review health endpoint.", code_summary
    )
    assert isinstance(review, str)
    assert "OpenAPI" in review
    if use_gemini:
        contents = json.loads(client.models.generate_content.call_args.kwargs["contents"])
        assert contents["context"]["code_summary"] == code_summary
        assert review.startswith("# Review")
    else:
        assert code_summary in review


async def ready_run(engine):
    run = engine.create_run(
        CreateRunRequest(prompt="Add pagination.", target_files=["app/users.py"])
    )
    assert await engine.run_pipeline(run.run_id) is run
    return run


async def test_workspace_blueprint_multifile_review_and_approved_writes(tmp_path):
    (tmp_path / "app").mkdir()
    existing = "def shared_utility():\n    return 42\n"
    (tmp_path / "app/shared.py").write_text(existing)
    engine = OrchestratorEngine(workspace_root=tmp_path, write_on_approval=True)
    run = engine.create_run(CreateRunRequest(prompt="Add a reusable solution."))
    await engine.run_pipeline(run.run_id)
    assert run.status is RunStatus.AWAITING_APPROVAL
    blueprint = run.context_diffs["solution_blueprint"]
    assert {file["path"] for file in blueprint["files"]} == {"app/solution.py", "tests/test_solution.py"}
    assert run.context_diffs["DocReviewer"]["blueprint_validation"] == "passed"
    assert "workspace_files" not in run.context_diffs
    assert not (tmp_path / "app/solution.py").exists()
    engine.submit_approval(run.run_id, run.pending_checkpoint.checkpoint_id, ApprovalRequest(decision="approved"))
    assert run.status is RunStatus.COMPLETED
    assert (tmp_path / "app/solution.py").read_text() == run.context_diffs["code_outputs"]["app/solution.py"]
    assert (tmp_path / "tests/test_solution.py").exists()
    assert (tmp_path / "docs/orchestrator-review.md").exists()
    assert (tmp_path / "app/shared.py").read_text() == existing


async def test_workspace_existing_code_is_preserved_and_stale_approval_is_rejected(tmp_path):
    path = tmp_path / "existing.py"
    original = "def shared():\n    return 42\n"
    path.write_text(original)
    engine = OrchestratorEngine(workspace_root=tmp_path, write_on_approval=True)
    run = engine.create_run(CreateRunRequest(prompt="Extend shared code.", target_files=["existing.py"]))
    await engine.run_pipeline(run.run_id)
    assert run.context_diffs["code_outputs"]["existing.py"].startswith(original)
    assert run.diffs[0].status == "modified"
    path.write_text("User edited this file.\n")
    checkpoint = run.pending_checkpoint.checkpoint_id
    with pytest.raises(ValueError, match="Workspace changed"):
        engine.submit_approval(run.run_id, checkpoint, ApprovalRequest(decision="approved"))
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert path.read_text() == "User edited this file.\n"
    assert not (tmp_path / "docs").exists()


@pytest.mark.parametrize("relative", ["../outside.py", "/tmp/outside.py", ".env", "credentials.json", "node_modules/evil.py", "app/../../evil.py", "C:\\evil.py"])
async def test_workspace_rejects_unsafe_target_paths(tmp_path, relative):
    engine = OrchestratorEngine(workspace_root=tmp_path)
    run = engine.create_run(CreateRunRequest(prompt="Task", target_files=[relative]))
    await engine.run_pipeline(run.run_id)
    assert run.status is RunStatus.FAILED
    assert "Unsafe workspace path" in run.state_history[-1].output_summary


async def test_workspace_reader_excludes_secrets_and_symlinks(tmp_path):
    (tmp_path / "safe.py").write_text("VALUE = 1\n")
    (tmp_path / ".env").write_text("SECRET=hidden")
    (tmp_path / "credentials.json").write_text('{"secret":"hidden"}')
    (tmp_path / "linked.py").symlink_to(tmp_path / "safe.py")
    context = engine_module._read_workspace(tmp_path, [])
    assert context["workspace_files"] == {"safe.py": "VALUE = 1\n"}
    with pytest.raises(ValueError, match="Symlinks"):
        engine_module._write_workspace(tmp_path, {"linked.py": "evil"}, {})


async def test_reviewer_rejects_incomplete_blueprint():
    context = {
        "solution_blueprint": {"architecture": "Shared module", "files": [{"path": "required.py", "purpose": "Implementation"}], "dependencies": []},
        "code_outputs": {},
    }
    with pytest.raises(ValueError, match="does not match"):
        await DocReviewerAgent().execute("Review", json.dumps(context))


def test_workspace_writer_rolls_back_partial_writes(monkeypatch, tmp_path):
    (tmp_path / "existing.py").write_text("OLD = 1\n")
    replace = engine_module.os.replace
    calls = 0

    def fail_second_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Simulated disk failure")
        return replace(source, destination)

    monkeypatch.setattr(engine_module.os, "replace", fail_second_replace)
    with pytest.raises(OSError, match="disk failure"):
        engine_module._write_workspace(
            tmp_path, {"existing.py": "NEW = 2\n", "nested/new.py": "NEW = 1\n"},
            {"existing.py": "OLD = 1\n"},
        )
    assert (tmp_path / "existing.py").read_text() == "OLD = 1\n"
    assert not (tmp_path / "nested/new.py").exists()
    assert list((tmp_path / "nested").iterdir()) == []


async def test_workspace_revision_rescans_and_rejection_never_writes(tmp_path):
    path = tmp_path / "shared.py"
    path.write_text("VALUE = 1\n")
    engine = OrchestratorEngine(workspace_root=tmp_path, write_on_approval=True)
    run = engine.create_run(CreateRunRequest(prompt="Extend shared.", target_files=["shared.py"]))
    await engine.run_pipeline(run.run_id)
    engine.submit_approval(run.run_id, run.pending_checkpoint.checkpoint_id, ApprovalRequest(decision="request_changes", feedback="Keep new value."))
    path.write_text("VALUE = 2\n")
    await engine.run_pipeline(run.run_id)
    assert run.context_diffs["code_outputs"]["shared.py"].startswith("VALUE = 2\n")
    assert "Keep new value." in run.diffs[0].patch
    engine.submit_approval(run.run_id, run.pending_checkpoint.checkpoint_id, ApprovalRequest(decision="rejected"))
    assert path.read_text() == "VALUE = 2\n"
    assert not (tmp_path / "docs").exists()


async def test_workspace_real_gemini_plan_and_multifile_results(monkeypatch, tmp_path):
    (tmp_path / "shared.py").write_text("VALUE = 1\n")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    blueprint = {"architecture": "Reuse shared value.", "files": [
        {"path": "shared.py", "purpose": "Extend module"},
        {"path": "feature.py", "purpose": "Reuse utility"},
    ], "dependencies": []}
    coder = {"output_summary": "Updated two files", "files": [
        {"path": "shared.py", "status": "modified", "original": "invented\n", "proposed": "VALUE = 1\nEXTRA = 2\n"},
        {"path": "feature.py", "status": "added", "original": "", "proposed": "from shared import VALUE\nRESULT = VALUE\n"},
    ]}
    reviewer = {"output_summary": "Reviewed blueprint", "feedback": "# Review\nOpenAPI unchanged.\n", "files": [
        {"path": "docs/feature.md", "status": "added", "original": "", "proposed": "# Feature\nReuses shared VALUE.\n"},
    ]}
    client = MagicMock()
    client.models.generate_content = MagicMock(side_effect=[SimpleNamespace(text=json.dumps(payload)) for payload in [blueprint, coder, reviewer]])
    monkeypatch.setattr(engine_module.genai, "Client", MagicMock(return_value=client))
    engine = OrchestratorEngine(workspace_root=tmp_path)
    run = engine.create_run(CreateRunRequest(prompt="Extend shared value."))
    await engine.run_pipeline(run.run_id)
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert "invented" not in run.diffs[0].patch
    assert "+EXTRA = 2" in run.diffs[0].patch
    assert not (tmp_path / "feature.py").exists()
    requests = [json.loads(call.kwargs["contents"]) for call in client.models.generate_content.call_args_list]
    assert requests[1]["context"]["workspace_files"]["shared.py"] == "VALUE = 1\n"
    assert requests[2]["context"]["code_outputs"]["feature.py"].startswith("from shared")


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
