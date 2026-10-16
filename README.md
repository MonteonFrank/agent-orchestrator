# Agent Orchestrator API

The `agent-orchestrator` package uses FastAPI, Pydantic v2, and Uvicorn on
Python 3.11+. Its container runs Python 3.11; Ruff targets Python 3.11 syntax.

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e '.[dev]'
```

`requirements.txt` includes runtime and test dependencies; the `dev` extra also
installs Ruff. Keep shared dependency ranges aligned with `pyproject.toml`.
Pydantic Settings, LangGraph, LangChain Core, and OpenAI are installed for future
configuration and agent integrations. Coding and documentation agents use the
official `google-genai` SDK with `gemini-3.8-flash` when `GEMINI_API_KEY` is set
in the backend process's environment. Set the key in your terminal before
starting Uvicorn; a local `.env` file is not loaded automatically. No OpenAI key
is required.

Gemini receives the task, target paths, supplied code/diff context, and revision
feedback. Responses are validated as structured JSON and converted to unified
file diffs. Default engines do not read repository files automatically or apply
changes; workspace access and approved writes are explicit opt-ins below. Agents
never create commits. Missing Gemini credentials, API failures, and invalid model
outputs fall back to deterministic mock proposals. Terminal debug messages identify
successful Gemini use, missing keys, and exception details; the configured API key
is redacted from exception messages. Error details may include model output, so
keep backend logs private. Review the outputs before approving them.

Gemini calls that fail with status 503 or an `UNAVAILABLE` message are retried
up to three times after asynchronous waits of 2, 4, and 8 seconds (four attempts
total). Each retry is reported in the backend terminal. Exhausted retries and
non-transient errors use the existing mock fallback.
Each synchronous model attempt runs in a worker thread via `asyncio.to_thread`,
with a 60-second `asyncio.wait_for` timeout. A timeout stops waiting and uses the
mock fallback without retrying; it cannot stop the underlying worker thread,
which may continue until the SDK request finishes. Backoff waits do not block
the event loop. Successful or fallback proposals pause at `awaiting_approval`;
approval is still required to mark a run `completed`.

To run tests without making real Gemini requests, even if your shell has a key:

```sh
env -u GEMINI_API_KEY python -m pytest
```

## Workspace Solutions

Configure a trusted workspace explicitly when constructing the API or engine:

```python
from app.engine import OrchestratorEngine
from app.main import create_app

