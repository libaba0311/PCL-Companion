from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PCLCompanion"
CONFIG_PATH = APP_DIR / "settings.json"


@dataclass(slots=True)
class AppSettings:
    pcl_executable: str = ""
    minecraft_dir: str = ""
    close_with_pcl: bool = False
    launch_pcl_on_start: bool = False
    theme: str = "dark"
    deployment_mode: str = "pcl"
    custom_minecraft_dir: str = ""

    @classmethod
    def load(cls) -> "AppSettings":
        try:
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return cls(**{key: payload[key] for key in cls.__annotations__ if key in payload})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        temp = CONFIG_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(CONFIG_PATH)
