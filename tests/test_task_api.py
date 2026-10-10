import asyncio

import httpx

from app.engine import CodingAgent, OrchestratorEngine
from app.main import create_app


async def test_post_returns_before_pipeline_finishes_and_exposes_context():
    started = asyncio.Event()
    release = asyncio.Event()

    class WaitingCoder(CodingAgent):
        async def execute(self, prompt, context="", target_files=None):
            started.set()
            await release.wait()
            return await super().execute(prompt, context, target_files)

    application = create_app(OrchestratorEngine(coding_agent=WaitingCoder()))
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            response = await asyncio.wait_for(client.post("/v1/runs", json={"prompt": "Test"}), 1)
            assert response.status_code == 202
            assert response.json()["status"] == "queued"
            run_id = response.json()["run_id"]
            await asyncio.wait_for(started.wait(), 1)
            assert (await client.get(f"/v1/runs/{run_id}")).json()["status"] == "running"
            release.set()
            await asyncio.gather(*application.state.pipeline_tasks)
            state = (await client.get(f"/v1/runs/{run_id}")).json()
            assert state["status"] == "awaiting_approval"
            diffs = (await client.get(f"/v1/runs/{run_id}/diffs")).json()
            assert set(diffs) == {"run_id", "diffs", "context_diffs"}
            assert diffs["context_diffs"] == state["context_diffs"]
            assert (await client.get("/healthz")).json() == {"status": "ok"}
            assert (await client.get("/openapi.json")).json()["info"]["version"] == "1.0.0"


async def test_shutdown_cancels_pipeline_tasks():
    class WaitingCoder(CodingAgent):
        async def execute(self, prompt, context="", target_files=None):
            await asyncio.Event().wait()

    application = create_app(OrchestratorEngine(coding_agent=WaitingCoder()))
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            await client.post("/v1/runs", json={"prompt": "Test"})
            await asyncio.sleep(0)
            tasks = list(application.state.pipeline_tasks)
    assert tasks and all(task.cancelled() for task in tasks)