engine = OrchestratorEngine(
	workspace_root="/absolute/path/to/your/project",
	write_on_approval=True,
)
application = create_app(engine)
```

Serve this configured application instead of the default `app.main:app`.
Omit `write_on_approval` for workspace-aware proposals without disk writes.
The existing default API remains proposal-only for compatibility and safety.

Workspace-enabled coding first requests a Solution Blueprint containing
architecture, file paths/purposes, and dependencies. The blueprint is available
in `run.context_diffs["solution_blueprint"]`. Planning occurs within the Coder
step before implementation, preserving the existing step-history contract.
`code_outputs` maps every planned relative path to complete source contents.
Diffs are rebuilt against the actual workspace snapshot, not model-supplied
originals. Revision rounds rescan the workspace and preserve feedback.

Each workspace Solution Blueprint also produces `docs/Solution_Blueprint.docx`
using `python-docx`. The styled report contains a title, architecture overview,
component/API specifications, data models, a file-path/purpose table, and
dependencies. Markdown headings, lists, tables, inline code, and fenced code
blocks are rendered as Word formatting. The architect supplies Markdown in the
structured blueprint's `architecture`, `component_api_specs`, and `data_models`
fields; the document builder also accepts standalone JSON or Markdown reports.
Missing optional specifications are marked "Not specified."

The Word file appears in the CLI/API file diffs as a binary-document summary;
its complete bytes are base64-encoded under
`run.context_diffs["blueprint_documents"]["docs/Solution_Blueprint.docx"]`
with `encoding` and `content` fields. It is separate from `code_outputs` and
the architect's source-file plan. No document is written until human approval
with `write_on_approval=True`. Existing Word files are snapshotted as bytes,
and approval rejects external edits or unsafe paths before writing any files.
Rejection and proposal-only mode never write the document.

The context scanner inventories up to 1,000 source/documentation paths and reads
up to 80 UTF-8 files, with a 50 KB per-file and 200 KB total limit. Explicit
target paths are prioritized. Hidden paths, dependency/build folders, symlinks,
environment files, credential-named files, and common key-file extensions are
excluded. Oversized/unreadable target files and unsafe paths fail the run.
These exclusions are not a complete secret detector: use a sanitized workspace.
Selected source contents are sent to Gemini when a key is configured, so ensure
that your repository's confidentiality policy permits this.

The DocReviewer receives the blueprint and all generated contents. File coverage
and Python syntax are checked before approval; Gemini reviews architecture,
documentation, and OpenAPI consistency. Workspace-enabled `code` mode includes
this review gate too. These checks do not execute tests, install dependencies,
or guarantee architectural correctness. Offline fallback preserves existing
modules, adds clearly labeled task metadata, and generates an implementation
proposal plus a smoke-test file for an unscoped new solution. It is not a real
implementation of arbitrary tasks.

Nothing is written before approval. With writes enabled, approval validates
relative paths and rejects symlinks, traversal, changed existing files, and
code/documentation overlaps. Files are staged beside their destinations and
replaced atomically per file, with best-effort rollback on an interrupted batch.
The writer creates parent directories but does not delete files, install
dependencies, execute generated code, or make Git commits. Multi-file writes
are not a filesystem transaction, and concurrent hostile filesystem mutation
is not supported. Use an isolated worktree and backups. Approval I/O runs in a
worker thread in the API. Keep this API authenticated and private before enabling
workspace writes; its default CORS policy is intended only for local development.

## Container

Start Docker before building. The image installs all requirements, copies the
API, CLI, and tests, and installs the package editably so `orch` is on `PATH`.

```sh
docker build -t agent-orchestrator:0.1.0 .
docker run --rm -p 8000:8000 agent-orchestrator:0.1.0
```

Use port `8001:8000` if a local API already occupies port 8000. Test the image or
inspect the installed CLI without starting another server:

```sh
docker run --rm agent-orchestrator:0.1.0 python -m pytest
docker run --rm agent-orchestrator:0.1.0 orch --help
```

For an interactive CLI session against a running container, replace
`CONTAINER_ID` with that container's ID:

```sh
docker exec -it CONTAINER_ID orch run "Add pagination."
```

The default container command uses one Uvicorn worker on `0.0.0.0:8000`.
Runs remain in memory and are lost when the container stops. Local `.env` files
are excluded from the build; pass configuration with `docker run --env-file .env`
when needed rather than baking credentials into the image.

## Run

```sh
python -m uvicorn app.main:app --reload --reload-dir app
```

The API runs at http://127.0.0.1:8000, with interactive documentation at
http://127.0.0.1:8000/docs and a health check at `/healthz`. `/health` remains
an undocumented compatibility alias. Run `python -m app.main` to launch Uvicorn
on `0.0.0.0:8000` with reload enabled.

VS Code also provides the `Run Agent Orchestrator API` task. Stop the running
server with Ctrl+C in its task terminal.

## CLI

The editable install registers `orch` in the virtual environment. With `.venv`
activated and the API running:

```sh
orch run "Add pagination." --mode code_and_review --file app/users.py --file tests/test_users.py
orch run "Review the generated proposal." --detach
orch diff RUN_ID
orch review RUN_ID
```

`run` displays status changes with a Rich live spinner until the run completes,
fails, is rejected, or pauses for approval. `--detach` prints the run ID and
initial status without polling. Failed and rejected runs produce exit code 1.
`--mode` / `-m` accepts `code`, `review_docs`, or `code_and_review` (default).
These run Coder, DocReviewer, or both sequentially. Repeat `--file` / `-f` to
scope target files. Run summaries use Rich panels and agent-history tables.

`diff` renders colorized Git-style patches. `review` displays agent summaries
and file diffs, then prompts for **A**pprove, **R**eject, or request **C**hanges.
Requested changes require feedback; the API automatically schedules the revision
round. Approval and rejection send `null` feedback. The command returns after
submitting the decision; run `orch review RUN_ID` again once a revised checkpoint
is ready. Cancelling the prompt does not submit a decision.

The default API URL is `http://localhost:8000`. Override it with `ORCH_API_URL`
or the global option (placed before the command):

```sh
orch --api-url http://127.0.0.1:8001 diff RUN_ID
python -m cli.orch --help
```

HTTP, connection, and malformed-response errors print a concise message and
exit with code 1. These commands only manage proposals; they do not apply patches.

On macOS, Python skips `.pth` files marked with the filesystem `hidden` flag.
If an editable install succeeds but `orch` reports `No module named 'cli'`,
clear that flag on the generated editable-install hooks:

```sh
find .venv -name '__editable__*.pth' -exec chflags nohidden {} +
```

If a filesystem or editor process reapplies the hidden flag, use a regular
install instead: `python -m pip install '.[dev]'`. This avoids editable hooks;
repeat that install after changing CLI source to update the installed command.

## Schemas

`app/schemas.py` defines task types, run statuses, creation and approval
requests, file diffs, step history, checkpoints, and runs. Each model includes
field descriptions and realistic JSON Schema examples for OpenAPI.

Enum wire values, file statuses, and approval decisions are lowercase. Requests
default to `code_and_review` mode and repository ref `main`; prompts must contain
1 to 4000 characters. Collections use independent default factories. Optional
run fields and approval feedback default to `null`. Checkpoint actions are
`approve_reject` or `edit_prompt`, defaulting to `approve_reject`.

