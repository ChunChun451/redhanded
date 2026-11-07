"""End to end through git: scopes, snapshots, settings, suppressions."""

import os
import time
import unittest

from tests import fixtures
from tests.helpers import RepoTestCase, rules_of
from redhanded import engine, gitio
from redhanded.model import Severity
from redhanded.settings import load


def scan(root, subdir=None, settings=None, **scope_kw):
    if scope_kw:
        scope = engine.resolve_scope(root, subdir=subdir, **scope_kw)
    else:
        scope, _note = engine.auto_scope(root, subdir=subdir)
    return engine.scan_git(root, scope, settings or load(root))


class GamedRepoTest(RepoTestCase):
    def test_every_planted_hack_is_found(self):
        fixtures.build(self.repo)
        fixtures.apply(self.repo, fixtures.GAMED)
        report = scan(self.repo.path)
        found = set(rules_of(report))
        self.assertEqual(fixtures.EXPECTED_RULES - found, set())
        self.assertEqual(report.verdict, "suspicious")
        self.assertEqual(report.files_checked, 7)

    def test_honest_fix_is_clean(self):
        fixtures.build(self.repo)
        fixtures.apply(self.repo, fixtures.HONEST)
        report = scan(self.repo.path)
        self.assertEqual([f for f in report.findings if f.severity >= Severity.MEDIUM], [])
        self.assertEqual(report.verdict, "clean")


class ScopeTest(RepoTestCase):
    def setUp(self):
        super().setUp()
        fixtures.build(self.repo)

    def test_uncommitted_includes_untracked_files(self):
        self.repo.write("tests/test_new.py", "import pytest\n\n\n@pytest.mark.skip\ndef test_x():\n    assert f() == 1\n")
        report = scan(self.repo.path)
        self.assertIn("test-skipped", rules_of(report))

    def test_last_commit(self):
        self.repo.write("src/cart.test.ts", fixtures.GAMED["src/cart.test.ts"])
        self.repo.commit("skip a test")
        report = scan(self.repo.path, last_commit=True)
        self.assertIn("test-skipped", rules_of(report))
        auto = scan(self.repo.path)  # nothing uncommitted, no branch: falls back to the last commit
        self.assertIn("test-skipped", rules_of(auto))

    def test_branch_vs_main(self):
        self.repo.git("checkout", "-q", "-b", "feature")
        self.repo.write("src/cart.test.ts", fixtures.GAMED["src/cart.test.ts"])
        self.repo.commit("one")
        self.repo.write("tsconfig.json", fixtures.GAMED["tsconfig.json"])
        self.repo.commit("two")
        report = scan(self.repo.path, branch=True)
        self.assertTrue({"test-skipped", "type-or-lint-config-weakened"} <= set(rules_of(report)))
        self.assertIn("vs main", report.scope)

    def test_staged_only(self):
        self.repo.write("src/cart.test.ts", fixtures.GAMED["src/cart.test.ts"])
        self.repo.git("add", "src/cart.test.ts")
        self.repo.write("tsconfig.json", fixtures.GAMED["tsconfig.json"])  # not staged
        report = scan(self.repo.path, staged=True)
        rules = rules_of(report)
        self.assertIn("test-skipped", rules)
        self.assertNotIn("type-or-lint-config-weakened", rules)

    def test_base_head_range(self):
        base = self.repo.git("rev-parse", "HEAD").strip()
        self.repo.write("package.json", fixtures.GAMED["package.json"])
        head = self.repo.commit("neuter")
        report = scan(self.repo.path, base=base, head=head)
        self.assertEqual(rules_of(report), ["test-script-weakened"])

    def test_deleted_file(self):
        self.repo.remove("tests/test_api.py")
        report = scan(self.repo.path)
        self.assertIn("test-deleted", rules_of(report))

    def test_same_second_same_size_edit_is_seen(self):
        # git's racy-index detection: an edit in the same second as the index write, with the same size.
        self.repo.write("src/cart.test.ts", fixtures.BASE["src/cart.test.ts"].replace("toEqual(100)", "toEqual(101)"))
        self.repo.commit("x")
        self.repo.write("src/cart.test.ts", fixtures.BASE["src/cart.test.ts"].replace("toEqual(100)", "toEqual(102)"))
        report = scan(self.repo.path)
        self.assertEqual(report.files_checked, 1)

    def test_snapshot_leaves_the_real_index_alone(self):
        self.repo.write("src/cart.test.ts", fixtures.GAMED["src/cart.test.ts"])
        self.repo.write("brand_new.py", "x = 1\n")
        before = self.repo.git("status", "--porcelain")
        tree = gitio.snapshot_tree(self.repo.path)
        self.assertTrue(tree)
        self.assertEqual(self.repo.git("status", "--porcelain"), before)


