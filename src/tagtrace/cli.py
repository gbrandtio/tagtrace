"""The `tagtrace` command line."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from tagtrace.analyze import Report, analyze
from tagtrace.collect import Manifest, collect
from tagtrace.config import JiraConfig, load_config
from tagtrace.errors import TagtraceError
from tagtrace.gitlog import RepoCache
from tagtrace.models import create_llm

EXIT_FATAL = 1
EXIT_INCOMPLETE = 2

app = typer.Typer(help=__doc__, no_args_is_help=True, add_completion=False)

ConfigOption = Annotated[Path, typer.Option("--config", "-c", help="The config file.")]
OutOption = Annotated[Path | None, typer.Option("--out", "-o", help="The run folder. Default: runs/<time>.")]
OldFile = Annotated[Path, typer.Argument(help="pulldeps.json of the old version.")]
NewFile = Annotated[Path, typer.Argument(help="pulldeps.json of the new version.")]


def _default_out() -> Path:
    """Name a new run folder after the current time."""
    return Path("runs") / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def _describe_collection(manifest: Manifest, out: Path, jira: JiraConfig) -> None:
    """Tell the user what was collected and what failed."""
    failed = [result for result in manifest.components if result.status == "failed"]
    typer.echo(
        f"Collected {len(manifest.components)} changed component(s), {len(manifest.added)} added, "
        f"{len(manifest.removed)} removed, in {out}"
    )
    for result in failed:
        typer.secho(f"  Failed: {result.component.name}: {result.error}", fg="yellow", err=True)
    for key in manifest.tickets_failed:
        typer.secho(f"  Ticket could not be loaded: {key}", fg="yellow", err=True)
    if manifest.tickets_not_found and not jira.projects:
        typer.secho(
            f"  Jira did not return {', '.join(manifest.tickets_not_found)}. If these are not tickets, their commits "
            "are wrongly counted as linked. Set jira.projects to fix this.",
            fg="yellow",
            err=True,
        )


def _describe_report(report: Report, run: Path) -> None:
    """Tell the user where the report is and what is missing from it."""
    if report.release is not None:
        typer.echo(f"Overall risk: {report.release.risk.level}. Testing effort: {report.release.testing.effort}.")
    for item in report.incomplete:
        typer.secho(f"  Incomplete: {item}", fg="yellow", err=True)
    typer.echo(f"Report: {run / 'report.md'}")


def _fail(error: TagtraceError) -> typer.Exit:
    """Show an expected error without a stack trace."""
    typer.secho(f"Error: {error}", fg="red", err=True)
    return typer.Exit(EXIT_FATAL)


@app.command("collect")
def collect_command(
    old_file: OldFile, new_file: NewFile, config: ConfigOption = Path("tagtrace.yaml"), out: OutOption = None
) -> None:
    """Save the commits and Jira tickets of every changed component."""
    run = out or _default_out()
    try:
        jira = load_config(config).require_jira()
        manifest = asyncio.run(collect(old_file, new_file, jira, run, RepoCache()))
    except TagtraceError as error:
        raise _fail(error) from error
    _describe_collection(manifest, run, jira)
    if not manifest.is_complete:
        raise typer.Exit(EXIT_INCOMPLETE)


@app.command("analyze")
def analyze_command(
    run: Annotated[Path, typer.Argument(help="A run folder made by 'tagtrace collect'.")],
    config: ConfigOption = Path("tagtrace.yaml"),
) -> None:
    """Assess a collected run with the configured model and write the report."""
    try:
        ai = load_config(config).require_ai()
        report = asyncio.run(analyze(run, ai, create_llm(ai)))
    except TagtraceError as error:
        raise _fail(error) from error
    _describe_report(report, run)
    if not report.is_complete:
        raise typer.Exit(EXIT_INCOMPLETE)


@app.command("run")
def run_command(
    old_file: OldFile, new_file: NewFile, config: ConfigOption = Path("tagtrace.yaml"), out: OutOption = None
) -> None:
    """Run 'collect', then 'analyze'."""
    run = out or _default_out()
    try:
        settings = load_config(config)
        jira, ai = settings.require_jira(), settings.require_ai()
        # Create the model client first, so a missing extra fails before the slow collection step.
        llm = create_llm(ai)
        manifest = asyncio.run(collect(old_file, new_file, jira, run, RepoCache()))
        _describe_collection(manifest, run, jira)
        report = asyncio.run(analyze(run, ai, llm))
    except TagtraceError as error:
        raise _fail(error) from error
    _describe_report(report, run)
    if not (manifest.is_complete and report.is_complete):
        raise typer.Exit(EXIT_INCOMPLETE)
