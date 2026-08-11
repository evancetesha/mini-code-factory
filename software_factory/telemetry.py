from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from software_factory.config import ROLES, FactoryError
from software_factory.models import ResolvedModel, resolved_model_for_role
from software_factory.process import run_command

TELEMETRY_VERSION = 1
TELEMETRY_FILENAME = "telemetry.json"
FACTORY_VERSION = "0.1.0"


@dataclass(frozen=True)
class DispatchTelemetry:
    run: str
    role: str
    tier: str
    model: str
    agent_status: str
    duration_ms: int
    started_at: str
    finished_at: str
    pane: str
    prompt: str
    report: str

    def to_json(self) -> dict[str, object]:
        return {
            "run": self.run,
            "role": self.role,
            "tier": self.tier,
            "model": self.model,
            "agent_status": self.agent_status,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pane": self.pane,
            "prompt": self.prompt,
            "report": self.report,
        }

    @classmethod
    def from_json(cls, value: object) -> DispatchTelemetry:
        raw = _object_map(value, "dispatch telemetry")
        return cls(
            run=_string(raw, "run"),
            role=_string(raw, "role"),
            tier=_string(raw, "tier"),
            model=_string(raw, "model"),
            agent_status=_string(raw, "agent_status"),
            duration_ms=_integer(raw, "duration_ms"),
            started_at=_string(raw, "started_at"),
            finished_at=_string(raw, "finished_at"),
            pane=_string(raw, "pane"),
            prompt=_string(raw, "prompt"),
            report=_string(raw, "report"),
        )


@dataclass(frozen=True)
class RunTelemetry:
    run: str
    started_at: str
    factory_version: str
    herdr_version: str
    opencode_version: str
    models: tuple[ResolvedModel, ...]
    dispatches: tuple[DispatchTelemetry, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "version": TELEMETRY_VERSION,
            "run": self.run,
            "started_at": self.started_at,
            "factory_version": self.factory_version,
            "herdr_version": self.herdr_version,
            "opencode_version": self.opencode_version,
            "models": {item.role: {"tier": item.tier, "model": item.model} for item in self.models},
            "dispatches": [item.to_json() for item in self.dispatches],
        }

    @classmethod
    def from_json(cls, value: object) -> RunTelemetry:
        raw = _object_map(value, "run telemetry")
        if _integer(raw, "version") != TELEMETRY_VERSION:
            raise FactoryError("unsupported run telemetry version")
        raw_models = _object_map(raw.get("models"), "telemetry models")
        models: list[ResolvedModel] = []
        for role in ROLES:
            model = _object_map(raw_models.get(role), f"telemetry model '{role}'")
            models.append(
                ResolvedModel(
                    role=role,
                    tier=_string(model, "tier"),
                    model=_string(model, "model"),
                )
            )
        raw_dispatches = raw.get("dispatches")
        if not isinstance(raw_dispatches, list):
            raise FactoryError("telemetry dispatches must be a list")
        return cls(
            run=_string(raw, "run"),
            started_at=_string(raw, "started_at"),
            factory_version=_string(raw, "factory_version"),
            herdr_version=_string(raw, "herdr_version"),
            opencode_version=_string(raw, "opencode_version"),
            models=tuple(models),
            dispatches=tuple(DispatchTelemetry.from_json(item) for item in raw_dispatches),
        )


def utc_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    return current.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def initialize_run_telemetry(run_directory: Path, now: datetime | None = None) -> RunTelemetry:
    telemetry = RunTelemetry(
        run=run_directory.name,
        started_at=utc_timestamp(now),
        factory_version=FACTORY_VERSION,
        herdr_version=run_command(["herdr", "--version"]).stdout.strip(),
        opencode_version=run_command(["opencode", "--version"]).stdout.strip(),
        models=tuple(resolved_model_for_role(role) for role in ROLES),
        dispatches=(),
    )
    _write_run_telemetry(run_directory, telemetry)
    return telemetry


def load_run_telemetry(run_directory: Path) -> RunTelemetry:
    path = run_directory / TELEMETRY_FILENAME
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FactoryError(f"invalid or missing run telemetry: {path}") from error
    return RunTelemetry.from_json(raw)


