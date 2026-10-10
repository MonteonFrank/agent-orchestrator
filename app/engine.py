"""Async, mode-aware orchestration with Gemini and offline proposal-only agents."""

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import unified_diff
from typing import Any, Literal
from uuid import uuid4

from google import genai
from pydantic import BaseModel, Field, StrictStr

from app.schemas import (
    ApprovalRequest,
    Checkpoint,
    CreateRunRequest,
    FileDiff,
    Run,
    RunStatus,
    StepState,
    TaskType,
)


class BaseAgent(ABC):
    agent_name: str

    @abstractmethod
    async def execute(
        self,
        prompt: str,
        context: str = "",
        target_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return diffs and an output summary for an agent step."""
        raise NotImplementedError


def _parse_context(context: str) -> dict[str, Any]:
    if not context:
        return {}
    try:
        parsed = json.loads(context)
    except json.JSONDecodeError:
        return {"code_context": context}
    if isinstance(parsed, list):
        return {"diffs": parsed}
    if isinstance(parsed, dict):
        return parsed
    return {"code_context": context}


def _make_patch(path: str, original: str, proposed: str, status: str) -> str:
    return "".join(
        unified_diff(
            original.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile="/dev/null" if status == "added" else f"a/{path}",
            tofile="/dev/null" if status == "deleted" else f"b/{path}",
        )
    )


class _FileProposal(BaseModel):
    path: StrictStr = Field(min_length=1)
    status: Literal["modified", "added", "deleted"]
    original: StrictStr
    proposed: StrictStr


class _AgentProposal(BaseModel):
    output_summary: StrictStr = Field(min_length=1)
    files: list[_FileProposal]


class _ReviewProposal(_AgentProposal):
    feedback: StrictStr = Field(min_length=1)
    findings: list[dict[str, StrictStr]] = Field(default_factory=list)


async def _generate_proposal(
    system_instruction: str,
    prompt: str,
    details: dict[str, Any],
    target_files: list[str],
    review: bool = False,
) -> dict[str, Any] | None:
    if not os.environ.get("GEMINI_API_KEY"):
        return None
    proposal_type = _ReviewProposal if review else _AgentProposal
    try:
        client = genai.Client()
        async with client.aio:
            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=json.dumps({
                    "task": prompt,
                    "context": details,
                    "target_files": target_files,
                    "output_schema": proposal_type.model_json_schema(),
                    "requirements": (
                        "Return JSON matching output_schema. Include complete original and "
                        "proposed file contents, not patches. Use only supplied context; "
                        "do not claim to have read repository files. For added files original "
                        "must be empty; for deleted files proposed must be empty. "
                        "For documentation review, propose only documentation changes."
                    ),
                }),
                config={
                    "system_instruction": system_instruction,
                    "response_mime_type": "application/json",
                },
            )
        proposal = proposal_type.model_validate_json(response.text or "")
        diffs = []
        for file in proposal.files:
            if (file.status == "added" and file.original) or (
                file.status == "deleted" and file.proposed
            ):
                raise ValueError("File contents do not match the proposed status")
            patch = _make_patch(file.path, file.original, file.proposed, file.status)
            if not patch:
                raise ValueError("Proposed file has no changes")
            diffs.append(FileDiff(path=file.path, status=file.status, patch=patch))
        result = {"diffs": diffs, "output_summary": proposal.output_summary}
        if isinstance(proposal, _ReviewProposal):
            result.update(feedback=proposal.feedback, findings=proposal.findings)
        else:
            result["code_outputs"] = {
                file.path: file.proposed for file in proposal.files if file.status != "deleted"
            }
        return result
    except Exception:
        logging.getLogger(__name__).exception(
            "Gemini proposal failed; using offline mock fallback.", exc_info=False
        )
        return None


class CodingAgent(BaseAgent):
    def __init__(self) -> None:
        self.agent_name = "Coder"

    async def execute(
        self,
        prompt: str,
        context: str = "",
        target_files: list[str] | None = None,
    ) -> dict[str, Any]:
        details = _parse_context(context)
        targets = target_files if target_files is not None else details.get("target_files", [])
        result = await _generate_proposal(
            "You are an expert Software Engineer. Implement coding tasks and return structured outputs.",
            prompt,
            details,
            targets,
        )
        if result is not None:
            return result
        original = details.get("code_context", "")
        feedback = details.get("feedback")
        instruction = prompt if not feedback else f"{prompt}\nRequested changes: {feedback}"
        code = (
            '"""Mock implementation proposal; not production code."""\n\n'
            "def generated_task():\n"
            f"    return {instruction!r}\n"
        )
        status = "modified" if targets or original else "added"
        diffs = [
            FileDiff(path=path, status=status, patch=_make_patch(path, original, code, status))
            for path in dict.fromkeys(targets or ["app/generated_task.py"])
        ]
        return {
            "diffs": diffs,
            "code_outputs": {diff.path: code for diff in diffs},
            "output_summary": f"Generated {len(diffs)} mock code proposal(s).",
        }


class DocReviewerAgent(BaseAgent):
    def __init__(self) -> None:
        self.agent_name = "DocReviewer"

    async def execute(
        self,
        prompt: str,
        context: str = "",
        target_files: list[str] | None = None,
    ) -> dict[str, Any]:
        details = _parse_context(context)
        result = await _generate_proposal(
            "You are an expert Technical Writer and API Reviewer. Review documentation and specs against code context.",
            prompt,
            details,
            target_files if target_files is not None else details.get("target_files", []),
            review=True,
        )
        if result is not None:
            return result
        diffs = [FileDiff.model_validate(diff) for diff in details.get("diffs", [])]
        findings = [
            {
                "path": diff.path,
                "category": "documentation",
                "message": f"Document the {diff.status} behavior and verify affected OpenAPI examples.",
            }
            for diff in diffs
        ]
        changes = "\n".join(f"- `{diff.path}`: {diff.status}" for diff in diffs)
        markdown = (
            "# Documentation Review\n\n"
            f"Requested change: {prompt}\n\n"
            "## Proposed Code Changes\n\n"
            f"{changes or 'No code diffs were supplied.'}\n\n"
            "## Documentation and OpenAPI Follow-up\n\n"
            "- Document changed behavior, inputs, outputs, and error cases.\n"
            "- Verify affected OpenAPI operations, schemas, and examples.\n"
            "- Mock review only: repository documents and specs have not been inspected.\n"
        )
        if details.get("feedback"):
            markdown += f"\n## Requested Revisions\n\n{details['feedback']}\n"
        docs = [path for path in target_files or [] if path.endswith(".md")]
        path = docs[0] if docs else "docs/orchestrator-review.md"
        status = "modified" if docs else "added"
        return {
            "feedback": markdown,
            "findings": findings,
            "diffs": [
                FileDiff(path=path, status=status, patch=_make_patch(path, "", markdown, status))
            ],
            "output_summary": markdown,
        }


@dataclass
class _ExecutionState:
    request: CreateRunRequest
    next_step: int = 0
    context: dict[str, Any] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class OrchestratorEngine:
    """Single-event-loop manager; proposals are never applied to the filesystem."""

    def __init__(
        self,
        coding_agent: BaseAgent | None = None,
        doc_reviewer_agent: BaseAgent | None = None,
    ) -> None:
        self.runs: dict[str, Run] = {}
        self._states: dict[str, _ExecutionState] = {}
        self.coding_agent = coding_agent if coding_agent is not None else CodingAgent()
        self.doc_reviewer_agent = (
            doc_reviewer_agent if doc_reviewer_agent is not None else DocReviewerAgent()
        )

    def _pipeline(self, state: _ExecutionState) -> tuple[BaseAgent, ...]:
        if state.request.pipeline_mode == TaskType.CODE:
            return (self.coding_agent,)
        if state.request.pipeline_mode == TaskType.REVIEW_DOCS:
            return (self.doc_reviewer_agent,)
        return (self.coding_agent, self.doc_reviewer_agent)

    def create_run(self, request: CreateRunRequest) -> Run:
        now = datetime.now(timezone.utc)
        run = Run(
            run_id=f"run_{uuid4().hex}",
            status=RunStatus.QUEUED,
            current_step=None,
            state_history=[],
            pending_checkpoint=None,
            diffs=[],
            created_at=now,
            updated_at=now,
        )
        self.runs[run.run_id] = run
        self._states[run.run_id] = _ExecutionState(request=request.model_copy(deep=True))
        return run

    async def run_pipeline(self, run_id: str) -> Run:
        state = self._states[run_id]
        async with state.lock:
            run = self.runs[run_id]
            while run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
                await self._execute_step(run, state)
            return run

    async def step_run(self, run_id: str) -> Run:
        """Execute a single selected agent for callers needing explicit stepping."""
        state = self._states[run_id]
        async with state.lock:
            run = self.runs[run_id]
            if run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
                await self._execute_step(run, state)
            return run

    async def _execute_step(self, run: Run, state: _ExecutionState) -> None:
        pipeline = self._pipeline(state)
        agent = pipeline[state.next_step]
        run.status = RunStatus.RUNNING
        run.current_step = agent.agent_name
        run.updated_at = datetime.now(timezone.utc)
        context = json.dumps(
            {
                **state.context,
                "target_files": state.request.target_files,
                "pipeline_mode": state.request.pipeline_mode.value,
                "repository_ref": state.request.repository_ref,
            }
        )
        try:
            result = await agent.execute(
                state.request.prompt, context, list(state.request.target_files)
            )
            diffs = [FileDiff.model_validate(diff) for diff in result["diffs"]]
            step = StepState(
                step_index=len(run.state_history),
                agent_name=agent.agent_name,
                status="completed",
                output_summary=result["output_summary"],
            )
            output_context = {
                key: value
                for key, value in result.items()
                if key not in ("diffs", "output_summary")
            }
            json.dumps(output_context)
        except asyncio.CancelledError:
            self._record_failure(run, agent, "Execution cancelled.")
            raise
        except Exception as error:
            self._record_failure(run, agent, f"{type(error).__name__}: {error}")
            return
        if state.next_step == 0:
            run.diffs = diffs
        else:
            run.diffs.extend(diffs)
        run.state_history.append(step)
        state.context[agent.agent_name] = output_context
        if agent is self.coding_agent:
            state.context["code_outputs"] = output_context.get("code_outputs", {})
        state.context["diffs"] = [diff.model_dump(mode="json") for diff in run.diffs]
        run.context_diffs = deepcopy(
            {key: value for key, value in state.context.items() if key != "diffs"}
        )
        state.next_step += 1
        if state.next_step == len(pipeline):
            run.status = RunStatus.AWAITING_APPROVAL
            run.current_step = None
            run.pending_checkpoint = Checkpoint(
                checkpoint_id=f"chk_{uuid4().hex}",
                description="Agent proposals are ready for review.",
                requires_action="approve_reject",
            )
        else:
            run.current_step = pipeline[state.next_step].agent_name
        run.updated_at = datetime.now(timezone.utc)

    def _record_failure(self, run: Run, agent: BaseAgent, summary: str) -> None:
        run.state_history.append(
            StepState(
                step_index=len(run.state_history),
                agent_name=agent.agent_name,
                status="failed",
                output_summary=summary,
            )
        )
        run.status = RunStatus.FAILED
        run.current_step = None
        run.pending_checkpoint = None
        run.updated_at = datetime.now(timezone.utc)

    def submit_approval(
        self,
        run_id: str,
        checkpoint_id: str,
        approval: ApprovalRequest,
    ) -> Run:
        run = self.runs[run_id]
        if run.status != RunStatus.AWAITING_APPROVAL or run.pending_checkpoint is None:
            raise ValueError("Run is not awaiting approval.")
        if run.pending_checkpoint.checkpoint_id != checkpoint_id:
            raise ValueError("Checkpoint ID does not match the pending checkpoint.")
        run.pending_checkpoint = None
        if approval.decision == "request_changes":
            state = self._states[run_id]
            state.next_step = 0
            feedback = [
                *state.context.get("feedback", []),
                approval.feedback or "Changes requested.",
            ]
            state.context = {"feedback": feedback}
            run.context_diffs = {"feedback": list(feedback)}
            run.status = RunStatus.RUNNING
            run.current_step = self._pipeline(state)[0].agent_name
        else:
            run.status = (
                RunStatus.COMPLETED if approval.decision == "approved" else RunStatus.REJECTED
            )
            run.current_step = None
        run.updated_at = datetime.now(timezone.utc)
        return run


orchestrator = OrchestratorEngine()
