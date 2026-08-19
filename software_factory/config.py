from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema

FACTORY_ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = FACTORY_ROOT / ".factory" / "runs"
MODEL_POLICY_PATH = FACTORY_ROOT / "model-tiers.json"
MODEL_SCHEMA_PATH = FACTORY_ROOT / "model-tiers.schema.json"
MODEL_RUNTIME_ROOT = FACTORY_ROOT / ".factory" / "runtime" / "models"
OPENCODE_CONFIG_PATH = FACTORY_ROOT / "opencode.json"
HERDR_SKILL_PATH = FACTORY_ROOT / ".opencode" / "skills" / "herdr" / "SKILL.md"
ROLES = ("orchestrator", "builder", "reviewer")


class FactoryError(RuntimeError):
    """A concise, user-actionable factory failure."""


@dataclass(frozen=True)
class Tier:
    description: str
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class ModelPolicy:
    roles: dict[str, str]
    tiers: dict[str, Tier]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FactoryError(f"invalid JSON in {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise FactoryError(f"{path.name} must contain a JSON object")
    return value


def _validate_model_schema(value: dict[str, Any], label: str) -> None:
    try:
        schema = _load_json(MODEL_SCHEMA_PATH)
    except FactoryError as error:
        raise FactoryError(f"could not load {MODEL_SCHEMA_PATH.name}: {error}") from error
    try:
        jsonschema.validate(instance=value, schema=schema)
    except (jsonschema.ValidationError, jsonschema.SchemaError) as error:
        raise FactoryError(f"{label} is invalid: {error.message}") from error


def load_model_policy(path: Path = MODEL_POLICY_PATH) -> ModelPolicy:
    raw = _load_json(path)
    _validate_model_schema(raw, path.name)
    raw_roles = raw.get("roles")
    raw_tiers = raw.get("tiers")
    if not isinstance(raw_roles, dict) or set(raw_roles) != set(ROLES):
        raise FactoryError(f"{path.name} must assign exactly these roles: {', '.join(ROLES)}")
    if not isinstance(raw_tiers, dict) or not raw_tiers:
        raise FactoryError(f"{path.name} must define at least one tier")

    tiers: dict[str, Tier] = {}
    for name, raw_tier in raw_tiers.items():
        if not isinstance(name, str) or not isinstance(raw_tier, dict):
            raise FactoryError(f"invalid tier entry in {path.name}")
        description = raw_tier.get("description")
        candidates = raw_tier.get("candidates")
        if not isinstance(description, str) or not description.strip():
            raise FactoryError(f"tier '{name}' needs a description")
        if not isinstance(candidates, list) or not candidates:
            raise FactoryError(f"tier '{name}' needs at least one candidate")
        if not all(isinstance(candidate, str) and "/" in candidate for candidate in candidates):
            raise FactoryError(f"tier '{name}' candidates must use provider/model IDs")
        tiers[name] = Tier(description=description, candidates=tuple(candidates))

    roles: dict[str, str] = {}
    for role in ROLES:
        tier = raw_roles.get(role)
        if not isinstance(tier, str) or tier not in tiers:
            raise FactoryError(f"role '{role}' references undefined tier '{tier}'")
        roles[role] = tier
    return ModelPolicy(roles=roles, tiers=tiers)


def validate_opencode_config(path: Path = OPENCODE_CONFIG_PATH) -> None:
    raw = _load_json(path)
    agents = raw.get("agent")
    if not isinstance(agents, dict):
        raise FactoryError(f"{path.name} must define agents")
    for role in ROLES:
        agent = agents.get(role)
        if not isinstance(agent, dict) or not isinstance(agent.get("model"), str):
            raise FactoryError(f"{path.name} is missing model configuration for '{role}'")


def require_commands(*commands: str) -> None:
    for command in commands:
        if shutil.which(command) is None:
            raise FactoryError(f"missing required command: {command}")


def require_herdr_context() -> None:
    if os.environ.get("HERDR_ENV") != "1" or not os.environ.get("HERDR_PANE_ID"):
        raise FactoryError("start Herdr in this directory, then run ./factory in its shell pane")


def prompt_timeout_ms() -> int:
    raw = os.environ.get("FACTORY_PROMPT_TIMEOUT_MS", "1800000")
    try:
        timeout = int(raw)
    except ValueError as error:
        raise FactoryError("FACTORY_PROMPT_TIMEOUT_MS must be an integer") from error
    if timeout <= 5000:
        raise FactoryError("FACTORY_PROMPT_TIMEOUT_MS must be greater than 5000")
    return timeout


def slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:32]
    return slug or "task"


def create_run(
    label: str,
    *,
    runs_root: Path = RUNS_ROOT,
    now: datetime | None = None,
) -> Path:
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    base_name = f"{timestamp}-{slugify(label)}"
    runs_root.mkdir(parents=True, exist_ok=True)
    suffix = 2
    candidate = runs_root / base_name
    while True:
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            candidate = runs_root / f"{base_name}-{suffix}"
            suffix += 1


def canonical_prompt_path(supplied: Path, runs_root: Path = RUNS_ROOT) -> Path:
    candidate = supplied if supplied.is_absolute() else FACTORY_ROOT / supplied
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise FactoryError(f"prompt file does not exist: {supplied}") from error
    if not resolved.is_file() or not resolved.is_relative_to(runs_root.resolve()):
        raise FactoryError("prompt files must be inside .factory/runs/")
    return resolved


def canonical_run_directory(supplied: Path, runs_root: Path = RUNS_ROOT) -> Path:
    candidate = supplied if supplied.is_absolute() else FACTORY_ROOT / supplied
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise FactoryError(f"run directory does not exist: {supplied}") from error
    if not resolved.is_dir() or resolved.parent != runs_root.resolve():
        raise FactoryError("run directories must be direct children of .factory/runs/")
    return resolved


def canonical_report_target(supplied: Path, run_directory: Path) -> Path:
    candidate = supplied if supplied.is_absolute() else FACTORY_ROOT / supplied
    try:
        resolved_parent = candidate.parent.resolve(strict=True)
    except OSError as error:
        raise FactoryError(f"report directory does not exist: {supplied.parent}") from error
    if resolved_parent != run_directory or candidate.suffix.lower() != ".md":
        raise FactoryError("report files must be Markdown files in the prompt's run directory")
    return resolved_parent / candidate.name
