from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class PclInstallation:
    executable: Path
    minecraft_dir: Path
    version: str = "Unknown"
    is_running: bool = False


@dataclass(slots=True)
class MinecraftInstance:
    name: str
    path: Path
    minecraft_version: str = "Unknown"
    loader: str = "Vanilla"
    loader_version: str = ""
    java_major: int | None = None
    valid: bool = True
    icon_path: Path | None = None
    error: str = ""


@dataclass(slots=True)
class CatalogProject:
    project_id: str
    slug: str
    title: str
    description: str
    source: str
    downloads: int = 0
    icon_url: str = ""
    categories: list[str] = field(default_factory=list)
    latest_version: str = ""
    loaders: list[str] = field(default_factory=list)
    project_type: str = "modpack"

    @classmethod
    def from_modrinth(cls, data: dict[str, Any]) -> "CatalogProject":
        versions = data.get("versions") or []
        categories = data.get("categories") or []
        known_loaders = [
            value for value in ("fabric", "forge", "neoforge", "quilt")
            if value in categories
        ]
        return cls(
            project_id=str(data.get("project_id", "")),
            slug=str(data.get("slug", "")),
            title=str(data.get("title", "未命名项目")),
            description=str(data.get("description", "")),
            source="Modrinth",
            downloads=int(data.get("downloads") or 0),
            icon_url=str(data.get("icon_url") or ""),
            categories=list(categories),
            latest_version=str(versions[0]) if versions else "",
            loaders=known_loaders,
            project_type=str(data.get("project_type") or "modpack"),
        )


@dataclass(slots=True)
class ProjectFile:
    filename: str
    url: str
    size: int
    primary: bool = False
    hashes: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ProjectVersion:
    version_id: str
    name: str
    version_number: str
    game_versions: list[str]
    loaders: list[str]
    published: str
    files: list[ProjectFile] = field(default_factory=list)

    @property
    def primary_file(self) -> ProjectFile | None:
        return next((item for item in self.files if item.primary), self.files[0] if self.files else None)


@dataclass(slots=True)
class InstallTask:
    task_id: str
    name: str
    source: str
    state: str = "等待分析"
    progress: float = 0.0
    detail: str = ""
    project_type: str = ""
    file_path: str = ""
    instance_name: str = ""


@dataclass(slots=True)
class VanillaVersion:
    version_id: str
    version_type: str
    release_time: str
    metadata_url: str
