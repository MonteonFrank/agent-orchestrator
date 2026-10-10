import json

import httpx
import pytest
from typer.testing import CliRunner

from cli import orch

runner = CliRunner()


def run_payload(status="queued", checkpoint=None):
    return {
        "run_id": "run_test",
        "status": status,
        "current_step": None,
        "state_history": [],
        "pending_checkpoint": checkpoint,
        "diffs": [],
        "created_at": "2026-10-05T12:00:00Z",
        "updated_at": "2026-10-05T12:00:00Z",
    }


def checkpoint_payload():
    return {
        "checkpoint_id": "chk_test",
        "description": "Review proposals.",
        "requires_action": "approve_reject",
    }


def diff_payload():
    return {
        "run_id": "run_test",
        "context_diffs": {},
        "diffs": [
            {
                "path": "app/users.py",
                "status": "modified",
                "patch": "--- a/app/users.py\n+++ b/app/users.py\n@@ -1 +1 @@\n-old\n+new\n",
            }
        ],
    }


def mock_api(monkeypatch, handler):
    monkeypatch.setattr(
        orch,
        "_api_client",
        lambda context: httpx.Client(
            base_url=context.obj,
            transport=httpx.MockTransport(handler),
        ),
    )
    monkeypatch.setattr(orch.time, "sleep", lambda interval: None)


