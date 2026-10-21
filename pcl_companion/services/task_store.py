from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from pcl_companion.config import APP_DIR
from pcl_companion.models import InstallTask


TASK_PATH = APP_DIR / "tasks.json"


class TaskStore:
    def load(self) -> list[InstallTask]:
        try:
            payload = json.loads(TASK_PATH.read_text(encoding="utf-8"))
            return [InstallTask(**item) for item in payload if isinstance(item, dict)]
        except (OSError, ValueError, TypeError):
            return []

    def save(self, tasks: list[InstallTask]) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        temporary = TASK_PATH.with_suffix(".tmp")
        temporary.write_text(
            json.dumps([asdict(item) for item in tasks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(TASK_PATH)
