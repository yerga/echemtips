"""Executable guards for documentation coverage and internal consistency."""

from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest
from urllib.parse import unquote

from echemtips.data import DataRecorder
from echemtips.models import ScanHoppingCVParameters


ROOT = Path(__file__).resolve().parent.parent
MARKDOWN_LINK = re.compile(r"!?(?:\[[^]]*\])\(([^)]+)\)")


class DocumentationTests(unittest.TestCase):
    """Keep public Python and Markdown contracts discoverable as code changes."""

    def test_public_python_objects_have_docstrings(self) -> None:
        """Require module and public object docs without importing GUI modules."""
        missing: list[str] = []
        for path in sorted((ROOT / "echemtips").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if ast.get_docstring(tree) is None:
                missing.append(f"{path.relative_to(ROOT)}: module")
            for node in ast.walk(tree):
                if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith("_") or ast.get_docstring(node) is not None:
                    continue
                missing.append(f"{path.relative_to(ROOT)}:{node.lineno}: {node.name}")
        self.assertEqual([], missing, "Missing public docstrings:\n" + "\n".join(missing))

    def test_minimum_python_syntax(self) -> None:
        """Catch newer syntax locally even when tests run on a newer Python."""
        for path in sorted((ROOT / "echemtips").rglob("*.py")):
            with self.subTest(module=str(path.relative_to(ROOT))):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path),
                          feature_version=(3, 11))

    def test_internal_markdown_links_resolve(self) -> None:
        """Reject broken relative file links in public Markdown sources."""
        documents = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "CHANGELOG.md"]
        documents.extend(sorted((ROOT / "docs").rglob("*.md")))
        missing: list[str] = []
        for document in documents:
            for match in MARKDOWN_LINK.finditer(document.read_text(encoding="utf-8")):
                raw_target = match.group(1).strip().split(maxsplit=1)[0].strip("<>")
                target = unquote(raw_target.split("#", 1)[0])
                if not target or "://" in target or target.startswith(("mailto:", "#")):
                    continue
                resolved = (document.parent / target).resolve()
                if not resolved.exists():
                    missing.append(f"{document.relative_to(ROOT)} -> {raw_target}")
        self.assertEqual([], missing, "Broken documentation links:\n" + "\n".join(missing))

    def test_recording_schema_columns_are_documented(self) -> None:
        """Keep the versioned data reference synchronized with recorder fields."""
        text = (ROOT / "docs" / "DATA_FORMAT.md").read_text(encoding="utf-8")
        standard = DataRecorder._fields_for_parameters(None)
        scan = DataRecorder._fields_for_parameters(ScanHoppingCVParameters())
        self.assertEqual(("scan_pixel",), tuple(name for name in scan if name not in standard))
        for column in scan:
            self.assertIn(f"`{column}`", text)
        self.assertIn("recording schema version 2", text.casefold())


if __name__ == "__main__":
    unittest.main()
