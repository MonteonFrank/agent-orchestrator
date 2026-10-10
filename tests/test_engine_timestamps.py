from datetime import timezone

import pytest

from app.engine import OrchestratorEngine
from app.schemas import ApprovalRequest, CreateRunRequest


@pytest.mark.parametrize("decision", ["approved", "rejected", "request_changes"])
async def test_engine_maintains_utc_timestamps(decision):
    engine = OrchestratorEngine()
    run = engine.create_run(CreateRunRequest(prompt="Add pagination."))
    created_at = run.created_at
    assert run.updated_at == created_at
    assert created_at.tzinfo is timezone.utc
    await engine.step_run(run.run_id)
    first_update = run.updated_at
    assert first_update >= created_at
    await engine.step_run(run.run_id)
    assert run.updated_at >= first_update
    assert all(step.created_at.tzinfo is timezone.utc for step in run.state_history)
    assert run.pending_checkpoint.requires_action == "approve_reject"
    checkpoint_update = run.updated_at
    engine.submit_approval(
        run.run_id,
        run.pending_checkpoint.checkpoint_id,
        ApprovalRequest(decision=decision),
    )
    assert run.updated_at >= checkpoint_update
    assert run.created_at == created_at
