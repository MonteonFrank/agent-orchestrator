# Project Instructions

- Use Python 3.11+ with FastAPI, Pydantic v2, and Uvicorn.
- Keep domain models in `app/schemas.py` and the API entry point in `app/main.py`.
- Use `ConfigDict` and `Field` for schema documentation and realistic examples.
- Preserve lowercase task and run enum wire values and literal choices.
- Use a list default factory for `CreateRunRequest.target_files`.
- Default optional request and run fields to the values declared in the schemas.
- Use independent factories for collection defaults and timezone-aware UTC timestamps.
- Do not add orchestration execution, persistence, or approval behavior without a request.
- Use `.venv` and install development dependencies with `python -m pip install -e '.[dev]'`.
- Validate changes with `python -m pytest`.

## Setup Progress

- [x] Clarify requirements: FastAPI API foundation and eight schema definitions.
- [x] Scaffold and customize the project with packaging, schemas, and tests.
- [x] Configure an isolated Python environment and install dependencies.
- [x] Validate schemas and FastAPI integration: 20 tests passed.
- [x] Document setup, launch commands, and schema contracts.
- [x] Skip additional extensions: none required for this API scaffold.
- [x] Verify editor diagnostics and editable package installation.
- [x] Create and run the Uvicorn development task and verify the health endpoint.