from __future__ import annotations

import re
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class LoaderError(RuntimeError):
    pass


class LoaderService:
    """Read official/public metadata and return only Minecraft-compatible builds."""

    IRIS_PROJECT_ID = "YL57xq9U"

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PCLCompanion/0.2 (https://github.com/libaba0311/PCL-Companion)"})
        retry = Retry(total=3, connect=3, read=2, backoff_factor=.5, status_forcelist=(429, 500, 502, 503, 504))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def compatible_versions(self, minecraft: str) -> dict[str, list[str]]:
        loaders: dict[str, list[str]] = {"Fabric": [], "Forge": [], "NeoForge": [], "Quilt": [], "Iris": [], "OptiFine": []}
        calls = (
            ("Fabric", self._fabric), ("Forge", self._forge), ("NeoForge", self._neoforge),
            ("Quilt", self._quilt), ("Iris", self._iris), ("OptiFine", self._optifine),
        )
        for name, callback in calls:
            try:
                loaders[name] = callback(minecraft)
            except (requests.RequestException, ValueError, TypeError, ET.ParseError):
                loaders[name] = []
        return loaders

    def _fabric(self, minecraft: str) -> list[str]:
        response = self.session.get(f"https://meta.fabricmc.net/v2/versions/loader/{minecraft}", timeout=15)
        response.raise_for_status()
        return [str(item["loader"]["version"]) for item in response.json() if item.get("loader", {}).get("version")]

    def _quilt(self, minecraft: str) -> list[str]:
        response = self.session.get(f"https://meta.quiltmc.org/v3/versions/loader/{minecraft}", timeout=15)
        response.raise_for_status()
        return [str(item["loader"]["version"]) for item in response.json() if item.get("loader", {}).get("version")]

    def _forge(self, minecraft: str) -> list[str]:
        response = self.session.get("https://files.minecraftforge.net/net/minecraftforge/forge/maven-metadata.json", timeout=20)
        response.raise_for_status()
        versions = response.json().get(minecraft) or []
        return [str(value).removeprefix(f"{minecraft}-") for value in reversed(versions)]

    def _neoforge(self, minecraft: str) -> list[str]:
        response = self.session.get("https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml", timeout=20)
        response.raise_for_status()
        values = [node.text or "" for node in ET.fromstring(response.text).findall("./versioning/versions/version")]
        if minecraft == "1.20.1":
            pattern = re.compile(r"^47\.1\.")
        else:
            parts = minecraft.split(".")
            prefix = ".".join(parts[1:]) + "." if len(parts) >= 3 else parts[-1] + ".0."
            pattern = re.compile(rf"^{re.escape(prefix)}")
        return [value for value in reversed(values) if pattern.match(value)]

    def _iris(self, minecraft: str) -> list[str]:
        response = self.session.get(
            f"https://api.modrinth.com/v2/project/{self.IRIS_PROJECT_ID}/version",
            params={"game_versions": f'["{minecraft}"]', "loaders": '["fabric"]'}, timeout=20,
        )
        response.raise_for_status()
        releases = list(response.json())
        stable = [item for item in releases if item.get("version_type") == "release"]
        selected = stable or releases
        return [str(item.get("version_number")) for item in selected if item.get("version_number")]

    def _optifine(self, minecraft: str) -> list[str]:
        response = self.session.get("https://optifine.net/downloads", timeout=20)
        response.raise_for_status()
        pattern = re.compile(rf"OptiFine_{re.escape(minecraft)}_([A-Za-z0-9_]+)(?:_pre\d+)?\.jar")
        values: list[str] = []
        for value in pattern.findall(response.text):
            if value not in values:
                values.append(value)
        return values

    def install_iris(self, minecraft: str, version_number: str, instance_dir: Path) -> Path:
        response = self.session.get(
            f"https://api.modrinth.com/v2/project/{self.IRIS_PROJECT_ID}/version",
            params={"game_versions": f'["{minecraft}"]', "loaders": '["fabric"]'}, timeout=20,
        )
        response.raise_for_status()
        release = next((item for item in response.json() if str(item.get("version_number")) == version_number), None)
        if not release:
            raise LoaderError("找不到所选 Iris 版本")
        mods_dir = instance_dir / "mods"
        target = self._download_modrinth_file(release, mods_dir)
        for dependency in release.get("dependencies") or []:
            if dependency.get("dependency_type") != "required":
                continue
            dependency_release = None
            if dependency.get("version_id"):
                item = self.session.get(f"https://api.modrinth.com/v2/version/{dependency['version_id']}", timeout=20)
                item.raise_for_status(); dependency_release = item.json()
            elif dependency.get("project_id"):
                item = self.session.get(
                    f"https://api.modrinth.com/v2/project/{dependency['project_id']}/version",
                    params={"game_versions": f'["{minecraft}"]', "loaders": '["fabric"]'}, timeout=20,
                )
                item.raise_for_status(); candidates = item.json(); dependency_release = candidates[0] if candidates else None
            if dependency_release:
                self._download_modrinth_file(dependency_release, mods_dir)
        return target

    def _download_modrinth_file(self, release: dict, target_dir: Path) -> Path:
        files = list(release.get("files") or [])
        file = next((item for item in files if item.get("primary")), files[0] if files else None)
        if not file or not file.get("url"):
            raise LoaderError("Modrinth 发布版本没有可下载文件")
        target = target_dir / str(file.get("filename") or f"{release.get('id', 'component')}.jar")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        digest = hashlib.sha512()
        with self.session.get(str(file["url"]), stream=True, timeout=(15, 120)) as download:
            download.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in download.iter_content(1024 * 256):
                    if chunk:
                        output.write(chunk); digest.update(chunk)
        expected = str((file.get("hashes") or {}).get("sha512") or "")
        if expected and digest.hexdigest().casefold() != expected.casefold():
            temporary.unlink(missing_ok=True)
            raise LoaderError(f"组件哈希校验失败：{target.name}")
        temporary.replace(target)
        return target

