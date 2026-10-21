from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pcl_companion.models import CatalogProject, ProjectFile, ProjectVersion
from pcl_companion.config import APP_DIR


class CatalogError(RuntimeError):
    pass


class ModrinthService:
    BASE_URL = "https://api.modrinth.com/v2"
    USER_AGENT = "pcl-companion/0.1.0 (https://github.com/)"

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
        retry = Retry(
            total=3,
            connect=3,
            read=2,
            backoff_factor=0.45,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=16)
        self.session.mount("https://", adapter)

    def search_projects(
        self,
        query: str = "",
        project_type: str = "modpack",
        game_version: str = "",
        loader: str = "",
        category: str = "",
        limit: int = 30,
    ) -> list[CatalogProject]:
        projects, _ = self.search_projects_page(query, project_type, game_version, loader, category, limit, 0)
        return projects

    def search_projects_page(
        self,
        query: str = "",
        project_type: str = "modpack",
        game_version: str = "",
        loader: str = "",
        category: str = "",
        limit: int = 8,
        offset: int = 0,
    ) -> tuple[list[CatalogProject], int]:
        facets: list[list[str]] = [[f"project_type:{project_type}"]]
        if game_version:
            facets.append([f"versions:{game_version}"])
        if loader:
            facets.append([f"categories:{loader.casefold()}"])
        if category:
            facets.append([f"categories:{category}"])
        params: dict[str, Any] = {
            "query": query,
            "limit": min(max(limit, 1), 100),
            "offset": max(offset, 0),
            "index": "downloads" if not query else "relevance",
            "facets": json.dumps(facets, separators=(",", ":")),
        }
        try:
            response = self.session.get(f"{self.BASE_URL}/search", params=params, timeout=12)
            response.raise_for_status()
            payload = response.json()
            projects = [CatalogProject.from_modrinth(item) for item in payload.get("hits", [])]
            return projects, int(payload.get("total_hits") or len(projects))
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise CatalogError(f"Modrinth 请求失败：{exc}") from exc

    def search_modpacks(
        self,
        query: str = "",
        game_version: str = "",
        loader: str = "",
        limit: int = 30,
    ) -> list[CatalogProject]:
        return self.search_projects(query, "modpack", game_version, loader, "", limit)

    def get_game_versions(self) -> list[str]:
        try:
            response = self.session.get(f"{self.BASE_URL}/tag/game_version", timeout=12)
            response.raise_for_status()
            return [
                str(item.get("version")) for item in response.json()
                if item.get("version") and item.get("version_type") == "release"
            ]
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise CatalogError(f"读取游戏版本失败：{exc}") from exc

    def get_icon(self, url: str) -> bytes:
        if not url:
            return b""
        cache_dir = APP_DIR / "cache" / "icons"
        cache_path = cache_dir / hashlib.sha1(url.encode("utf-8")).hexdigest()
        try:
            if cache_path.is_file():
                return cache_path.read_bytes()
        except OSError:
            pass
        try:
            response = self.session.get(url, timeout=12)
            response.raise_for_status()
            data = response.content
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(data)
            except OSError:
                pass
            return data
        except requests.RequestException:
            return b""

    def get_versions(
        self,
        project_id: str,
        game_version: str = "",
        loader: str = "",
    ) -> list[ProjectVersion]:
        params: dict[str, str] = {}
        if game_version:
            params["game_versions"] = json.dumps([game_version])
        if loader:
            params["loaders"] = json.dumps([loader.casefold()])
        try:
            response = self.session.get(
                f"{self.BASE_URL}/project/{project_id}/version",
                params=params,
                timeout=15,
            )
            response.raise_for_status()
            versions: list[ProjectVersion] = []
            for item in response.json():
                files = [
                    ProjectFile(
                        filename=str(file.get("filename") or "download.bin"),
                        url=str(file.get("url") or ""),
                        size=int(file.get("size") or 0),
                        primary=bool(file.get("primary")),
                        hashes=dict(file.get("hashes") or {}),
                    )
                    for file in item.get("files", [])
                    if file.get("url")
                ]
                versions.append(ProjectVersion(
                    version_id=str(item.get("id") or ""),
                    name=str(item.get("name") or item.get("version_number") or "未命名版本"),
                    version_number=str(item.get("version_number") or ""),
                    game_versions=list(item.get("game_versions") or []),
                    loaders=list(item.get("loaders") or []),
                    published=str(item.get("date_published") or ""),
                    files=files,
                ))
            return versions
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise CatalogError(f"读取版本失败：{exc}") from exc

    def download_file(self, file: ProjectFile, target: Path, progress=None) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            with self.session.get(file.url, stream=True, timeout=(15, 60)) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length") or file.size or 0)
                received = 0
                sha512 = hashlib.sha512()
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(1024 * 256):
                        if not chunk:
                            continue
                        output.write(chunk)
                        sha512.update(chunk)
                        received += len(chunk)
                        if progress:
                            progress(received, total)
            expected = file.hashes.get("sha512")
            if expected and sha512.hexdigest().casefold() != expected.casefold():
                raise CatalogError("文件 SHA-512 校验失败，已阻止保存")
            temporary.replace(target)
            return target
        except (requests.RequestException, OSError, CatalogError) as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise CatalogError(f"下载失败：{exc}") from exc