def record_dispatch(run_directory: Path, dispatch: DispatchTelemetry) -> None:
    path = run_directory / TELEMETRY_FILENAME
    telemetry = (
        load_run_telemetry(run_directory)
        if path.exists()
        else initialize_run_telemetry(run_directory)
    )
    updated = RunTelemetry(
        run=telemetry.run,
        started_at=telemetry.started_at,
        factory_version=telemetry.factory_version,
        herdr_version=telemetry.herdr_version,
        opencode_version=telemetry.opencode_version,
        models=telemetry.models,
        dispatches=(*telemetry.dispatches, dispatch),
    )
    _write_run_telemetry(run_directory, updated)


def format_dispatch_headers(dispatch: DispatchTelemetry) -> str:
    return _format_headers(
        (
            ("Telemetry-Version", str(TELEMETRY_VERSION)),
            ("Run", dispatch.run),
            ("Role", dispatch.role),
            ("Model-Tier", dispatch.tier),
            ("Model", dispatch.model),
            ("Agent-Status", dispatch.agent_status),
            ("Duration", format_duration(dispatch.duration_ms)),
            ("Duration-Ms", str(dispatch.duration_ms)),
            ("Started-At", dispatch.started_at),
            ("Finished-At", dispatch.finished_at),
            ("Herdr-Pane", dispatch.pane),
            ("Prompt", dispatch.prompt),
            ("Report", dispatch.report),
        )
    )


def format_run_headers(
    telemetry: RunTelemetry,
    *,
    status: str,
    finished_at: datetime | None = None,
) -> str:
    finished = finished_at or datetime.now(UTC)
    started = _parse_timestamp(telemetry.started_at)
    elapsed_ms = max(0, round((finished - started).total_seconds() * 1000))
    headers: list[tuple[str, str]] = [
        ("Telemetry-Version", str(TELEMETRY_VERSION)),
        ("Run", telemetry.run),
        ("Status", status),
        ("Elapsed", format_duration(elapsed_ms)),
        ("Elapsed-Ms", str(elapsed_ms)),
        ("Started-At", telemetry.started_at),
        ("Finished-At", utc_timestamp(finished)),
        ("Version", telemetry.factory_version),
        ("Herdr-Version", telemetry.herdr_version),
        ("OpenCode-Version", telemetry.opencode_version),
        ("Dispatch-Count", str(len(telemetry.dispatches))),
        (
            "Blocked-Dispatch-Count",
            str(sum(item.agent_status == "blocked" for item in telemetry.dispatches)),
        ),
    ]
    for model in telemetry.models:
        label = model.role.title()
        headers.extend(
            (
                (f"{label}-Model-Tier", model.tier),
                (f"{label}-Model", model.model),
            )
        )
    return _format_headers(headers)


def prepend_report_headers(report: Path, dispatch: DispatchTelemetry) -> None:
    if report.is_symlink() or not report.is_file():
        raise FactoryError(f"worker did not create a regular report file: {report}")
    try:
        body = report.read_text(encoding="utf-8")
        report.write_text(f"{format_dispatch_headers(dispatch)}\n\n---\n\n{body}", encoding="utf-8")
    except OSError as error:
        raise FactoryError(f"could not add telemetry headers to {report}") from error


def format_duration(duration_ms: int) -> str:
    seconds = duration_ms / 1000
    if seconds < 60:
        return f"{seconds:.3f}s"
    minutes, remaining = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remaining:06.3f}s"
    hours, remaining_minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(remaining_minutes)}m {remaining:06.3f}s"


def _write_run_telemetry(run_directory: Path, telemetry: RunTelemetry) -> None:
    target = run_directory / TELEMETRY_FILENAME
    temporary = target.with_suffix(".tmp")
    try:
        temporary.write_text(
            f"{json.dumps(telemetry.to_json(), indent=2, sort_keys=True)}\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    except OSError as error:
        raise FactoryError(f"could not write run telemetry: {target}") from error


def _format_headers(items: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> str:
    return "\n".join(f"Factory-{name}: {value}" for name, value in items)


def _object_map(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise FactoryError(f"{label} must be an object")
    return cast("dict[str, object]", value)


def _string(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise FactoryError(f"telemetry field '{key}' must be a string")
    return value


def _integer(raw: dict[str, object], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int):
        raise FactoryError(f"telemetry field '{key}' must be an integer")
    return value


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise FactoryError(f"invalid telemetry timestamp: {value}") from error
    if parsed.tzinfo is None:
        raise FactoryError(f"telemetry timestamp must include a timezone: {value}")
    return parsed.astimezone(UTC)
