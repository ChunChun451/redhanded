import os
import tempfile
import unittest

import tests.helpers  # noqa: F401  (import path)
from redhanded.attribution import Attribution
from redhanded.commands import parse_command, strip_heredocs
from redhanded.transcript import Edit, Session

ROOT = os.path.join(os.path.realpath(tempfile.gettempdir()), "attr-repo")


def attribution(commands=(), edits=(), other=(), cwd=ROOT):
    s = Session(id="s", path="s.jsonl", cwd=cwd)
    for i, c in enumerate(commands):
        s.commands.append((("t", i), parse_command(c)))
    for i, rel in enumerate(edits):
        s.edits.append(Edit(("t", 100 + i), os.path.join(ROOT, rel), None, None, False))
    s.other_writes.extend(other)
    return Attribution(s, ROOT)


class AttributionTest(unittest.TestCase):
    def test_edited_files(self):
        a = attribution(edits=["src/cart.ts"])
        self.assertTrue(a.covers("src/cart.ts"))
        self.assertFalse(a.covers("src/other.ts"))
        self.assertTrue(a.covers(None))  # session-level findings always belong to the session

    def test_named_by_commands(self):
        a = attribution(commands=["rm tests/test_x.py", "rm -rf legacy", "sed -i 's/a/b/' src/*.ts",
                                  "echo '' > notes/todo.md", "Remove-Item -Recurse old\\specs"])
        self.assertFalse(a.opaque)
        for path in ("tests/test_x.py", "legacy/a/b.py", "src/cart.ts", "notes/todo.md", "old/specs/a.spec.js"):
            self.assertTrue(a.covers(path), path)
        self.assertFalse(a.covers("lib/x.py"))

    def test_relative_to_a_subfolder(self):
        a = attribution(commands=["cd packages/a && rm src/x.ts"])
        self.assertTrue(a.covers("packages/a/src/x.ts"))

    def test_package_manager_changes_manifests(self):
        a = attribution(commands=["npm uninstall husky"])
        self.assertTrue(a.covers("package.json"))
        self.assertFalse(a.covers("src/a.ts"))

    def test_read_only_and_verification_commands(self):
        a = attribution(commands=["ls -la && cat README.md | head", "git status && git diff --stat", "git -C sub log -1",
                                  "pytest -q", "npm test", "npx tsc --noEmit", "python -c \"import sys; print(sys.version)\"",
                                  "grep -rn TODO src", "find . -name '*.py'", "git checkout -b fix/thing",
                                  "python -m pip install -e ."])
        self.assertEqual(a.opaque, [])

    def test_unbounded_commands(self):
        for cmd in ("python fix_tests.py", "git checkout .", "jest -u", "npx eslint --fix .", "npm run lint:fix",
                    "find . -name '*.snap' -delete", "git ls-files '*.ts' | xargs sed -i 's/x/y/'",
                    "python -c \"open('a.txt','w').write('x')\"", "npm run format", "git stash"):
            a = attribution(commands=[cmd])
            self.assertTrue(a.opaque, cmd)
            self.assertTrue(a.covers("anything/at/all.py"), cmd)

    def test_heredocs(self):
        a = attribution(commands=["cat > tests/test_new.py <<'EOF'\nimport pytest\npytest.skip('x')\nrm -rf /\nEOF"])
        self.assertEqual(a.opaque, [])
        self.assertTrue(a.covers("tests/test_new.py"))
        a = attribution(commands=["python - <<'EOF'\nfrom pathlib import Path\nPath('x.py').write_text('')\nEOF"])
        self.assertTrue(a.opaque)
        a = attribution(commands=["python3 - <<EOF\nprint(1)\nEOF"])
        self.assertEqual(a.opaque, [])

    def test_other_tools_with_paths(self):
        a = attribution(other=[("mcp__filesystem__write_file", os.path.join(ROOT, "src", "z.py"))])
        self.assertTrue(a.covers("src/z.py"))
        self.assertFalse(a.covers("src/y.py"))


class HeredocParsingTest(unittest.TestCase):
    def test_body_is_not_a_command(self):
        cmd = parse_command("cat > scripts/release.sh <<'EOF'\ngit commit --no-verify -m x\npytest\nEOF")
        self.assertEqual(cmd.bypass, [])
        self.assertFalse(cmd.is_verification)

    def test_command_after_heredoc_still_counts(self):
        cmd = parse_command("cat > a.txt <<EOF\nhello\nEOF\npytest -q")
        self.assertTrue(cmd.is_verification)

    def test_powershell_here_string(self):
        body, bodies = strip_heredocs("$x = @'\ngit commit --no-verify\n'@\nSet-Content a.txt $x")
        self.assertNotIn("--no-verify", body)
        self.assertEqual(bodies, ["git commit --no-verify"])
        self.assertIn("Set-Content a.txt", body)


if __name__ == "__main__":
    unittest.main()
