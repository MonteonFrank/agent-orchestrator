"""Typer commands for agent runs, proposed diffs, and human review."""

import time
from typing import Annotated, NoReturn, TypeVar
from urllib.parse import quote

import httpx
import typer
from pydantic import BaseModel, ValidationError
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from app.schemas import CreateRunRequest, Run, RunDiffsResponse, RunStatus, TaskType

app = typer.Typer(help="Agent Orchestrator CLI", no_args_is_help=True)
console = Console()
error_console = Console(stderr=True)
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


@app.callback()
def configure(
    context: typer.Context,
    api_url: Annotated[
        str,
        typer.Option(
            "--api-url",
            envvar="ORCH_API_URL",
            help="Base URL of the orchestrator API.",
        ),
    ] = "http://localhost:8000",
) -> None:
    context.obj = api_url.rstrip("/")


def _api_client(context: typer.Context) -> httpx.Client:
    return httpx.Client(base_url=context.obj, timeout=30.0)


def _fail(message: str) -> NoReturn:
    error_console.print(Text(f"Error: {message}", style="red"))
    raise typer.Exit(code=1)


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    model: type[ResponseModel],
    payload: dict | None = None,
) -> ResponseModel:
    try:
        response = client.request(method, path, json=payload)
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        detail = error.response.reason_phrase
        try:
            body = error.response.json()
            if isinstance(body, dict):
                detail = str(body.get("detail", detail))
        except ValueError:
            pass
        _fail(f"HTTP {error.response.status_code}: {detail}")
    except httpx.RequestError as error:
        _fail(f"Cannot reach the API: {error}")
    try:
        return model.model_validate(response.json())
    except ValueError:
        _fail("The API returned an invalid response.")


def _status_text(run: Run) -> Text:
    text = Text(f"{run.run_id}: {run.status.value}")
    if run.current_step:
        text.append(f" ({run.current_step})")
    return text


def _print_summary(run: Run) -> None:
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold")
    details.add_column(overflow="fold")
    details.add_row("Run ID", Text(run.run_id))
    details.add_row("Status", Text(run.status.value))
    details.add_row("Current step", Text(run.current_step or "-"))
    details.add_row("File diffs", str(len(run.diffs)))
    details.add_row("Updated", run.updated_at.isoformat())
    if run.pending_checkpoint:
        details.add_row("Checkpoint", Text(run.pending_checkpoint.checkpoint_id))
    console.print(Panel(details, title="Run Summary", border_style="cyan"))
    if run.state_history:
        history = Table(title="Agent State History", expand=True)
        history.add_column("Step", justify="right", width=4)
        history.add_column("Agent")
        history.add_column("Status")
        history.add_column("Output", overflow="fold")
        for step in run.state_history:
            history.add_row(
                str(step.step_index),
                Text(step.agent_name),
                Text(step.status),
                Text(step.output_summary),
            )
        console.print(history)


def _watch_run(client: httpx.Client, run: Run) -> None:
    active_states = (RunStatus.QUEUED, RunStatus.RUNNING)
    console.print(_status_text(run))
    previous_status = (run.status, run.current_step)
    with Live(Spinner("dots", text=_status_text(run)), console=console, transient=True) as live:
        while run.status in active_states:
            run = _request(client, "GET", f"/v1/runs/{quote(run.run_id, safe='')}", Run)
            current_status = (run.status, run.current_step)
            if current_status != previous_status:
                live.console.print(_status_text(run))
                previous_status = current_status
            live.update(Spinner("dots", text=_status_text(run)))
            if run.status in active_states:
                time.sleep(0.5)
    _print_summary(run)
    if run.status == RunStatus.AWAITING_APPROVAL:
        console.print(Text(f"Review required: orch review {run.run_id}", style="yellow"))
    elif run.status in (RunStatus.FAILED, RunStatus.REJECTED):
        for step in run.state_history:
            if step.status == "failed":
                console.print(Text(step.output_summary, style="red"))
        raise typer.Exit(code=1)


def _print_diffs(result: RunDiffsResponse) -> None:
    if not result.diffs:
        console.print("No file diffs available.")
    for diff in result.diffs:
        console.print(Text(f"{diff.status}: {diff.path}", style="bold"))
        console.print(Syntax(diff.patch, "diff", theme="ansi_dark", background_color="default"))


@app.command()
def run(
    context: typer.Context,
    prompt: Annotated[str, typer.Argument(help="Instructions for the agents.")],
    mode: Annotated[
        TaskType, typer.Option("--mode", "-m", help="Execution mode.")
    ] = TaskType.PIPELINE,
    file: Annotated[
        list[str] | None,
        typer.Option("--file", "-f", help="Target file; repeat for multiple files."),
    ] = None,
    detach: Annotated[
        bool, typer.Option("--detach", help="Return without waiting for execution.")
    ] = False,
) -> None:
    try:
        request = CreateRunRequest(prompt=prompt, pipeline_mode=mode, target_files=file or [])
    except ValidationError as error:
        _fail(str(error.errors()[0]["msg"]))
    with _api_client(context) as client:
        created = _request(client, "POST", "/v1/runs", Run, request.model_dump(mode="json"))
        if detach:
            console.print(_status_text(created))
            _print_summary(created)
            return
        _watch_run(client, created)


@app.command()
def diff(context: typer.Context, run_id: str) -> None:
    """Print proposed file changes as colorized unified diffs."""
    with _api_client(context) as client:
        result = _request(
            client, "GET", f"/v1/runs/{quote(run_id, safe='')}/diffs", RunDiffsResponse
        )
    _print_diffs(result)


@app.command()
def review(context: typer.Context, run_id: str) -> None:
    """Review an active checkpoint and submit a human decision."""
    path = f"/v1/runs/{quote(run_id, safe='')}"
    with _api_client(context) as client:
        run = _request(client, "GET", path, Run)
        if run.status != RunStatus.AWAITING_APPROVAL or run.pending_checkpoint is None:
            _fail(f"Run {run_id} is not awaiting approval (status: {run.status.value}).")
        _print_summary(run)
        for step in run.state_history:
            console.print(
                Text(f"Step {step.step_index}: {step.agent_name} ({step.status})", style="bold")
            )
            console.print(Markdown(step.output_summary))
        result = _request(client, "GET", f"{path}/diffs", RunDiffsResponse)
        _print_diffs(result)
        console.print(Text(run.pending_checkpoint.description, style="bold"))
        console.print(Text(run.pending_checkpoint.requires_action))
        decisions = {"a": "approved", "r": "rejected", "c": "request_changes"}
        while True:
            choice = typer.prompt("[A]pprove, [R]eject, or Request [C]hanges").strip().lower()
            if choice in decisions:
                break
            console.print("Enter A, R, or C.")
        feedback = None
        if choice == "c":
            while not feedback:
                feedback = typer.prompt("Requested changes").strip()
                if not feedback:
                    console.print("Describe the requested changes.")
        checkpoint_id = quote(run.pending_checkpoint.checkpoint_id, safe="")
        updated = _request(
            client,
            "POST",
            f"{path}/approvals/{checkpoint_id}",
            Run,
            {
                "decision": decisions[choice],
                "feedback": feedback,
            },
        )
        console.print(_status_text(updated))
        _print_summary(updated)


if __name__ == "__main__":
    app()
