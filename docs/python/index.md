# Python Engineering Guidelines

These rules apply to the Python code in this repository, today the [Synapse](../../synapse/README.md) suite. Like the [.NET](../dotnet/index.md) and [Flutter](../flutter/index.md) pages, they are a **shared baseline**. When a rule doesn't fit, deviate on purpose and write down why. General principles (SOLID, coupling, ADRs) live in the .NET pages and are not repeated here.

**Baseline:** Python 3.12+ (3.13 pinned for development) · `uv` workspaces · `ruff` (lint and format) · `pyright` in strict mode · `pytest` with `pytest-asyncio`.

## Project layout

- **Do** put each package in `synapse/packages/<name>/` with a `src/<import_name>/` folder, a `tests/` folder, a `README.md` and a `py.typed` marker.
- **Do** declare optional integrations (model providers, Playwright, embedding back ends) as extras, and import them inside the function that needs them. A missing extra must raise a `ConfigError` that names the extra to install.
- **Don't** let a package import a sibling it does not list in its `pyproject.toml`. The RAG packages never import each other.

## Types and data

- **Do** type every function, parameter and return value. `pyright` runs in strict mode. Its "unknown type" rules are off only because LangChain, Playwright and psycopg are partly untyped.
- **Do** use pydantic models for anything read from config or sent over HTTP, and `dataclass` for internal records.
- **Do** keep secrets in `SecretStr` and read them from `${ENV_VAR}` in `synapse.yaml`. Never log them or put them in error messages.
- **Don't** use mutable default values or module-level state. Pass settings in explicitly.

## Errors

- **Do** raise the errors from `synapse_core`: `ConfigError` for bad config, `SourceError` for a source that failed in its mode, and `UnsupportedOperationError` for an operation the mode cannot do.
- **Do** write messages a user can act on: what failed, where, and what to do (`Run: synapse-jira login corp-web`).
- **Don't** fall back to another access mode, another model or another index on failure. Report the failure.

## Async and I/O

- **Do** use `async` for network, database and browser work, and `async with` for anything that must be closed.
- **Don't** call blocking functions (`subprocess.run`, `time.sleep`, `requests`) inside `async def`. Use `asyncio.create_subprocess_exec`, `asyncio.sleep` and `httpx.AsyncClient`.
- **Do** run `git` and other tools with an argument list, never through a shell.

## SQL

- **Do** build SQL with `psycopg.sql` (`Identifier`, `Literal`, placeholders). Table and column names come from validated config or fixed lists, and values are always parameters.

## Documentation and style

- **Do** give every module, public class and public function a docstring that says what it is for, in plain language.
- **Do** keep lines within 120 characters. `ruff format` decides the layout.

## Testing

- **Do** test sources without the network: `respx` for HTTP, real temporary git repositories for `clone` mode, and saved pages or XML for Playwright modes.
- **Do** mark tests that need Postgres with `@pytest.mark.integration` and the `database_url` fixture. They skip when `SYNAPSE_TEST_DATABASE_URL` is unset.
- **Do** test pipelines with `ScriptedModel` from `packages/testing_helpers.py`, and assert on what each model was sent, not only on what it returned.

## Review checklist

- [ ] New config fields are documented in the package README and `synapse.example.yaml`.
- [ ] No mode or model fallback was added.
- [ ] Data that may reach a remote model passes through redaction first.
- [ ] `uv run ruff check`, `uv run ruff format --check`, `uv run pyright` and `uv run pytest` pass.
