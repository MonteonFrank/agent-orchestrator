"""Request and response contracts for agent orchestration."""

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone.")
    return value.astimezone(timezone.utc)


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]
StrictString = Annotated[str, Field(strict=True)]


class SchemaModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class TaskType(str, Enum):
    """Supported orchestration modes."""

    CODE = "code"
    REVIEW_DOCS = "review_docs"
    PIPELINE = "code_and_review"


class RunStatus(str, Enum):
    """Lifecycle states of an orchestration run."""

    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class CreateRunRequest(SchemaModel):
    prompt: StrictString = Field(
        min_length=1,
        max_length=4000,
        description="Instructions for the agents to execute.",
        examples=["Add pagination to the GET /users endpoint and cover it with tests."],
    )
    pipeline_mode: TaskType = Field(
        default=TaskType.PIPELINE,
        description="Orchestration mode: code, review_docs, or code_and_review.",
        examples=["code_and_review"],
    )
    repository_ref: StrictString | None = Field(
        default="main",
        description="Repository branch, tag, or commit to use.",
        examples=["main"],
    )
    target_files: list[StrictString] = Field(
        default_factory=list,
        description="Repository-relative files to focus on; an empty list leaves scope open.",
        examples=[["app/routes/users.py", "tests/test_users.py"]],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "prompt": "Add pagination to the GET /users endpoint and cover it with tests.",
                    "pipeline_mode": "code_and_review",
                    "repository_ref": "main",
                    "target_files": ["app/routes/users.py", "tests/test_users.py"],
                }
            ]
        }
    )


class FileDiff(SchemaModel):
    path: StrictString = Field(
        description="Repository-relative path of the changed file.",
        examples=["app/routes/users.py"],
    )
    status: Literal["modified", "added", "deleted"] = Field(
        description="Whether the file was modified, added, or deleted.",
        examples=["modified"],
    )
    patch: StrictString = Field(
        description="Unified diff describing the proposed file change.",
        examples=[
            "--- a/app/routes/users.py\n+++ b/app/routes/users.py\n@@ -1 +1 @@\n-def list_users():\n+def list_users(limit: int = 20):\n"
        ],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "path": "app/routes/users.py",
                    "status": "modified",
                    "patch": "--- a/app/routes/users.py\n+++ b/app/routes/users.py\n@@ -1 +1 @@\n-def list_users():\n+def list_users(limit: int = 20):\n",
                }
            ]
        }
    )


class StepState(SchemaModel):
    step_index: int = Field(
        strict=True, ge=0, description="Zero-based step position in the pipeline.", examples=[1]
    )
    agent_name: StrictString = Field(
        description="Name of the agent executing this step.", examples=["code_reviewer"]
    )
    status: StrictString = Field(
        description="Agent-reported execution status for this step.", examples=["completed"]
    )
    output_summary: StrictString = Field(
        description="Concise summary of the agent's results.",
        examples=["Reviewed pagination changes; all 12 tests passed."],
    )
    created_at: UtcDatetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the step was recorded.",
        examples=["2026-10-05T12:00:00Z"],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "step_index": 1,
                    "agent_name": "code_reviewer",
                    "status": "completed",
                    "output_summary": "Reviewed pagination changes; all 12 tests passed.",
                    "created_at": "2026-10-05T12:00:00Z",
                }
            ]
        }
    )


class Checkpoint(SchemaModel):
    checkpoint_id: StrictString = Field(
        description="Unique identifier for this approval checkpoint.", examples=["chk_7b42e91c"]
    )
    description: StrictString = Field(
        description="Context for the decision requested from the user.",
        examples=["Pagination implementation and tests are ready for review."],
    )
    requires_action: Literal["approve_reject", "edit_prompt"] = Field(
        default="approve_reject",
        description="Action the user needs to take before the run can continue.",
        examples=["approve_reject"],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "checkpoint_id": "chk_7b42e91c",
                    "description": "Pagination implementation and tests are ready for review.",
                    "requires_action": "approve_reject",
                }
            ]
        }
    )


class Run(SchemaModel):
    run_id: StrictString = Field(
        description="Unique identifier for the orchestration run.", examples=["run_8f3a2b19"]
    )
    status: RunStatus = Field(
        description="Current lifecycle state of the run.", examples=["awaiting_approval"]
    )
    current_step: StrictString | None = Field(
        default=None,
        description="Current step name, or null when no step is active.",
        examples=["review_changes", None],
    )
    state_history: list[StepState] = Field(
        default_factory=list, description="Ordered execution history of the run's steps."
    )
    pending_checkpoint: Checkpoint | None = Field(
        default=None,
        description="Checkpoint awaiting a user decision, or null if none is pending.",
    )
    diffs: list[FileDiff] = Field(
        default_factory=list, description="Proposed file changes produced by the agents."
    )
    context_diffs: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured changes to shared agent context.",
        examples=[{"documentation": {"summary": "Added pagination examples."}}],
    )
    created_at: UtcDatetime = Field(
        description="UTC timestamp when the run was created.",
        examples=["2026-10-05T12:00:00Z"],
    )
    updated_at: UtcDatetime = Field(
        description="UTC timestamp of the last run mutation.",
        examples=["2026-10-05T12:01:00Z"],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "run_id": "run_8f3a2b19",
                    "status": "awaiting_approval",
                    "current_step": "review_changes",
                    "state_history": [
                        {
                            "step_index": 0,
                            "agent_name": "coding_agent",
                            "status": "completed",
                            "output_summary": "Implemented pagination and added regression tests.",
                            "created_at": "2026-10-05T12:00:30Z",
                        }
                    ],
                    "pending_checkpoint": {
                        "checkpoint_id": "chk_7b42e91c",
                        "description": "Pagination implementation and tests are ready for review.",
                        "requires_action": "approve_reject",
                    },
                    "diffs": [
                        {
                            "path": "app/routes/users.py",
                            "status": "modified",
                            "patch": "--- a/app/routes/users.py\n+++ b/app/routes/users.py\n@@ -1 +1 @@\n-def list_users():\n+def list_users(limit: int = 20):\n",
                        }
                    ],
                    "context_diffs": {},
                    "created_at": "2026-10-05T12:00:00Z",
                    "updated_at": "2026-10-05T12:01:00Z",
                }
            ]
        }
    )


class ApprovalRequest(SchemaModel):
    decision: Literal["approved", "rejected", "request_changes"] = Field(
        description="User's decision about the pending checkpoint.",
        examples=["request_changes"],
    )
    feedback: StrictString | None = Field(
        default=None,
        description="Review feedback, or null when no additional feedback is provided.",
        examples=["Please cap the page size at 100 and test invalid limits.", None],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "decision": "request_changes",
                    "feedback": "Please cap the page size at 100 and test invalid limits.",
                }
            ]
        }
    )


class RunDiffsResponse(SchemaModel):
    run_id: str = Field(
        description="Identifier of the run that produced these proposals.",
        examples=["run_8f3a2b19"],
    )
    diffs: list[FileDiff] = Field(
        description="Current code and documentation file patch proposals."
    )
    context_diffs: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured agent outputs, findings, and revision feedback.",
        examples=[{"DocReviewer": {"findings": []}}],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "run_id": "run_8f3a2b19",
                    "diffs": [
                        {
                            "path": "docs/api.md",
                            "status": "added",
                            "patch": "--- /dev/null\n+++ b/docs/api.md\n@@ -0,0 +1 @@\n+# Pagination\n",
                        }
                    ],
                    "context_diffs": {"DocReviewer": {"findings": []}},
                }
            ]
        }
    )
