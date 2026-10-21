from __future__ import annotations

import hashlib
import json
import shutil
import os
import re
import subprocess
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pcl_companion.models import VanillaVersion
from pcl_companion.services.vanilla_service import VanillaService, VanillaError


class MrpackError(RuntimeError):
    pass


class MrpackInstaller:
    def __init__(self, vanilla: VanillaService) -> None:
        self.vanilla = vanilla
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PCLCompanion/0.1"})
        retry = Retry(
            total=5, connect=5, read=4, backoff_factor=.7,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8))

    def inspect(self, archive: Path) -> dict:
        try:
            with zipfile.ZipFile(archive) as pack:
                payload = json.loads(pack.read("modrinth.index.json"))
            dependencies = dict(payload.get("dependencies") or {})
            return {
                "name": str(payload.get("name") or archive.stem),
                "version": str(payload.get("versionId") or ""),
                "minecraft": str(dependencies.get("minecraft") or ""),
                "fabric": str(dependencies.get("fabric-loader") or ""),
                "quilt": str(dependencies.get("quilt-loader") or ""),
                "forge": str(dependencies.get("forge") or ""),
                "neoforge": str(dependencies.get("neoforge") or ""),
                "files": len(payload.get("files") or []),
            }
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            raise MrpackError(f"无法读取 mrpack：{exc}") from exc

    def install(self, archive: Path, minecraft_dir: Path, instance_name: str, progress=None) -> Path:
        info = self.inspect(archive)
        if not info["minecraft"]:
            raise MrpackError("整合包没有声明 Minecraft 版本")
        target = minecraft_dir / "versions" / instance_name
        if target.exists():
            raise MrpackError("同名实例已经存在，请更换实例名称")
        staging = minecraft_dir / "downloads" / "PCLCompanion" / f"install-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        try:
            versions = self.vanilla.list_versions(True)
            base = next((item for item in versions if item.version_id == info["minecraft"]), None)
            if base is None:
                raise MrpackError(f"找不到原版 {info['minecraft']} 的元数据")
            self.vanilla.install_base(minecraft_dir, base)
            with zipfile.ZipFile(archive) as pack:
                index = json.loads(pack.read("modrinth.index.json"))
                files = list(index.get("files") or [])
                total = max(len(files), 1)
                for position, item in enumerate(files, 1):
                    env = item.get("env") or {}
                    if env.get("client") == "unsupported":
                        continue
                    relative = self._safe_relative(str(item.get("path") or ""))
                    destination = staging / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    self._download(
                        item.get("downloads") or [], destination, item.get("hashes") or {},
                        minecraft_dir / "downloads" / "PCLCompanion" / "file-cache",
                    )
                    if progress: progress(position / total * .85)
                self._extract_prefix(pack, "overrides/", staging)
                self._extract_prefix(pack, "client-overrides/", staging)
            if progress: progress(.88)
            profile = self._profile_json(instance_name, info, minecraft_dir)
            (staging / f"{instance_name}.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            staging.replace(target)
            if progress: progress(1.0)
            return target
        except MrpackError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except (OSError, ValueError, KeyError, requests.RequestException, zipfile.BadZipFile, VanillaError) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            if isinstance(exc, requests.RequestException) and ("SSL" in str(exc) or "EOF" in str(exc)):
                raise MrpackError("连接 Modrinth CDN 时中断。已下载文件已缓存，请点击“重试部署”。") from exc
            raise MrpackError(f"部署整合包失败：{exc}") from exc

    def _profile_json(self, instance_name: str, info: dict, minecraft_dir: Path) -> dict:
        if info["fabric"]:
            url = f"https://meta.fabricmc.net/v2/versions/loader/{info['minecraft']}/{info['fabric']}/profile/json"
            response = self.session.get(url, timeout=20); response.raise_for_status()
            profile = response.json(); profile["id"] = instance_name
            return profile
        if info["quilt"]:
            url = f"https://meta.quiltmc.org/v3/versions/loader/{info['minecraft']}/{info['quilt']}/profile/json"
            response = self.session.get(url, timeout=20); response.raise_for_status()
            profile = response.json(); profile["id"] = instance_name
            return profile
        if info["forge"]:
            loader_id = f"{info['minecraft']}-forge-{info['forge']}"
            url = (
                "https://maven.minecraftforge.net/net/minecraftforge/forge/"
                f"{info['minecraft']}-{info['forge']}/forge-{info['minecraft']}-{info['forge']}-installer.jar"
            )
            profile = self._run_loader_installer(url, loader_id, minecraft_dir)
            profile["id"] = instance_name
            return profile
        if info["neoforge"]:
            loader_id = f"neoforge-{info['neoforge']}"
            url = f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{info['neoforge']}/neoforge-{info['neoforge']}-installer.jar"
            profile = self._run_loader_installer(url, loader_id, minecraft_dir)
            profile["id"] = instance_name
            return profile
        return {"id": instance_name, "inheritsFrom": info["minecraft"], "type": "release"}

    def _run_loader_installer(self, url: str, loader_id: str, minecraft_dir: Path) -> dict:
        existing = self._find_loader_profile(minecraft_dir, loader_id)
        if existing:
            return existing
        java = self._find_modern_java(minecraft_dir)
        cache_dir = minecraft_dir / "downloads" / "PCLCompanion" / "installers"
        cache_dir.mkdir(parents=True, exist_ok=True)
        installer = cache_dir / f"{loader_id}-installer.jar"
        if not installer.is_file():
            temporary = installer.with_suffix(".part")
            with self.session.get(url, stream=True, timeout=(20, 180)) as response:
                response.raise_for_status()
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(1024 * 512):
                        if chunk:
                            output.write(chunk)
            temporary.replace(installer)
        self._prefetch_forge_mappings(installer, minecraft_dir)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            [str(java), "-Djavax.net.ssl.trustStoreType=Windows-ROOT", "-jar", str(installer), "--installClient", str(minecraft_dir)],
            cwd=str(installer.parent), capture_output=True, text=True, errors="replace",
            timeout=900, check=False, creationflags=flags,
        )
        profile = self._find_loader_profile(minecraft_dir, loader_id)
        if result.returncode != 0 or not profile:
            details = (result.stderr or result.stdout or "未知错误")[-1200:]
            raise MrpackError(f"加载器安装失败（退出码 {result.returncode}）：{details}")
        return profile

    def _prefetch_forge_mappings(self, installer: Path, minecraft_dir: Path) -> None:
        """Avoid Forge's Java TLS path for Mojang mappings on unstable networks."""
        try:
            with zipfile.ZipFile(installer) as archive:
                profile = json.loads(archive.read("install_profile.json"))
            coordinate = str(((profile.get("data") or {}).get("MOJMAPS") or {}).get("client") or "")
            match = re.search(r"net\.minecraft:client:([^:\]]+):mappings@txt", coordinate)
            if not match:
                return
            mapping_version = match.group(1)
            minecraft_version = mapping_version.split("-", 1)[0]
            target = (
                minecraft_dir / "libraries" / "net" / "minecraft" / "client" /
                mapping_version / f"client-{mapping_version}-mappings.txt"
            )
            if target.is_file():
                return
            version = next((item for item in self.vanilla.list_versions(True) if item.version_id == minecraft_version), None)
            if not version:
                return
            response = self.vanilla.session.get(version.metadata_url, timeout=30)
            response.raise_for_status()
            mapping = response.json().get("downloads", {}).get("client_mappings")
            if not mapping:
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            self.vanilla._download(mapping["url"], target, mapping.get("sha1", ""))
        except (OSError, ValueError, KeyError, requests.RequestException, zipfile.BadZipFile):
            return

    @staticmethod
    def _find_loader_profile(minecraft_dir: Path, loader_id: str) -> dict | None:
        exact = minecraft_dir / "versions" / loader_id / f"{loader_id}.json"
        candidates = [exact] if exact.is_file() else []
        candidates.extend((minecraft_dir / "versions").glob(f"*{loader_id}*/*.json"))
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(payload, dict) and payload.get("mainClass"):
                    return payload
            except (OSError, ValueError, TypeError):
                continue
        return None

    def _find_modern_java(self, minecraft_dir: Path) -> Path:
        candidates: list[Path] = []
        java_home = os.environ.get("JAVA_HOME")
        if java_home:
            candidates.append(Path(java_home) / "bin" / "java.exe")
        candidates.extend([
            Path("E:/world/Java21/bin/java.exe"),
            Path("C:/Program Files/Java/jdk-21/bin/java.exe"),
            Path("C:/Program Files/Java/jdk-17/bin/java.exe"),
        ])
        candidates.extend(minecraft_dir.glob("runtime/**/bin/java.exe"))
        path_java = shutil.which("java")
        if path_java:
            candidates.append(Path(path_java))
        seen: set[str] = set()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for candidate in candidates:
            key = str(candidate).casefold()
            if key in seen or not candidate.is_file():
                continue
            seen.add(key)
            try:
                result = subprocess.run(
                    [str(candidate), "-version"], capture_output=True, text=True,
                    timeout=8, check=False, creationflags=flags,
                )
                output = result.stderr + result.stdout
                match = re.search(r'version "(\d+)(?:\.(\d+))?', output)
                if match:
                    major = int(match.group(1))
                    if major == 1 and match.group(2):
                        major = int(match.group(2))
                    if major >= 17:
                        return candidate
            except (OSError, subprocess.SubprocessError, ValueError):
                continue
        raise MrpackError("安装 Forge/NeoForge 需要 Java 17 或更高版本，但没有找到可用 Java。")

    def _download(self, urls: list[str], target: Path, hashes: dict, cache_dir: Path) -> None:
        if not urls:
            raise MrpackError(f"文件没有下载地址：{target.name}")
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_key = str(hashes.get("sha512") or hashes.get("sha1") or hashlib.sha1(urls[0].encode()).hexdigest())
        cached = cache_dir / cache_key
        if cached.is_file() and self._verify_file(cached, hashes):
            shutil.copy2(cached, target)
            return
        cached.unlink(missing_ok=True)
        temporary = cached.with_suffix(".part")
        last_error: Exception | None = None
        for attempt in range(1, 6):
            for url in urls:
                try:
                    temporary.unlink(missing_ok=True)
                    with self.session.get(url, stream=True, timeout=(20, 120), headers={"Connection": "close"}) as response:
                        response.raise_for_status()
                        with temporary.open("wb") as output:
                            for chunk in response.iter_content(1024 * 256):
                                if chunk:
                                    output.write(chunk)
                    if not self._verify_file(temporary, hashes):
                        raise MrpackError(f"文件哈希校验失败：{target.name}")
                    temporary.replace(cached)
                    shutil.copy2(cached, target)
                    return
                except (requests.RequestException, OSError) as exc:
                    last_error = exc
            if attempt < 5:
                time.sleep(min(1.2 * (2 ** (attempt - 1)), 8))
        temporary.unlink(missing_ok=True)
        if isinstance(last_error, requests.RequestException):
            raise last_error
        raise MrpackError(f"文件下载失败：{target.name} · {last_error}")

    @staticmethod
    def _verify_file(path: Path, hashes: dict) -> bool:
        sha1 = hashlib.sha1(); sha512 = hashlib.sha512()
        try:
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    sha1.update(chunk); sha512.update(chunk)
        except OSError:
            return False
        if hashes.get("sha1") and sha1.hexdigest().casefold() != str(hashes["sha1"]).casefold():
            return False
        if hashes.get("sha512") and sha512.hexdigest().casefold() != str(hashes["sha512"]).casefold():
            return False
        return True

    @staticmethod
    def _safe_relative(value: str) -> Path:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise MrpackError(f"整合包包含不安全路径：{value}")
        return Path(*path.parts)

    def _extract_prefix(self, pack: zipfile.ZipFile, prefix: str, target: Path) -> None:
        for member in pack.infolist():
            if not member.filename.startswith(prefix) or member.is_dir():
                continue
            relative = self._safe_relative(member.filename[len(prefix):])
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with pack.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