@pytest.mark.parametrize(
    "mode,pipeline",
    [("code", "code"), ("review_docs", "review_docs"), ("code_and_review", "code_and_review")],
)
def test_detached_run_sends_mode_and_repeated_files(monkeypatch, mode, pipeline):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/v1/runs"
        assert json.loads(request.content) == {
            "prompt": "Add pagination.",
            "pipeline_mode": pipeline,
            "repository_ref": "main",
            "target_files": ["app/users.py", "tests/test_users.py"],
        }
        return httpx.Response(202, json=run_payload())

    mock_api(monkeypatch, handler)
    result = runner.invoke(
        orch.app,
        [
            "run",
            "Add pagination.",
            "--mode",
            mode,
            "--file",
            "app/users.py",
            "--file",
            "tests/test_users.py",
            "--detach",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "run_test: queued" in result.output
    assert len(requests) == 1


def test_default_run_polls_until_approval(monkeypatch):
    states = iter([run_payload("running"), run_payload("awaiting_approval", checkpoint_payload())])
    requests = []

    def handler(request):
        requests.append(request.method)
        if request.method == "POST":
            assert json.loads(request.content)["pipeline_mode"] == "code_and_review"
            return httpx.Response(202, json=run_payload())
        return httpx.Response(200, json=next(states))

    mock_api(monkeypatch, handler)
    result = runner.invoke(orch.app, ["run", "Add pagination."])
    assert result.exit_code == 0, result.output
    for state in ["queued", "running", "awaiting_approval"]:
        assert state in result.output
    assert "orch review run_test" in result.output
    assert "Run Summary" in result.output
    assert requests == ["POST", "GET", "GET"]


@pytest.mark.parametrize("status,exit_code", [("completed", 0), ("failed", 1), ("rejected", 1)])
def test_run_stops_at_terminal_status(monkeypatch, status, exit_code):
    def handler(request):
        return httpx.Response(
            202 if request.method == "POST" else 200,
            json=run_payload("queued" if request.method == "POST" else status),
        )

    mock_api(monkeypatch, handler)
    result = runner.invoke(orch.app, ["run", "Test"])
    assert result.exit_code == exit_code
    assert status in result.output


def test_diff_displays_git_style_patch(monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/runs/run_test/diffs"
        return httpx.Response(200, json=diff_payload())

    mock_api(monkeypatch, handler)
    result = runner.invoke(orch.app, ["diff", "run_test"])
    assert result.exit_code == 0, result.output
    assert "modified: app/users.py" in result.output
    assert "--- a/app/users.py" in result.output
    assert "+new" in result.output


def test_diff_handles_empty_proposals(monkeypatch):
    mock_api(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"run_id": "run_test", "diffs": [], "context_diffs": {}}
        ),
    )
    result = runner.invoke(orch.app, ["diff", "run_test"])
    assert result.exit_code == 0
    assert "No file diffs" in result.output


@pytest.mark.parametrize(
    "choice,decision,feedback,status",
    [
        ("A\n", "approved", None, "completed"),
        ("r\n", "rejected", None, "rejected"),
        ("C\nCap page size at 100.\n", "request_changes", "Cap page size at 100.", "running"),
        ("invalid\na\n", "approved", None, "completed"),
    ],
)
def test_review_displays_outputs_and_submits_decision(
    monkeypatch, choice, decision, feedback, status
):
    submissions = []

    def handler(request):
        if request.method == "POST":
            assert request.url.path == "/v1/runs/run_test/approvals/chk_test"
            submissions.append(json.loads(request.content))
            return httpx.Response(200, json=run_payload(status))
        if request.url.path.endswith("/diffs"):
            return httpx.Response(200, json=diff_payload())
        payload = run_payload("awaiting_approval", checkpoint_payload())
        payload["state_history"] = [
            {
                "step_index": 0,
                "agent_name": "coding_agent",
                "status": "completed",
                "output_summary": "Generated pagination changes.",
            }
        ]
        return httpx.Response(200, json=payload)

    mock_api(monkeypatch, handler)
    result = runner.invoke(orch.app, ["review", "run_test"], input=choice)
    assert result.exit_code == 0, result.output
    assert "Generated pagination changes." in result.output
    assert "--- a/app/users.py" in result.output
    assert "Review proposals." in result.output
    assert status in result.output
    assert submissions == [{"decision": decision, "feedback": feedback}]


def test_review_requires_pending_checkpoint(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=run_payload("running"))

    mock_api(monkeypatch, handler)
    result = runner.invoke(orch.app, ["review", "run_test"])
    assert result.exit_code == 1
    assert "not awaiting approval" in result.output
    assert len(requests) == 1


def test_cancelled_review_does_not_submit(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request.method)
        return httpx.Response(
            200,
            json=diff_payload()
            if request.url.path.endswith("/diffs")
            else run_payload("awaiting_approval", checkpoint_payload()),
        )

    mock_api(monkeypatch, handler)
    result = runner.invoke(orch.app, ["review", "run_test"], input="")
    assert result.exit_code == 1
    assert "POST" not in requests


@pytest.mark.parametrize(
    "code,detail", [(404, "Run not found"), (400, "Invalid checkpoint ID"), (500, "Server error")]
)
def test_http_errors_are_actionable(monkeypatch, code, detail):
    mock_api(monkeypatch, lambda request: httpx.Response(code, json={"detail": detail}))
    result = runner.invoke(orch.app, ["diff", "missing"])
    assert result.exit_code == 1
    assert f"HTTP {code}: {detail}" in result.output
    assert "Traceback" not in result.output


def test_connection_error_is_actionable(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    mock_api(monkeypatch, handler)
    result = runner.invoke(orch.app, ["run", "Test", "--detach"])
    assert result.exit_code == 1
    assert "Cannot reach the API" in result.output


@pytest.mark.parametrize(
    "response",
    [httpx.Response(200, text="not JSON"), httpx.Response(200, json={"unexpected": True})],
)
def test_invalid_response_is_actionable(monkeypatch, response):
    mock_api(monkeypatch, lambda request: response)
    result = runner.invoke(orch.app, ["diff", "run_test"])
    assert result.exit_code == 1
    assert "invalid response" in result.output


def test_api_url_configuration(monkeypatch):
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, json=diff_payload())

    mock_api(monkeypatch, handler)
    monkeypatch.setenv("ORCH_API_URL", "http://api.example:9000")
    assert runner.invoke(orch.app, ["diff", "run_test"]).exit_code == 0
    assert (
        runner.invoke(
            orch.app, ["--api-url", "http://override.example:8001", "diff", "run_test"]
        ).exit_code
        == 0
    )
    assert urls == [
        "http://api.example:9000/v1/runs/run_test/diffs",
        "http://override.example:8001/v1/runs/run_test/diffs",
    ]


def test_invalid_mode_is_rejected():
    result = runner.invoke(orch.app, ["run", "Test", "--mode", "unknown"])
    assert result.exit_code == 2
    assert "Invalid value" in result.output


def test_short_flags_and_default_server(monkeypatch):
    monkeypatch.delenv("ORCH_API_URL", raising=False)

    def handler(request):
        assert str(request.url) == "http://localhost:8000/v1/runs"
        payload = json.loads(request.content)
        assert payload["pipeline_mode"] == "review_docs"
        assert payload["target_files"] == ["README.md", "docs/api.md"]
        return httpx.Response(202, json=run_payload())

    mock_api(monkeypatch, handler)
    result = runner.invoke(
        orch.app,
        [
            "run",
            "Review documentation.",
            "-m",
            "review_docs",
            "-f",
            "README.md",
            "-f",
            "docs/api.md",
            "--detach",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Run Summary" in result.output
    assert "Run ID" in result.output


@pytest.mark.parametrize("prompt", ["", "x" * 4001])
def test_invalid_prompt_has_clean_error(prompt):
    result = runner.invoke(orch.app, ["run", prompt, "--detach"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Traceback" not in result.output


def test_summary_renders_history_and_checkpoint(monkeypatch):
    payload = run_payload("awaiting_approval", checkpoint_payload())
    payload["state_history"] = [
        {
            "step_index": 0,
            "agent_name": "Coder",
            "status": "completed",
            "output_summary": "Generated code changes.",
        }
    ]
    mock_api(monkeypatch, lambda request: httpx.Response(202, json=payload))
    result = runner.invoke(orch.app, ["run", "Test", "--detach"])
    assert result.exit_code == 0, result.output
    for label in [
        "Run Summary",
        "Agent State History",
        "Coder",
        "chk_test",
        "Generated code changes.",
    ]:
        assert label in result.output
