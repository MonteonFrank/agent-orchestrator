from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas import (
    ApprovalRequest,
    Checkpoint,
    CreateRunRequest,
    Run,
    RunStatus,
    StepState,
    TaskType,
)


def test_request_defaults_and_lowercase_enums():
    request = CreateRunRequest(prompt="Implement pagination.")
    assert request.pipeline_mode is TaskType.PIPELINE
    assert request.repository_ref == "main"
    assert request.target_files == []
    assert [mode.value for mode in TaskType] == ["code", "review_docs", "code_and_review"]
    assert [status.value for status in RunStatus] == [
        "queued",
        "running",
        "awaiting_approval",
        "completed",
        "failed",
        "rejected",
    ]
    assert CreateRunRequest(prompt="Test", repository_ref=None).repository_ref is None
    assert ApprovalRequest(decision="approved").feedback is None


@pytest.mark.parametrize("prompt", ["", "x" * 4001, 123, b"prompt"])
def test_invalid_prompts_are_rejected(prompt):
    with pytest.raises(ValidationError):
        CreateRunRequest(prompt=prompt)


@pytest.mark.parametrize("length", [1, 4000])
def test_prompt_length_boundaries(length):
    assert len(CreateRunRequest(prompt="x" * length).prompt) == length


def test_independent_collections_and_required_timestamps():
    now = datetime.now(timezone.utc)
    first = Run(run_id="run_first", status=RunStatus.QUEUED, created_at=now, updated_at=now)
    second = Run(run_id="run_second", status=RunStatus.QUEUED, created_at=now, updated_at=now)
    first.context_diffs["review"] = "changed"
    first.state_history.append(
        StepState(step_index=0, agent_name="coder", status="completed", output_summary="Done")
    )
    assert second.context_diffs == {}
    assert second.state_history == []
    assert first.diffs is not second.diffs
    assert first.current_step is None
    assert first.pending_checkpoint is None
    with pytest.raises(ValidationError):
        Run(run_id="missing_timestamps", status=RunStatus.QUEUED)


def test_utc_normalization_and_json_formatting():
    step = StepState(step_index=0, agent_name="coder", status="completed", output_summary="Done")
    assert step.created_at.tzinfo is timezone.utc
    payload = Run.model_json_schema()["examples"][0]
    payload["created_at"] = "2026-10-05T14:00:00+02:00"
    run = Run.model_validate(payload)
    assert run.created_at.tzinfo is timezone.utc
    assert run.model_dump(mode="json")["created_at"] == "2026-10-05T12:00:00Z"
    payload["created_at"] = "2026-10-05T12:00:00"
    with pytest.raises(ValidationError, match="timezone"):
        Run.model_validate(payload)


@pytest.mark.parametrize("index", [-1, True, "1", 1.5])
def test_step_index_is_strict_and_nonnegative(index):
    with pytest.raises(ValidationError):
        StepState(step_index=index, agent_name="coder", status="completed", output_summary="Done")


def test_checkpoint_action_validation():
    assert (
        Checkpoint(checkpoint_id="chk_test", description="Review").requires_action
        == "approve_reject"
    )
    with pytest.raises(ValidationError):
        Checkpoint(checkpoint_id="chk_test", description="Review", requires_action="anything")


@pytest.mark.parametrize("model", [CreateRunRequest, StepState, Checkpoint, Run, ApprovalRequest])
def test_population_by_name_is_enabled(model):
    assert model.model_config["populate_by_name"] is True
