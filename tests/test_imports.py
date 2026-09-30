"""DoD#5: machine-checked zero-third-party imports.

ast-walk of every repo .py file; absolute imports must be stdlib
(sys.stdlib_module_names) or local {fullstop, support}; fullstop modules must
import their siblings RELATIVELY only.
"""

import ast
import sys
import unittest
from pathlib import Path

import support

REPO = support.REPO_ROOT
LOCAL = {"fullstop", "support"}
PKG_DIR = REPO / "fullstop"


class ImportTests(unittest.TestCase):
    def _py_files(self):
        files = [p for p in REPO.rglob("*.py")
                 if not any(part in {".git", "__pycache__", ".smoke-out"}
                            for part in p.parts)]
        self.assertGreaterEqual(len(files), 30,
                                f"expected the full repo, found {len(files)} files")
        return sorted(files)

    def test_stdlib_or_local_only(self):
        for path in self._py_files():
            tree = ast.parse(path.read_text(encoding="utf-8"),
                             filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self._check(alias.name, path)
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        self._check(node.module, path)

    def test_intra_package_imports_are_relative_only(self):
        for path in PKG_DIR.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"),
                             filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(
                            alias.name.split(".")[0] == "fullstop",
                            f"{path}: import siblings relatively, got "
                            f"'import {alias.name}'")
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    self.assertFalse(
                        (node.module or "").split(".")[0] == "fullstop",
                        f"{path}: import siblings relatively, got "
                        f"'from {node.module} import ...'")

    def test_all_repo_modules_import(self):
        # Compile-level import sanity for every local module named above.
        import fullstop  # noqa: F401
        for mod in ("types", "redact", "manifest", "policy", "gate", "sandbox",
                    "protocol", "activity", "state", "provider", "agent", "cli"):
            __import__(f"fullstop.{mod}")
        for mod in ("base", "file", "shell", "web", "note", "browser"):
            __import__(f"fullstop.tools.{mod}")

    def _check(self, module: str, path: Path):
        root = module.split(".")[0]
        self.assertIn(
            root, set(sys.stdlib_module_names) | LOCAL,
            f"{path}: non-stdlib import '{module}' "
            f"(stdlib-only law, CHARTER.md:28)")


if __name__ == "__main__":
    unittest.main()
