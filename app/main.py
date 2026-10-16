"""FastAPI routes for in-memory agent runs and approval checkpoints."""

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app.engine import OrchestratorEngine, orchestrator as default_orchestrator
from app.schemas import ApprovalRequest, CreateRunRequest, Run, RunDiffsResponse, RunStatus


def create_app(engine: OrchestratorEngine | None = None) -> FastAPI:
    tasks: set[asyncio.Task[Run]] = set()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        yield
        for task in tuple(tasks):
            task.cancel()
        await asyncio.gather(*tuple(tasks), return_exceptions=True)

    application = FastAPI(
        title="Agent Orchestrator API",
        description="Agent runs, proposed file diffs, and human approval checkpoints.",
        version="1.0.0",
        lifespan=lifespan,
    )
    orchestrator = engine if engine is not None else OrchestratorEngine()
    application.state.engine = orchestrator
    application.state.pipeline_tasks = tasks
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def task_finished(task: asyncio.Task[Run]) -> None:
        tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logging.getLogger(__name__).error(
                "Pipeline task failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    def schedule_pipeline(run_id: str) -> None:
        task = asyncio.create_task(orchestrator.run_pipeline(run_id))
        tasks.add(task)
        task.add_done_callback(task_finished)

    def find_run(run_id: str) -> Run:
        if run_id not in orchestrator.runs:
            raise HTTPException(status_code=404, detail="Run not found")
        return orchestrator.runs[run_id]

    not_found_response = {
        404: {
            "description": "Run not found",
            "content": {
                "application/json": {
                    "example": {"detail": "Run not found"},
                }
            },
        },
    }

    @application.get("/health", tags=["health"], include_in_schema=False)
    @application.get(
        "/healthz", tags=["health"], responses={200: {"description": "API is healthy"}}
    )
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post(
        "/v1/runs",
        response_model=Run,
        status_code=status.HTTP_202_ACCEPTED,
        responses={202: {"description": "Run queued for asynchronous execution"}},
        tags=["runs"],
    )
    async def create_run(request: CreateRunRequest) -> Run:
        run = orchestrator.create_run(request)
        snapshot = run.model_copy(deep=True)
        schedule_pipeline(run.run_id)
        return snapshot

    @application.get(
        "/v1/runs",
        response_model=list[Run],
        responses={200: {"description": "In-memory runs ordered newest first"}},
        tags=["runs"],
    )
    async def list_runs() -> list[Run]:
        return [
            run.model_copy(deep=True)
            for run in sorted(
                orchestrator.runs.values(), key=lambda run: run.created_at, reverse=True
            )
        ]

    @application.get(
        "/v1/runs/{run_id}",
        response_model=Run,
        responses={**not_found_response, 200: {"description": "Current run state"}},
        tags=["runs"],
    )
    async def get_run(run_id: str) -> Run:
        return find_run(run_id).model_copy(deep=True)

    @application.get(
        "/v1/runs/{run_id}/diffs",
        response_model=RunDiffsResponse,
        responses={
            **not_found_response,
            200: {"description": "File proposals and structured context changes"},
        },
        tags=["runs"],
    )
    async def get_diffs(run_id: str) -> RunDiffsResponse:
        run = find_run(run_id).model_copy(deep=True)
        return RunDiffsResponse(
            run_id=run.run_id,
            diffs=run.diffs,
            context_diffs=run.context_diffs,
        )

    @application.post(
        "/v1/runs/{run_id}/approvals/{checkpoint_id}",
        response_model=Run,
        responses={
            **not_found_response,
            200: {"description": "Decision processed and updated run returned"},
            400: {
                "description": "Invalid checkpoint ID or run is not awaiting approval",
                "content": {"application/json": {"example": {"detail": "Invalid checkpoint ID"}}},
            },
        },
        tags=["approvals"],
    )
    async def submit_approval(
        run_id: str,
        checkpoint_id: str,
        approval: ApprovalRequest,
    ) -> Run:
        run = find_run(run_id)
        if run.status != RunStatus.AWAITING_APPROVAL or run.pending_checkpoint is None:
            raise HTTPException(status_code=400, detail="Run is not awaiting approval")
        if run.pending_checkpoint.checkpoint_id != checkpoint_id:
            raise HTTPException(status_code=400, detail="Invalid checkpoint ID")
        try:
            if orchestrator.write_on_approval and approval.decision == "approved":
                await asyncio.to_thread(orchestrator.submit_approval, run_id, checkpoint_id, approval)
            else:
                orchestrator.submit_approval(run_id, checkpoint_id, approval)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        snapshot = run.model_copy(deep=True)
        if run.status == RunStatus.RUNNING:
            schedule_pipeline(run_id)
        return snapshot

    return application


app = create_app(default_orchestrator)


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
