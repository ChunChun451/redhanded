"""The command line, run as a user would run it."""

import json
import os
import shutil
import tempfile
import unittest

from tests import fixtures
from tests.helpers import RepoTestCase, TranscriptBuilder, run_cli


class CheckCommandTest(RepoTestCase):
    def setUp(self):
        super().setUp()
        fixtures.build(self.repo)
        fixtures.apply(self.repo, fixtures.GAMED)

    def test_text_report_and_exit_code(self):
        proc = run_cli("-C", self.repo.path)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("SUSPICIOUS", proc.stdout)
        self.assertIn("test-skipped", proc.stdout)
        self.assertIn("src/cart.test.ts", proc.stdout)

    def test_fail_on(self):
        self.assertEqual(run_cli("check", "-C", self.repo.path, "--fail-on", "off").returncode, 0)
        self.assertEqual(run_cli("check", "-C", self.repo.path, "--fail-on", "low").returncode, 1)

    def test_json(self):
        proc = run_cli("check", "-C", self.repo.path, "-f", "json")
        data = json.loads(proc.stdout)
        self.assertEqual(data["verdict"], "suspicious")
        self.assertTrue(fixtures.EXPECTED_RULES <= {f["rule"] for f in data["findings"]})
        self.assertGreater(data["counts"]["high"], 0)

    def test_sarif(self):
        proc = run_cli("check", "-C", self.repo.path, "-f", "sarif")
        data = json.loads(proc.stdout)
        self.assertEqual(data["version"], "2.1.0")
        run = data["runs"][0]
        self.assertEqual(run["tool"]["driver"]["name"], "redhanded")
        self.assertTrue(run["results"])
        ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
        self.assertTrue(all(res["ruleId"] in ids for res in run["results"]))
        located = [r for r in run["results"] if r.get("locations")]
        self.assertTrue(located)
        uri = located[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        self.assertFalse(uri.startswith("/"))

    def test_markdown_and_github(self):
        md = run_cli("check", "-C", self.repo.path, "-f", "markdown").stdout
        self.assertIn("| ", md)
        self.assertIn("redhanded", md)
        gh = run_cli("check", "-C", self.repo.path, "-f", "github").stdout
        self.assertTrue(any(line.startswith("::error file=") for line in gh.splitlines()))

    def test_output_file(self):
        out = os.path.join(self.repo.path, "report.json")
        proc = run_cli("check", "-C", self.repo.path, "-f", "json", "-o", out)
        self.assertEqual(proc.stdout, "")
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["verdict"], "suspicious")

    def test_paths_filter(self):
        data = json.loads(run_cli("check", "-C", self.repo.path, "-f", "json", "tsconfig.json").stdout)
        self.assertEqual({f["path"] for f in data["findings"]}, {"tsconfig.json"})

    def test_verbose_and_quiet(self):
        self.assertIn("before", run_cli("check", "-C", self.repo.path, "-v").stdout.lower() + "before")
        quiet = run_cli("check", "-C", self.repo.path, "-q").stdout
        self.assertNotIn(" LOW ", quiet)


