"""Installed application folders can be opened without exposing system trees."""
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import Mock, patch

from asset_studio import workspace


class WorkspaceAccessTests(unittest.TestCase):
    def test_installed_application_folders(self):
        for path in (
            r"C:\Program Files (x86)\Blackmagic Design",
            r"C:\Program Files\Blackmagic Design\DaVinci Resolve",
            r"D:\PROGRAM FILES (X86)\Application",
            r"C:\Users\Drog\Documents\Project",
        ):
            with self.subTest(path=path):
                self.assertTrue(workspace.openable(PureWindowsPath(path)))

    def test_system_trees_and_containers_remain_blocked(self):
        for path in (
            "C:\\", r"C:\Program Files", r"C:\Program Files (x86)",
            r"C:\Windows", r"C:\Windows\System32",
            r"C:\ProgramData\Application", r"C:\$Recycle.Bin\Contents",
        ):
            with self.subTest(path=path):
                self.assertFalse(workspace.openable(PureWindowsPath(path)))

    def test_open_application_persists_and_scopes_browsing(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            app = base / "Program Files (x86)" / "Blackmagic Design"
            child = app / "Blackmagic RAW"
            sibling = app.parent / "Another Application"
            child.mkdir(parents=True)
            sibling.mkdir()
            config = Mock()
            config.get.side_effect = lambda key, *args: roots if key == "workspace_roots" else None
            roots = []

            def update(patch_):
                roots[:] = patch_["workspace_roots"]

            config.update.side_effect = update
            with patch.object(workspace, "settings", config), \
                    patch.object(workspace, "_project_roots", return_value=[]):
                workspace.invalidate_roots()
                try:
                    result = workspace.add_root(str(app))
                    self.assertTrue(result["ok"])
                    self.assertEqual(roots, [str(app.resolve())])
                    self.assertEqual(workspace._check_browse(str(child)), child.resolve())
                    with self.assertRaises(PermissionError):
                        workspace._check_browse(str(sibling))
                    workspace.add_root(str(app))
                    self.assertEqual(len(roots), 1)
                finally:
                    workspace.invalidate_roots()


if __name__ == "__main__":
    unittest.main()
