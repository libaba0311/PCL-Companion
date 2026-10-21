from __future__ import annotations

import hashlib
import json
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pcl_companion.models import VanillaVersion


class VanillaError(RuntimeError):
    pass


class VanillaService:
    MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PCLCompanion/0.1"})
        retry = Retry(total=3, connect=3, read=2, backoff_factor=.4, status_forcelist=(429, 500, 502, 503, 504))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def list_versions(self, include_snapshots: bool = True) -> list[VanillaVersion]:
        try:
            response = self.session.get(self.MANIFEST, timeout=15)
            response.raise_for_status()
            result = []
            for item in response.json().get("versions", []):
                kind = str(item.get("type") or "")
                if not include_snapshots and kind != "release":
                    continue
                result.append(VanillaVersion(
                    version_id=str(item.get("id") or ""), version_type=kind,
                    release_time=str(item.get("releaseTime") or ""),
                    metadata_url=str(item.get("url") or ""),
                ))
            return result
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise VanillaError(f"读取原版版本失败：{exc}") from exc

    def install_base(self, minecraft_dir: Path, version: VanillaVersion, progress=None) -> Path:
        try:
            metadata_response = self.session.get(version.metadata_url, timeout=20)
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            client = metadata["downloads"]["client"]
            target_dir = minecraft_dir / "versions" / version.version_id
            target_dir.mkdir(parents=True, exist_ok=True)
            json_target = target_dir / f"{version.version_id}.json"
            jar_target = target_dir / f"{version.version_id}.jar"
            json_target.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            self._download(client["url"], jar_target, client.get("sha1", ""), progress)
            return target_dir
        except (requests.RequestException, OSError, KeyError, ValueError, TypeError) as exc:
            raise VanillaError(f"安装原版基础文件失败：{exc}") from exc

    def _download(self, url: str, target: Path, sha1: str = "", progress=None) -> None:
        temporary = target.with_suffix(target.suffix + ".part")
        with self.session.get(url, stream=True, timeout=(15, 90)) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            digest = hashlib.sha1()
            with temporary.open("wb") as output:
                for chunk in response.iter_content(1024 * 256):
                    if chunk:
                        output.write(chunk); digest.update(chunk); received += len(chunk)
                        if progress: progress(received, total)
        if sha1 and digest.hexdigest().casefold() != sha1.casefold():
            temporary.unlink(missing_ok=True)
            raise VanillaError("原版客户端 SHA-1 校验失败")
        temporary.replace(target)
