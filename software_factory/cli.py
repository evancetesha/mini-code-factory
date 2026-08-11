from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from software_factory.config import (
    FACTORY_ROOT,
    HERDR_SKILL_PATH,
    FactoryError,
    canonical_run_directory,
    create_run,
    load_model_policy,
    require_commands,
    require_herdr_context,
    validate_opencode_config,
)
from software_factory.dispatch import dispatch_worker
from software_factory.models import format_resolution, resolve_and_write_models
from software_factory.process import run_command
from software_factory.telemetry import (
    format_dispatch_headers,
    format_run_headers,
    initialize_run_telemetry,
    load_run_telemetry,
)


class WorkerRole(StrEnum):
    BUILDER = "builder"
    REVIEWER = "reviewer"


class RunStatus(StrEnum):
    IN_PROGRESS = "in-progress"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes-requested"
    BLOCKED = "blocked"


app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    help="Minimal OpenCode software factory controlled through Herdr.",
)
run_app = typer.Typer(help="Manage file-based factory runs.")
app.add_typer(run_app, name="run")


def _check_prerequisites() -> None:
    require_commands("herdr", "opencode", "uv")
    validate_opencode_config()
    load_model_policy()
    integration = run_command(["herdr", "integration", "status"], check=False)
    output = f"{integration.stdout}\n{integration.stderr}"
    opencode_is_current = any(line.startswith("opencode: current ") for line in output.splitlines())
    if integration.returncode != 0 or not opencode_is_current:
        raise FactoryError(
            "Herdr's OpenCode integration is not current; run: herdr integration install opencode"
        )
    try:
        checked_in_skill = HERDR_SKILL_PATH.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise FactoryError(f"missing project Herdr skill: {HERDR_SKILL_PATH}") from error
    bundled_skill = run_command(["herdr", "--skill"]).stdout.strip()
    if checked_in_skill != bundled_skill:
        raise FactoryError(
            "the project Herdr skill does not match the installed release; "
            "refresh .opencode/skills/herdr/SKILL.md from: herdr --skill"
        )


def _resolve_models_and_print() -> None:
    typer.echo(format_resolution(resolve_and_write_models()))


@app.callback()
def root(context: typer.Context) -> None:
    """Start the orchestrator when no subcommand is given."""
    if context.invoked_subcommand is not None:
        return
    _check_prerequisites()
    require_herdr_context()
    resolve_and_write_models()
    os.execvp(
        "opencode",
        ["opencode", str(FACTORY_ROOT), "--agent", "orchestrator", "--mini"],
    )


@app.command("check")
def check_factory() -> None:
    """Validate prerequisites, configuration, and model tiers."""
    _check_prerequisites()
    _resolve_models_and_print()
    typer.echo("factory: prerequisites and model tiers are ready")


@app.command("models")
def resolve_models() -> None:
    """Resolve configured tiers against the available OpenCode catalog."""
    _check_prerequisites()
    _resolve_models_and_print()


@run_app.command("new")
def new_run(label: Annotated[str, typer.Argument()] = "task") -> None:
    """Create a timestamped run handoff directory."""
    path = create_run(label)
    initialize_run_telemetry(path)
    typer.echo(path.relative_to(FACTORY_ROOT))


@app.command("dispatch")
def dispatch_agent(
    role: WorkerRole,
    prompt_file: Annotated[
        Path,
        typer.Option("--file", exists=True, dir_okay=False, readable=True),
    ],
    report_file: Annotated[Path, typer.Option("--report", dir_okay=False)],
) -> None:
    """Prompt a live worker, wait for settlement, and add telemetry to its report."""
    dispatch = dispatch_worker(role.value, prompt_file, report_file)
    typer.echo(format_dispatch_headers(dispatch))


@run_app.command("telemetry")
def show_run_telemetry(
    run_directory: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, readable=True),
    ],
    status: Annotated[RunStatus, typer.Option("--status")] = RunStatus.IN_PROGRESS,
) -> None:
    """Render a copy-ready telemetry header for the user-facing run result."""
    resolved = canonical_run_directory(run_directory)
    typer.echo(format_run_headers(load_run_telemetry(resolved), status=status.value.upper()))


def main() -> None:
    try:
        app()
    except FactoryError as error:
        typer.secho(f"factory: {error}", fg=typer.colors.RED, err=True)
        raise SystemExit(1) from error
