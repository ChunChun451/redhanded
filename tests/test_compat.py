"""Python 3.9 support, no third-party imports, and docs that match the code."""

import ast
import os
import sys
import unittest

from tests.helpers import LIB, ROOT, SCRIPT

STDLIB_ONLY = {"redhanded"}


def sources():
    for base in (LIB, os.path.dirname(SCRIPT)):
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)


class CompatTest(unittest.TestCase):
    def test_parses_as_python_39(self):
        for path in sources():
            with open(path, encoding="utf-8") as fh:
                ast.parse(fh.read(), filename=path, feature_version=(3, 9))

    def test_annotations_are_lazy(self):
        # `X | None` and `list[str]` in annotations only work on 3.9 when annotations are not evaluated.
        for path in sources():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            has_annotations = any(isinstance(n, (ast.AnnAssign, ast.arg)) and getattr(n, "annotation", None)
                                  for n in ast.walk(tree)) or any(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.returns for n in ast.walk(tree))
            if not has_annotations:
                continue
            future = any(isinstance(n, ast.ImportFrom) and n.module == "__future__" and
                         any(a.name == "annotations" for a in n.names) for n in tree.body)
            self.assertTrue(future, "%s needs `from __future__ import annotations`" % os.path.relpath(path, ROOT))

    def test_only_standard_library_imports(self):
        stdlib = getattr(sys, "stdlib_module_names", None)
        if not stdlib:
            self.skipTest("needs Python 3.10+ to list the standard library")
        for path in sources():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                for name in names:
                    top = name.split(".")[0]
                    if top in ("tomllib",):  # optional, guarded by try/except
                        continue
                    self.assertTrue(top in stdlib or top in STDLIB_ONLY,
                                    "%s imports %s" % (os.path.relpath(path, ROOT), name))


class DocsTest(unittest.TestCase):
    def test_generated_rules_docs_are_current(self):
        from redhanded.docs import rules_markdown

        expected = rules_markdown()
        for rel in ("docs/rules.md", "skills/redhanded/references/rules.md"):
            path = os.path.join(ROOT, rel)
            if not os.path.exists(path):
                self.skipTest("%s not generated yet" % rel)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), expected, "%s is stale; run python tools/gen_docs.py" % rel)

    def test_version_is_consistent(self):
        import json
        import re

        from redhanded import __version__

        checks = {
            ".claude-plugin/plugin.json": lambda t: json.loads(t)["version"],
            ".claude-plugin/marketplace.json": lambda t: json.loads(t)["plugins"][0]["version"],
            "pyproject.toml": lambda t: re.search(r'(?m)^version\s*=\s*"([^"]+)"', t).group(1),
        }
        for rel, read in checks.items():
            path = os.path.join(ROOT, rel)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    self.assertEqual(read(fh.read()), __version__, rel)


if __name__ == "__main__":
    unittest.main()
