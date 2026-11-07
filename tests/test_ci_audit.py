"""GitHub CI mode and the transcript audit."""

import json
import os
import shutil
import tempfile
import unittest

from tests import fixtures
from tests.helpers import RepoTestCase, TranscriptBuilder, run_cli
from redhanded.ci import parse_allow
from redhanded.transcript import project_key


class AllowLineTest(unittest.TestCase):
    def test_rules_and_reason(self):
        allowed = parse_allow("Removes the v1 API.\n\nredhanded-allow: test-deleted, snapshot-rewritten - v1 is gone\n")
        self.assertEqual(set(allowed), {"test-deleted", "snapshot-rewritten"})
        self.assertIn("v1 is gone", allowed["test-deleted"])

    def test_wildcard_and_unknown(self):
        self.assertEqual(set(parse_allow("- redhanded-allow: * because big migration")), {"*"})
        self.assertEqual(parse_allow("redhanded-allow: not-a-rule"), {})
        self.assertEqual(parse_allow(""), {})


class CiModeTest(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.base = fixtures.build(self.repo)
        self.repo.git("checkout", "-q", "-b", "feature")
        self.repo.write("src/cart.test.ts", fixtures.GAMED["src/cart.test.ts"])
        self.head = self.repo.commit("feature")
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def run_ci(self, body="", extra=()):
        event = {"pull_request": {"number": 7, "title": "Fix cart", "body": body,
                                  "base": {"sha": self.base}, "head": {"sha": self.head}}}
        paths = {name: os.path.join(self.tmp, name) for name in ("event.json", "output", "summary.md", "out.sarif")}
        with open(paths["event.json"], "w", encoding="utf-8") as fh:
            json.dump(event, fh)
        env = {"GITHUB_EVENT_PATH": paths["event.json"], "GITHUB_OUTPUT": paths["output"],
               "GITHUB_STEP_SUMMARY": paths["summary.md"], "GITHUB_ACTIONS": "true"}
        proc = run_cli("ci", "-C", self.repo.path, "--sarif", paths["out.sarif"], *extra, env=env)
        with open(paths["output"], encoding="utf-8") as fh:
            outputs = dict(line.split("=", 1) for line in fh.read().splitlines())
        with open(paths["summary.md"], encoding="utf-8") as fh:
            summary = fh.read()
        with open(paths["out.sarif"], encoding="utf-8") as fh:
            sarif = json.load(fh)
        return proc, outputs, summary, sarif

    def test_pull_request(self):
        proc, outputs, summary, sarif = self.run_ci()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(outputs["verdict"], "suspicious")
        self.assertGreater(int(outputs["high"]), 0)
        self.assertIn("test-skipped", summary)
        self.assertIn("::error file=src/cart.test.ts", proc.stdout)
        self.assertTrue(sarif["runs"][0]["results"])
        self.assertIn("pull request #7", proc.stdout)

    def test_allow_line_lowers(self):
        proc, outputs, _summary, _sarif = self.run_ci(
            "redhanded-allow: test-skipped, assertion-weakened - the BOGUS code path is being removed")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(outputs["high"], "0")

    def test_fail_on_off(self):
        proc, _outputs, _summary, _sarif = self.run_ci(extra=("--fail-on", "off"))
        self.assertEqual(proc.returncode, 0)


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="redhanded-claude-home-")
        self.project = os.path.realpath(tempfile.mkdtemp(prefix="redhanded-project-"))
        folder = os.path.join(self.home, "projects", project_key(self.project))
        os.makedirs(folder)
        gamed = TranscriptBuilder(self.project, session_id="aaaaaaaa-0000-0000-0000-000000000001")
        gamed.prompt("The SAVE10 test fails, fix it")
        old = fixtures.BASE["src/cart.test.ts"]
        gamed.edit("src/cart.test.ts", old, old, fixtures.GAMED["src/cart.test.ts"])
        gamed.say("Fixed. All tests pass.")
        gamed.save(folder)
        honest = TranscriptBuilder(self.project, session_id="aaaaaaaa-0000-0000-0000-000000000002")
        honest.prompt("rename total to cartTotal")
        honest.edit("src/cart.ts", fixtures.BASE["src/cart.ts"], "export function total",
                    "export function cartTotal")
        honest.bash("npx jest", "Tests:       3 passed, 3 total\n")
        honest.say("Renamed it; `npx jest` passes.")
        honest.save(folder)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.project, ignore_errors=True)

    def audit(self, *args):
        return run_cli("audit", "--all", *args, env={"CLAUDE_CONFIG_DIR": self.home})

    def test_json(self):
        proc = self.audit("-f", "json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["summary"]["sessions"], 2)
        self.assertEqual(data["summary"]["flagged_high"], 1)
        worst = data["sessions"][0]
        self.assertEqual(worst["id"], "aaaaaaaa-0000-0000-0000-000000000001")
        self.assertIn("test-skipped", {f["rule"] for f in worst["findings"]})
        self.assertIn("unverified-claim", {f["rule"] for f in worst["findings"]})

    def test_redacted_json_has_no_code_or_prompts(self):
        data = json.loads(self.audit("-f", "json", "--redact").stdout)
        blob = json.dumps(data)
        self.assertNotIn("SAVE10", blob)
        self.assertNotIn("it.skip", blob)
        self.assertNotIn(self.project.replace("\\", "\\\\"), blob)

    def test_text_html_markdown(self):
        text = self.audit().stdout
        self.assertIn("redhanded audit", text)
        self.assertIn("test-skipped", text)
        out = os.path.join(self.home, "audit.html")
        proc = self.audit("-f", "html", "-o", out)
        self.assertEqual(proc.returncode, 0)
        with open(out, encoding="utf-8") as fh:
            page = fh.read()
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn("prefers-color-scheme:dark", page)
        self.assertIn("| Flagged high | 1 |", self.audit("-f", "markdown").stdout)

    def test_no_sessions(self):
        empty = tempfile.mkdtemp()
        try:
            proc = run_cli("audit", "--all", env={"CLAUDE_CONFIG_DIR": empty})
            self.assertEqual(proc.returncode, 0)
            self.assertIn("No Claude Code sessions", proc.stdout)
        finally:
            shutil.rmtree(empty, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
