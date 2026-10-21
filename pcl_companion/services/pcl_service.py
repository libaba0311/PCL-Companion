from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from pcl_companion.models import MinecraftInstance, PclInstallation


PCL_NAMES = ("Plain Craft Launcher 2.exe", "PCL2.exe", "PCL.exe")


class PclService:
    def detect(
        self,
        configured_executable: str = "",
        configured_minecraft_dir: str = "",
    ) -> PclInstallation | None:
        candidates: list[Path] = []
        if configured_executable:
            candidates.append(Path(configured_executable))

        running = self._running_pcl_paths()
        candidates.extend(running)
        candidates.extend(Path.cwd() / name for name in PCL_NAMES)
        candidates.extend(Path("E:/pcl") / name for name in PCL_NAMES)

        seen: set[str] = set()
        for executable in candidates:
            key = str(executable).casefold()
            if key in seen:
                continue
            seen.add(key)
            if not executable.is_file():
                continue
            configured_dir = Path(configured_minecraft_dir) if configured_minecraft_dir else None
            minecraft_dir = (
                configured_dir
                if configured_dir and (configured_dir / "versions").is_dir()
                else self._find_minecraft_dir(executable.parent)
            )
            if minecraft_dir is None:
                continue
            return PclInstallation(
                executable=executable,
                minecraft_dir=minecraft_dir,
                version=self._read_file_version(executable),
                is_running=any(self._same_path(executable, item) for item in running),
            )
        return None

    def list_instances(self, minecraft_dir: Path) -> list[MinecraftInstance]:
        versions_dir = minecraft_dir / "versions"
        if not versions_dir.is_dir():
            return []
        results = [self._parse_instance(folder) for folder in versions_dir.iterdir() if folder.is_dir()]
        return sorted(results, key=lambda item: (not item.valid, item.name.casefold()))

    def launch(self, installation: PclInstallation) -> None:
        subprocess.Popen(
            [str(installation.executable)],
            cwd=str(installation.executable.parent),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )

    def _parse_instance(self, folder: Path) -> MinecraftInstance:
        json_files = [item for item in folder.glob("*.json") if item.name not in {"usercache.json", "usernamecache.json"}]
        preferred = folder / f"{folder.name}.json"
        if preferred.is_file():
            json_files = [preferred] + [item for item in json_files if item != preferred]
        if not json_files:
            return MinecraftInstance(folder.name, folder, valid=False, error="缺少版本 JSON")

        last_error = "无法读取版本 JSON"
        for json_path in json_files:
            try:
                data = json.loads(json_path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict) or not data.get("id"):
                    continue
                loader, loader_version = self._detect_loader(data)
                minecraft_version = self._detect_minecraft_version(data, folder.name)
                java_major = (data.get("javaVersion") or {}).get("majorVersion")
                icon = folder / "PCL" / "Logo.png"
                return MinecraftInstance(
                    name=folder.name,
                    path=folder,
                    minecraft_version=minecraft_version,
                    loader=loader,
                    loader_version=loader_version,
                    java_major=int(java_major) if str(java_major).isdigit() else None,
                    valid=True,
                    icon_path=icon if icon.is_file() else None,
                )
            except (OSError, ValueError, TypeError) as exc:
                last_error = str(exc)
        return MinecraftInstance(folder.name, folder, valid=False, error=last_error)

    @staticmethod
    def _detect_loader(data: dict) -> tuple[str, str]:
        game_args = (data.get("arguments") or {}).get("game") or []
        for flag, loader_name in (("--fml.forgeVersion", "Forge"), ("--fml.neoForgeVersion", "NeoForge")):
            if flag in game_args:
                position = game_args.index(flag)
                if position + 1 < len(game_args) and isinstance(game_args[position + 1], str):
                    return loader_name, game_args[position + 1]
        library_names = [
            str(item.get("name", ""))
            for item in data.get("libraries", [])
            if isinstance(item, dict)
        ]
        for library in library_names:
            parts = library.split(":")
            if len(parts) < 3:
                continue
            group, artifact, version = parts[0], parts[1], parts[2]
            if group == "net.fabricmc" and artifact == "fabric-loader":
                return "Fabric", version
            if group == "org.quiltmc" and artifact == "quilt-loader":
                return "Quilt", version
            if group == "net.neoforged" and artifact == "neoforge":
                return "NeoForge", version
            if group == "net.minecraftforge" and artifact == "forge":
                return "Forge", version.rsplit("-", 1)[-1]

        haystack = " ".join(
            [str(data.get("id", "")), str(data.get("mainClass", ""))]
            + library_names
        )
        patterns = (
            ("NeoForge", r"(?:^|[^a-z])neoforge[_:/-]([0-9]+(?:\.[0-9]+)+)"),
            ("Forge", r"(?:^|[^a-z])forge[_:/-]([0-9]+(?:\.[0-9]+)+)"),
            ("Fabric", r"(?:^|[^a-z])fabric(?:-loader)?[_:/-]([0-9]+(?:\.[0-9]+)+)"),
            ("Quilt", r"(?:^|[^a-z])quilt(?:-loader)?[_:/-]([0-9]+(?:\.[0-9]+)+)"),
        )
        for name, pattern in patterns:
            match = re.search(pattern, haystack, re.IGNORECASE)
            if match:
                return name, match.group(1)
        if "fabric" in haystack.casefold():
            return "Fabric", ""
        if "forge" in haystack.casefold():
            return "Forge", ""
        return "Vanilla", ""

    @staticmethod
    def _detect_minecraft_version(data: dict, fallback: str) -> str:
        inherited = data.get("inheritsFrom")
        if inherited:
            return str(inherited)
        client_version = data.get("clientVersion")
        if client_version:
            return str(client_version)
        value = str(data.get("id") or fallback)
        match = re.search(r"(?<!\d)(1\.\d+(?:\.\d+)?)(?!\d)", value)
        return match.group(1) if match else value

    @staticmethod
    def _find_minecraft_dir(base: Path) -> Path | None:
        direct = base / ".minecraft"
        if (direct / "versions").is_dir():
            return direct
        if (base / "versions").is_dir():
            return base
        return None

    @staticmethod
    def _running_pcl_paths() -> list[Path]:
        if os.name != "nt":
            return []
        command = (
            "Get-CimInstance Win32_Process | "
            "Where-Object {$_.Name -match 'PCL|Plain Craft'} | "
            "ForEach-Object {$_.ExecutablePath}"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            return [Path(line.strip()) for line in completed.stdout.splitlines() if line.strip()]
        except (OSError, subprocess.SubprocessError):
            return []

    @staticmethod
    def _read_file_version(executable: Path) -> str:
        if os.name != "nt":
            return "Unknown"
        escaped = str(executable).replace("'", "''")
        command = f"(Get-Item -LiteralPath '{escaped}').VersionInfo.FileVersion"
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            return result.stdout.strip() or "Unknown"
        except (OSError, subprocess.SubprocessError):
            return "Unknown"

    @staticmethod
    def _same_path(left: Path, right: Path) -> bool:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
