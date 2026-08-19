from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from software_factory.cli import app
from software_factory.config import (
    ASSETS_ROOT,
    FACTORY_ASSETS,
    FactoryError,
    ModelPolicy,
    Tier,
    canonical_prompt_path,
    canonical_report_target,
    create_run,
    load_model_policy,
    materialize_factory,
    slugify,
)
from software_factory.models import ResolvedModel, parse_model_catalog, resolve_policy
from software_factory.telemetry import (
    DispatchTelemetry,
    RunTelemetry,
    format_dispatch_headers,
    format_run_headers,
    prepend_report_headers,
)


def test_help_lists_primary_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "dispatch" in result.stdout
    assert "models" in result.stdout
    assert "run" in result.stdout


def test_slugify_is_short_and_safe() -> None:
    assert slugify(" Python Lambda Demo! ") == "python-lambda-demo"
    assert slugify("***") == "task"
    assert len(slugify("a" * 100)) == 32


def test_create_run_handles_same_second_collision(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 8, 32, 48, tzinfo=UTC)

    first = create_run("demo", runs_root=tmp_path, now=now)
    second = create_run("demo", runs_root=tmp_path, now=now)

    assert first.name == "20260811T083248Z-demo"
    assert second.name == "20260811T083248Z-demo-2"


def test_create_run_propagates_when_runs_root_is_a_file(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(FileExistsError):
        create_run("demo", runs_root=runs_root)


def test_canonical_prompt_path_stays_within_runs(tmp_path: Path) -> None:
    prompt = tmp_path / "run" / "prompt.md"
    prompt.parent.mkdir()
    prompt.write_text("build", encoding="utf-8")

    assert canonical_prompt_path(prompt, runs_root=tmp_path) == prompt


def test_report_target_must_share_the_prompt_run(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()

    assert canonical_report_target(run / "build-report.md", run) == run / "build-report.md"


def test_model_policy_uses_two_tiers() -> None:
    policy = load_model_policy()

    assert policy.roles == {
        "orchestrator": "state-of-the-art",
        "builder": "workhorse",
        "reviewer": "state-of-the-art",
    }
    assert set(policy.tiers) == {"state-of-the-art", "workhorse"}


def test_model_policy_rejects_schema_violations(tmp_path: Path) -> None:
    policy_path = tmp_path / "model-tiers.json"
    policy_path.write_text(
        json.dumps(
            {
                "roles": {
                    "orchestrator": "workhorse",
                    "builder": "workhorse",
                    "reviewer": "workhorse",
                },
                "tiers": {
                    "workhorse": {
                        "description": "balanced",
                        "candidates": ["provider/a", "provider/a"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(FactoryError):
        load_model_policy(policy_path)


def test_parse_model_catalog_extracts_provider_model_ids() -> None:
    output = (
        "anthropic/claude-opus-4-5  (available)\n"
        "openai/gpt-5.2\n"
        "no-slash-here\n"
        "google/gemini-2.5-pro default\n"
    )

    assert parse_model_catalog(output) == {
        "anthropic/claude-opus-4-5",
        "openai/gpt-5.2",
        "google/gemini-2.5-pro",
    }


def test_packaged_assets_match_repository_files() -> None:
    repo_root = Path(__file__).resolve().parent.parent

    for source_rel, target_rel in FACTORY_ASSETS:
        assert (ASSETS_ROOT / source_rel).read_text(encoding="utf-8") == (
            repo_root / target_rel
        ).read_text(encoding="utf-8")


def test_materialize_factory_copies_missing_assets_only(tmp_path: Path) -> None:
    created = materialize_factory(tmp_path)

    assert (tmp_path / "opencode.json").is_file()
    assert (tmp_path / "model-tiers.json").is_file()
    assert (tmp_path / ".factory" / "prompts" / "builder.md").is_file()
    assert (tmp_path / ".opencode" / "skills" / "herdr" / "SKILL.md").is_file()
    assert {path.relative_to(tmp_path) for path in created} == {
        Path("opencode.json"),
        Path("model-tiers.json"),
        Path("model-tiers.schema.json"),
        Path(".factory/prompts/orchestrator.md"),
        Path(".factory/prompts/builder.md"),
        Path(".factory/prompts/reviewer.md"),
        Path(".opencode/skills/herdr/SKILL.md"),
    }

    assert materialize_factory(tmp_path) == []


def test_resolver_uses_first_available_candidate() -> None:
    policy = ModelPolicy(
        roles={
            "orchestrator": "state-of-the-art",
            "builder": "workhorse",
            "reviewer": "state-of-the-art",
        },
        tiers={
            "state-of-the-art": Tier("best", ("provider/missing", "provider/sota")),
            "workhorse": Tier("balanced", ("provider/workhorse",)),
        },
    )

    resolved = resolve_policy(policy, {"provider/sota", "provider/workhorse"})

    assert [(item.role, item.model) for item in resolved] == [
        ("orchestrator", "provider/sota"),
        ("builder", "provider/workhorse"),
        ("reviewer", "provider/sota"),
    ]


def test_dispatch_headers_are_copy_ready() -> None:
    dispatch = _dispatch()

    headers = format_dispatch_headers(dispatch)

    assert "Factory-Role: builder" in headers
    assert "Factory-Model: provider/workhorse" in headers
    assert "Factory-Agent-Status: done" in headers
    assert "Factory-Duration: 12.345s" in headers


def test_report_receives_telemetry_before_worker_content(tmp_path: Path) -> None:
    report = tmp_path / "build-report.md"
    report.write_text("STATUS: COMPLETE\n", encoding="utf-8")

    prepend_report_headers(report, _dispatch())

    content = report.read_text(encoding="utf-8")
    assert content.startswith("Factory-Telemetry-Version: 1\nFactory-Run: run-1")
    assert content.endswith("STATUS: COMPLETE\n")


def test_run_headers_include_total_time_and_role_models() -> None:
    telemetry = RunTelemetry(
        run="run-1",
        started_at="2026-08-11T10:00:00.000Z",
        factory_version="0.1.0",
        herdr_version="herdr 0.8.0",
        opencode_version="1.18.16",
        models=(
            ResolvedModel("orchestrator", "state-of-the-art", "provider/sota"),
            ResolvedModel("builder", "workhorse", "provider/workhorse"),
            ResolvedModel("reviewer", "state-of-the-art", "provider/sota"),
        ),
        dispatches=(_dispatch(),),
    )

    headers = format_run_headers(
        telemetry,
        status="APPROVED",
        finished_at=datetime(2026, 8, 11, 10, 1, 30, 250000, tzinfo=UTC),
    )

    assert "Factory-Status: APPROVED" in headers
    assert "Factory-Elapsed: 1m 30.250s" in headers
    assert "Factory-Herdr-Version: herdr 0.8.0" in headers
    assert "Factory-Builder-Model: provider/workhorse" in headers
    assert "Factory-Dispatch-Count: 1" in headers


def _dispatch() -> DispatchTelemetry:
    return DispatchTelemetry(
        run="run-1",
        role="builder",
        tier="workhorse",
        model="provider/workhorse",
        agent_status="done",
        duration_ms=12345,
        started_at="2026-08-11T10:00:00.000Z",
        finished_at="2026-08-11T10:00:12.345Z",
        pane="w1:p2",
        prompt=".factory/runs/run-1/build-prompt.md",
        report=".factory/runs/run-1/build-report.md",
    )
