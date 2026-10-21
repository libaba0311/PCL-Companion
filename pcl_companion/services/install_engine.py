from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class InstallComponent:
    component_type: str
    name: str
    version: str
    source: str
    required: bool = True


@dataclass(slots=True)
class InstallPlan:
    instance_name: str
    minecraft_version: str
    loader: str
    loader_version: str
    java_major: int | None
    target_directory: Path
    components: list[InstallComponent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class InstallEngine:
    """Boundary for the real installer.

    The UI may build and inspect plans now. Network download, staging, hash
    verification and atomic commit will be implemented behind this interface,
    keeping PCL process control out of the installer.
    """

    def validate_target(self, minecraft_dir: Path, instance_name: str) -> list[str]:
        errors: list[str] = []
        instance_name = instance_name.strip()
        if not minecraft_dir.is_dir():
            errors.append("Minecraft 文件夹不存在")
        if not (minecraft_dir / "versions").is_dir():
            errors.append("目标文件夹缺少 versions 目录")
        if not instance_name:
            errors.append("实例名称不能为空")
        if any(char in instance_name for char in '<>:"/\\|?*'):
            errors.append("实例名称包含 Windows 不允许的字符")
        if instance_name.endswith((".", " ")):
            errors.append("实例名称不能以句点或空格结尾")
        if (minecraft_dir / "versions" / instance_name).exists():
            errors.append("同名实例已经存在")
        return errors

    def staging_directory(self, minecraft_dir: Path, task_id: str) -> Path:
        return minecraft_dir / "downloads" / "PCLCompanion" / task_id
