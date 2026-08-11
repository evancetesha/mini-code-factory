from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from software_factory.config import FACTORY_ROOT, FactoryError


def run_command(
    arguments: Sequence[str],
    *,
    cwd: Path = FACTORY_ROOT,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(arguments),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise FactoryError(f"could not run {arguments[0]}: {error}") from error
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise FactoryError(f"{' '.join(arguments[:3])} failed: {detail}")
    return completed


def parse_json_output(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise FactoryError(f"command returned invalid JSON: {completed.stdout.strip()}") from error
    if not isinstance(value, dict):
        raise FactoryError("command returned a non-object JSON response")
    return value