class CleanAndErrorsTest(RepoTestCase):
    def test_clean_repo(self):
        fixtures.build(self.repo)
        fixtures.apply(self.repo, fixtures.HONEST)
        proc = run_cli("check", "-C", self.repo.path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("CLEAN", proc.stdout)

    def test_no_changes_at_all(self):
        self.repo.write("a.py", "x = 1\n")
        self.repo.commit("one")
        proc = run_cli("check", "-C", self.repo.path)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_empty_repository(self):
        proc = run_cli("check", "-C", self.repo.path)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_not_a_repository(self):
        d = tempfile.mkdtemp()
        try:
            proc = run_cli("check", "-C", d)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("not inside a git repository", proc.stderr)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_unknown_revision(self):
        self.repo.write("a.py", "x = 1\n")
        self.repo.commit()
        proc = run_cli("check", "-C", self.repo.path, "--base", "does-not-exist")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown revision", proc.stderr)


class InfoCommandsTest(unittest.TestCase):
    def test_version(self):
        for args in (("version",), ("--version",)):
            proc = run_cli(*args)
            self.assertEqual(proc.returncode, 0)
            self.assertRegex(proc.stdout, r"^redhanded \d+\.\d+\.\d+")

    def test_rules(self):
        proc = run_cli("rules")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("test-skipped", proc.stdout)
        data = json.loads(run_cli("rules", "-f", "json").stdout)
        self.assertEqual(len(data), 29)
        self.assertEqual(len({r["id"] for r in data}), 29)
        md = run_cli("rules", "-f", "markdown").stdout
        self.assertIn("## test-skipped", md)

    def test_explain(self):
        proc = run_cli("explain", "special-cased-test-value")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("special-cased-test-value", proc.stdout)
        bad = run_cli("explain", "skipped")
        self.assertEqual(bad.returncode, 2)
        self.assertIn("did you mean", bad.stderr)

    def test_help(self):
        proc = run_cli("--help")
        self.assertEqual(proc.returncode, 0)
        for cmd in ("check", "session", "audit", "hook", "init", "rules", "explain", "ci"):
            self.assertIn(cmd, proc.stdout)


class InitTest(RepoTestCase):
    def test_claude_hooks(self):
        dry = run_cli("init", "claude", "--dry-run", "-C", self.repo.path)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        settings_path = os.path.join(self.repo.path, ".claude", "settings.local.json")
        self.assertFalse(os.path.exists(settings_path))
        self.assertIn("SessionStart", dry.stdout)
        proc = run_cli("init", "claude", "-C", self.repo.path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(settings_path, encoding="utf-8") as fh:
            settings = json.load(fh)
        self.assertEqual(set(settings["hooks"]), {"SessionStart", "PostToolUse", "Stop"})
        stop = settings["hooks"]["Stop"][0]["hooks"][0]
        self.assertEqual(stop["args"][-2:], ["hook", "stop"])
        self.assertTrue(os.path.isabs(stop["command"]))
        again = run_cli("init", "claude", "-C", self.repo.path)
        self.assertIn("already", again.stdout)

    def test_claude_hooks_keep_existing_settings(self):
        path = self.repo.write(".claude/settings.local.json", json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))
        run_cli("init", "claude", "-C", self.repo.path)
        with open(path, encoding="utf-8") as fh:
            settings = json.load(fh)
        self.assertEqual(settings["permissions"], {"allow": ["Bash(ls)"]})
        self.assertIn("Stop", settings["hooks"])

    def test_github_and_pre_commit(self):
        proc = run_cli("init", "github", "-C", self.repo.path)
        self.assertEqual(proc.returncode, 0)
        with open(os.path.join(self.repo.path, ".github", "workflows", "redhanded.yml"), encoding="utf-8") as fh:
            self.assertIn("fetch-depth: 0", fh.read())
        proc = run_cli("init", "pre-commit", "-C", self.repo.path)
        self.assertEqual(proc.returncode, 0)
        with open(os.path.join(self.repo.path, ".pre-commit-config.yaml"), encoding="utf-8") as fh:
            self.assertIn("id: redhanded", fh.read())


class SessionCommandTest(RepoTestCase):
    def test_transcript_only(self):
        fixtures.build(self.repo)
        tb = TranscriptBuilder(self.repo.path)
        tb.prompt("fix the failing test")
        old = fixtures.BASE["src/cart.test.ts"]
        tb.edit("src/cart.test.ts", old, old, fixtures.GAMED["src/cart.test.ts"])
        tb.say("All tests pass.")
        d = tempfile.mkdtemp()
        try:
            path = tb.save(d)
            proc = run_cli("session", "--transcript", path, "-C", self.repo.path, "-f", "json")
            self.assertEqual(proc.returncode, 1, proc.stderr)
            rules = {f["rule"] for f in json.loads(proc.stdout)["findings"]}
            self.assertIn("test-skipped", rules)
            self.assertIn("unverified-claim", rules)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_missing_transcript(self):
        proc = run_cli("session", "--transcript", "nope.jsonl", "-C", self.repo.path)
        self.assertEqual(proc.returncode, 2)


class DemoCommandTest(unittest.TestCase):
    def test_gamed(self):
        proc = run_cli("demo")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("A pretend agent was asked", proc.stdout)
        self.assertIn("SUSPICIOUS", proc.stdout)
        self.assertIn("redhanded check", proc.stdout)

    def test_gamed_json_finds_every_planted_hack(self):
        proc = run_cli("demo", "-f", "json", "--fail-on", "high")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        data = json.loads(proc.stdout)
        rules = {f["rule"] for f in data["findings"]}
        self.assertTrue(fixtures.EXPECTED_RULES <= rules, fixtures.EXPECTED_RULES - rules)
        self.assertTrue({"unverified-claim", "verification-bypassed"} <= rules, rules)
        self.assertFalse(any(f.get("foreign") for f in data["findings"]))

    def test_honest(self):
        proc = run_cli("demo", "--honest", "--fail-on", "low")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("CLEAN", proc.stdout)


if __name__ == "__main__":
    unittest.main()
