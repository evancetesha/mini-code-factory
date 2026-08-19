from __future__ import annotations

import os
from dataclasses import dataclass

from software_factory.config import (
    FACTORY_ROOT,
    MODEL_RUNTIME_ROOT,
    ROLES,
    FactoryError,
    ModelPolicy,
    load_model_policy,
)
from software_factory.process import run_command


@dataclass(frozen=True)
class ResolvedModel:
    role: str
    tier: str
    model: str


def parse_model_catalog(output: str) -> set[str]:
    models: set[str] = set()
    for line in output.splitlines():
        for token in line.split():
            if "/" in token:
                models.add(token)
    return models


def available_models() -> set[str]:
    completed = run_command(["opencode", "models"], cwd=FACTORY_ROOT.parent)
    return parse_model_catalog(completed.stdout)


def resolve_policy(policy: ModelPolicy, catalog: set[str]) -> list[ResolvedModel]:
    resolved: list[ResolvedModel] = []
    for role in ROLES:
        tier_name = policy.roles[role]
        model = next(
            (candidate for candidate in policy.tiers[tier_name].candidates if candidate in catalog),
            None,
        )
        if model is None:
            raise FactoryError(
                f"no available OpenCode model matches tier '{tier_name}' for role '{role}'; "
                "edit model-tiers.json"
            )
        resolved.append(ResolvedModel(role=role, tier=tier_name, model=model))
    return resolved


def write_resolution(models: list[ResolvedModel]) -> None:
    MODEL_RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    for resolved in models:
        target = MODEL_RUNTIME_ROOT / resolved.role
        temporary = target.with_suffix(".tmp")
        temporary.write_text(resolved.model, encoding="utf-8")
        os.replace(temporary, target)


def resolve_and_write_models() -> list[ResolvedModel]:
    resolved = resolve_policy(load_model_policy(), available_models())
    write_resolution(resolved)
    return resolved


def resolved_model_for_role(role: str) -> ResolvedModel:
    policy = load_model_policy()
    if role not in policy.roles:
        raise FactoryError(f"unknown factory role: {role}")
    path = MODEL_RUNTIME_ROOT / role
    try:
        model = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise FactoryError("model tiers are unresolved; run ./factory models") from error
    if not model:
        raise FactoryError(f"resolved model is empty for role '{role}'")
    return ResolvedModel(role=role, tier=policy.roles[role], model=model)


def format_resolution(models: list[ResolvedModel]) -> str:
    rows = [("ROLE", "TIER", "RESOLVED MODEL")]
    rows.extend((item.role, item.tier, item.model) for item in models)
    role_width = max(len(row[0]) for row in rows) + 2
    tier_width = max(len(row[1]) for row in rows) + 2
    return "\n".join(
        f"{role:<{role_width}}{tier:<{tier_width}}{model}" for role, tier, model in rows
    )