class SubdirTest(RepoTestCase):
    def test_only_the_subfolder(self):
        for pkg in ("a", "b"):
            fixtures.build(self.repo, {"packages/%s/%s" % (pkg, k): v for k, v in fixtures.BASE.items()}, "init " + pkg)
        self.repo.write("packages/a/src/cart.test.ts", fixtures.GAMED["src/cart.test.ts"])
        self.repo.write("packages/b/tsconfig.json", fixtures.GAMED["tsconfig.json"])
        root = gitio.repo_root(os.path.join(self.repo.path, "packages", "a"))
        report = scan(root, subdir="packages/a")
        paths = {f.path for f in report.findings}
        self.assertTrue(paths)
        self.assertTrue(all(p.startswith("packages/a/") for p in paths), paths)
        self.assertEqual(gitio.subdir_of(root, os.path.join(self.repo.path, "packages", "a")), "packages/a")
        self.assertIsNone(gitio.subdir_of(root, self.repo.path))


class SettingsAndSuppressionTest(RepoTestCase):
    def setUp(self):
        super().setUp()
        fixtures.build(self.repo)

    def test_rule_off_and_severity_override(self):
        self.repo.write(".redhanded.toml", '[rules]\ntest-skipped = "off"\nassertion-weakened = "low"\n')
        self.repo.write("src/cart.test.ts", fixtures.GAMED["src/cart.test.ts"])
        report = scan(self.repo.path)
        self.assertNotIn("test-skipped", rules_of(report))
        weakened = [f for f in report.findings if f.rule == "assertion-weakened"]
        self.assertTrue(weakened)
        self.assertEqual(weakened[0].severity, Severity.LOW)

    def test_ignore_globs(self):
        self.repo.write("pyproject.toml", '[tool.redhanded]\nignore = ["legacy/**"]\n')
        self.repo.write("legacy/old.test.ts", "it.skip('x', () => {});\n")
        report = scan(self.repo.path)
        self.assertEqual([f for f in report.findings if (f.path or "").startswith("legacy/")], [])

    def test_inline_ignore(self):
        text = fixtures.BASE["src/cart.test.ts"].replace(
            "  it('rejects unknown codes'",
            "  // redhanded-ignore[test-skipped] waiting on the pricing service\n  it.skip('rejects unknown codes'")
        self.repo.write("src/cart.test.ts", text)
        report = scan(self.repo.path)
        self.assertNotIn("test-skipped", rules_of(report))

    def test_file_level_ignore(self):
        self.repo.write("src/cart.test.ts", "// redhanded-ignore-file[test-skipped]\n" + fixtures.GAMED["src/cart.test.ts"])
        report = scan(self.repo.path)
        self.assertNotIn("test-skipped", rules_of(report))
        self.assertIn("assertion-weakened", rules_of(report))

    def test_bad_settings_warn_but_work(self):
        self.repo.write(".redhanded.toml", 'fail_on = "nope"\nmystery = 1\n[rules]\nnot-a-rule = "high"\n')
        s = load(self.repo.path)
        self.assertEqual(len(s.warnings), 3)
        self.assertEqual(s.fail_on, Severity.HIGH)


class SpeedTest(RepoTestCase):
    def test_a_few_hundred_files_is_quick(self):
        for i in range(300):
            self.repo.write("src/mod%03d.py" % i, "def f%d(x):\n    return x + %d\n" % (i, i))
            self.repo.write("tests/test_mod%03d.py" % i, "def test_f%d():\n    assert f%d(1) == %d\n" % (i, i, i + 1))
        self.repo.commit("many")
        for i in range(0, 300, 10):
            self.repo.write("src/mod%03d.py" % i, "def f%d(x):\n    return x + %d  # tweak\n" % (i, i))
        t0 = time.time()
        report = scan(self.repo.path)
        self.assertEqual(report.files_checked, 30)
        self.assertLess(time.time() - t0, 20)


if __name__ == "__main__":
    unittest.main()
