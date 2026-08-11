from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from software_factory.config import (
    FACTORY_ROOT,
    FactoryError,
    canonical_prompt_path,
    canonical_report_target,
    prompt_timeout_ms,
    require_herdr_context,
)
from software_factory.models import resolved_model_for_role
from software_factory.process import parse_json_output, run_command
from software_factory.telemetry import (
    DispatchTelemetry,
    format_dispatch_headers,
    prepend_report_headers,
    record_dispatch,
    utc_timestamp,
)

SETTLED_STATUSES = {"idle", "done", "blocked"}


def dispatch_worker(role: str, prompt_file: Path, report_file: Path) -> DispatchTelemetry:
    require_herdr_context()
    prompt_path = canonical_prompt_path(prompt_file)
    report_path = canonical_report_target(report_file, prompt_path.parent)
    prompt = prompt_path.read_text(encoding="utf-8")
    if not prompt.strip():
        raise FactoryError(f"prompt file is empty: {prompt_file}")

    model = resolved_model_for_role(role)
    started_at = datetime.now(UTC)
    started_ns = time.monotonic_ns()
    completed = run_command(
        [
            "herdr",
            "agent",
            "prompt",
            role,
            prompt,
            "--wait",
            "--timeout",
            str(prompt_timeout_ms()),
        ],
        check=False,
    )
    finished_at = datetime.now(UTC)
    duration_ms = max(0, round((time.monotonic_ns() - started_ns) / 1_000_000))

    if completed.returncode != 0:
        dispatch = _dispatch_telemetry(
            role=role,
            tier=model.tier,
            model=model.model,
            status="error",
            pane="unknown",
            duration_ms=duration_ms,
            started_at=started_at,
            finished_at=finished_at,
            prompt_path=prompt_path,
            report_path=report_path,
        )
        record_dispatch(prompt_path.parent, dispatch)
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown Herdr error"
        raise FactoryError(
            f"{format_dispatch_headers(dispatch)}\n\nHerdr dispatch failed: {detail}"
        )

    response = parse_json_output(completed)
    result = _object_map(response.get("result"), "Herdr result")
    agent = _object_map(result.get("agent"), "Herdr agent")
    status = _required_string(agent, "agent_status")
    pane = _required_string(agent, "pane_id")
    if status not in SETTLED_STATUSES:
        raise FactoryError(f"Herdr returned unexpected settled state: {status}")

    dispatch = _dispatch_telemetry(
        role=role,
        tier=model.tier,
        model=model.model,
        status=status,
        pane=pane,
        duration_ms=duration_ms,
        started_at=started_at,
        finished_at=finished_at,
        prompt_path=prompt_path,
        report_path=report_path,
    )
    record_dispatch(prompt_path.parent, dispatch)
    if report_path.exists():
        prepend_report_headers(report_path, dispatch)
    elif status != "blocked":
        raise FactoryError(
            f"{format_dispatch_headers(dispatch)}\n\n"
            f"Worker settled without creating {report_path.name}"
        )
    return dispatch


def _dispatch_telemetry(
    *,
    role: str,
    tier: str,
    model: str,
    status: str,
    pane: str,
    duration_ms: int,
    started_at: datetime,
    finished_at: datetime,
    prompt_path: Path,
    report_path: Path,
) -> DispatchTelemetry:
    return DispatchTelemetry(
        run=prompt_path.parent.name,
        role=role,
        tier=tier,
        model=model,
        agent_status=status,
        duration_ms=duration_ms,
        started_at=utc_timestamp(started_at),
        finished_at=utc_timestamp(finished_at),
        pane=pane,
        prompt=str(prompt_path.relative_to(FACTORY_ROOT)),
        report=str(report_path.relative_to(FACTORY_ROOT)),
    )


def _object_map(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise FactoryError(f"{label} must be an object")
    return cast("dict[str, object]", value)


def _required_string(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise FactoryError(f"Herdr agent field '{key}' must be a string")
    return value
