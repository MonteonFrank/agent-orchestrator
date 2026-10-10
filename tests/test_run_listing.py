from fastapi.testclient import TestClient

from app.engine import OrchestratorEngine
from app.main import create_app
from app.schemas import CreateRunRequest


def test_run_listing_is_empty_then_newest_first():
    engine = OrchestratorEngine()
    with TestClient(create_app(engine)) as client:
        assert client.get("/v1/runs").json() == []
        first = engine.create_run(CreateRunRequest(prompt="First run"))
        second = engine.create_run(CreateRunRequest(prompt="Second run"))
        response = client.get("/v1/runs")
        assert response.status_code == 200
        assert [run["run_id"] for run in response.json()] == [second.run_id, first.run_id]
        assert all(run["status"] == "queued" for run in response.json())