Runs include `context_diffs` and required `created_at` / `updated_at` timestamps;
steps record their creation time automatically. Timestamps must have a timezone,
are normalized to UTC, and serialize as ISO 8601 strings ending in `Z`.
All model configurations enable population by field name. This replaces the
previous uppercase wire contract; clients and stored payloads must be migrated.

The API exposes these models and their examples in its generated OpenAPI
document at `/openapi.json` and interactive Swagger UI at `/docs`.

## HTTP API

| Method | Path | Response |
| --- | --- | --- |
| POST | `/v1/runs` | `202` with a `queued` Run snapshot; starts background execution |
| GET | `/v1/runs/{run_id}` | `200` with the current Run, step history, and pending checkpoint |
| GET | `/v1/runs/{run_id}/diffs` | `200` with `{run_id, diffs, context_diffs}` |
| POST | `/v1/runs/{run_id}/approvals/{checkpoint_id}` | `200` with the Run after processing the decision |

`diffs` contains current code and documentation `FileDiff` proposals.
`context_diffs` contains structured agent outputs, review findings, and revision
feedback. An unexecuted run returns an empty diff list and empty context object.
The OpenAPI document reports API version `1.0.0`.

```sh
curl -X POST http://127.0.0.1:8000/v1/runs \
	-H 'Content-Type: application/json' \
	-d '{"prompt":"Add pagination.","target_files":["app/users.py"]}'
```

Poll the returned run ID until the run reaches `awaiting_approval`, then submit
`{"decision":"approved","feedback":null}` to the pending checkpoint's approval
URL. Rejection stops the run. `request_changes` returns a `running` snapshot and
automatically schedules another coder/reviewer round with the provided feedback.

Unknown run IDs return `404` with `{"detail":"Run not found"}`. Invalid checkpoint
IDs return `400` with `{"detail":"Invalid checkpoint ID"}`. Submitting a decision
when the run is not awaiting approval also returns `400`. Invalid request bodies
retain FastAPI's standard `422` response.

CORS permits all origins, methods, and headers for local development.
Credentialed cross-origin requests are disabled. Restrict this policy and add
authentication before deploying the API beyond a trusted development environment.

Execution uses `asyncio.create_task` and per-run async locks. Tasks are retained
by the application until completion, failures are logged, and unfinished tasks
are cancelled during application shutdown. Agents
are awaited on the event loop, so state reads can observe a running step while
an agent waits for asynchronous work. Agent implementations must not block the loop.
Runs and tasks do not survive restarts. Use one Uvicorn worker for this in-memory
implementation; it is not a durable job queue or distributed storage.

## Orchestrator Engine

`app/engine.py` provides an abstract `BaseAgent`, Gemini-backed `CodingAgent` and
`DocReviewerAgent` with deterministic offline fallbacks, and an async-execution
`OrchestratorEngine`. Agent results
contain `diffs` and `output_summary`; the coder also returns `code_outputs`, and
the reviewer returns markdown `feedback`, structured `findings`, and a
documentation patch proposal. Diffs returned by agents are `FileDiff` objects.
Agents are named `Coder` and `DocReviewer` in run histories. Both accept an
optional `target_files` list; omitted lists use a safe `None` default.
The coder accepts optional plain-text code context or JSON context containing
`target_files`, `code_context`, and `feedback`. The reviewer accepts a JSON diff
list or JSON context containing `diffs` and `feedback`.

```python
import asyncio

from app.engine import OrchestratorEngine
from app.schemas import ApprovalRequest, CreateRunRequest

engine = OrchestratorEngine()
run = engine.create_run(CreateRunRequest(prompt="Add pagination."))
asyncio.run(engine.run_pipeline(run.run_id))
assert run.pending_checkpoint is not None
engine.submit_approval(
	run.run_id,
	run.pending_checkpoint.checkpoint_id,
	ApprovalRequest(decision="approved", feedback=None),
)
```

`code` executes Coder, `review_docs` executes DocReviewer, and `code_and_review`
executes both sequentially, forwarding generated code and diffs into the review.
`create_run` and `submit_approval` are synchronous. Await `run_pipeline` for an
entire selected round, or await `step_run` to execute just its next agent.
Approval completes a run; rejection marks it `rejected`. `request_changes`
appends feedback to context and resets the selected pipeline. Direct engine
callers must await `run_pipeline` again; the API schedules this automatically.
Step history is preserved, revised proposals replace the prior round's diffs,
and each round generates a new checkpoint. `context_diffs` exposes agent outputs,
review findings, and accumulated revision feedback.

Stepping a paused or terminal run is a no-op. Unknown run IDs raise `KeyError`;
out-of-state approval submissions and mismatched checkpoint IDs raise `ValueError`
without changing the run. Agent exceptions or malformed outputs mark the run
`failed` and record the error in its step history.

Runs are held in the engine's `runs` dictionary. This is a single-process,
in-memory mock, not persistent or thread-safe storage. Approval never applies
patches or modifies repository files. Documentation reviews are mock proposals,
not actual inspection of repository documentation or OpenAPI specifications.

## Test

```sh
python -m pytest
```