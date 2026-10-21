from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock

from pcl_companion.models import CatalogProject
from pcl_companion.services.install_engine import InstallEngine
from pcl_companion.services.loader_service import LoaderService
from pcl_companion.services.modrinth_service import ModrinthService
from pcl_companion.services.mrpack_installer import MrpackError, MrpackInstaller
from pcl_companion.services.pcl_service import PclService
from pcl_companion.services.vanilla_service import VanillaService


class PclServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.minecraft = self.root / ".minecraft"
        (self.minecraft / "versions").mkdir(parents=True)
        self.executable = self.root / "PCL2.exe"
        self.executable.touch()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_instance(self, name: str, payload: dict) -> None:
        folder = self.minecraft / "versions" / name
        folder.mkdir()
        (folder / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_detects_configured_installation(self) -> None:
        installation = PclService().detect(str(self.executable), str(self.minecraft))
        self.assertIsNotNone(installation)
        assert installation is not None
        self.assertEqual(installation.minecraft_dir, self.minecraft)

    def test_parses_vanilla_fabric_and_forge(self) -> None:
        self.add_instance("1.20.1", {"id": "1.20.1", "javaVersion": {"majorVersion": 17}})
        self.add_instance(
            "Fabric 1.20.1",
            {"id": "fabric-loader-0.15.11-1.20.1", "inheritsFrom": "1.20.1",
             "libraries": [{"name": "net.fabricmc:fabric-loader:0.15.11"}]},
        )
        self.add_instance(
            "Forge 1.19.2",
            {"id": "1.19.2-forge-43.3.8", "inheritsFrom": "1.19.2",
             "libraries": [{"name": "net.minecraftforge:forge:1.19.2-43.3.8"}]},
        )
        self.add_instance(
            "PCL Forge style",
            {"id": "1.20.1-Forge_47.0.35-OptiFine_I5", "inheritsFrom": "1.20.1",
             "libraries": [{"name": "net.minecraftforge:accesstransformers:8.0.4"}]},
        )
        instances = {item.name: item for item in PclService().list_instances(self.minecraft)}
        self.assertEqual(instances["1.20.1"].loader, "Vanilla")
        self.assertEqual(instances["Fabric 1.20.1"].loader, "Fabric")
        self.assertEqual(instances["Fabric 1.20.1"].loader_version, "0.15.11")
        self.assertEqual(instances["Forge 1.19.2"].loader, "Forge")
        self.assertEqual(instances["PCL Forge style"].loader_version, "47.0.35")


class InstallEngineTests(unittest.TestCase):
    def test_validates_target_and_name(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            (root / "versions" / "Existing").mkdir(parents=True)
            engine = InstallEngine()
            self.assertEqual(engine.validate_target(root, "Fresh Pack"), [])
            self.assertIn("实例名称不能为空", engine.validate_target(root, " "))
            self.assertIn("同名实例已经存在", engine.validate_target(root, "Existing"))
            self.assertIn("实例名称包含 Windows 不允许的字符", engine.validate_target(root, "Bad/Name"))


class LoaderServiceTests(unittest.TestCase):
    def test_filters_forge_versions_for_selected_minecraft(self) -> None:
        service = LoaderService()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"1.20.1": ["1.20.1-47.1.0", "1.20.1-47.2.1"]}
        service.session.get = Mock(return_value=response)
        self.assertEqual(service._forge("1.20.1"), ["47.2.1", "47.1.0"])

    def test_neoforge_uses_minecraft_compatible_prefix(self) -> None:
        service = LoaderService()
        response = Mock()
        response.raise_for_status.return_value = None
        response.text = "<metadata><versioning><versions><version>21.0.10</version><version>21.1.20</version><version>21.1.21</version></versions></versioning></metadata>"
        service.session.get = Mock(return_value=response)
        self.assertEqual(service._neoforge("1.21.1"), ["21.1.21", "21.1.20"])

    def test_optifine_filters_selected_minecraft(self) -> None:
        service = LoaderService()
        response = Mock()
        response.raise_for_status.return_value = None
        response.text = "OptiFine_1.20.1_HD_U_I6.jar OptiFine_1.21.1_HD_U_J1.jar"
        service.session.get = Mock(return_value=response)
        self.assertEqual(service._optifine("1.20.1"), ["HD_U_I6"])


class ModrinthTests(unittest.TestCase):
    def test_maps_search_response_and_facets(self) -> None:
        service = ModrinthService()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"hits": [{
            "project_id": "abc", "slug": "demo", "title": "Demo Pack",
            "description": "Example", "downloads": 1234,
            "categories": ["fabric", "adventure"], "versions": ["1.20.1"],
            "project_type": "modpack",
        }]}
        service.session.get = Mock(return_value=response)
        projects = service.search_projects("demo", "mod", "1.20.1", "Fabric", "optimization")
        self.assertEqual(projects[0], CatalogProject.from_modrinth(response.json()["hits"][0]))
        params = service.session.get.call_args.kwargs["params"]
        self.assertEqual(
            json.loads(params["facets"]),
            [["project_type:mod"], ["versions:1.20.1"], ["categories:fabric"], ["categories:optimization"]],
        )


class MrpackTests(unittest.TestCase):
    def test_inspects_manifest_and_rejects_unsafe_path(self) -> None:
        installer = MrpackInstaller(VanillaService())
        with tempfile.TemporaryDirectory() as value:
            archive = Path(value) / "demo.mrpack"
            manifest = {
                "name": "Demo Pack", "versionId": "1.0.0",
                "dependencies": {"minecraft": "1.20.1", "fabric-loader": "0.15.11"},
                "files": [{"path": "mods/demo.jar"}],
            }
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("modrinth.index.json", json.dumps(manifest))
            info = installer.inspect(archive)
            self.assertEqual(info["minecraft"], "1.20.1")
            self.assertEqual(info["fabric"], "0.15.11")
            self.assertEqual(info["files"], 1)
            with self.assertRaises(MrpackError):
                installer._safe_relative("../escape.txt")


if __name__ == "__main__":
    unittest.main()
