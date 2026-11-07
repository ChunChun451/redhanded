"""Claude Code hooks: SessionStart snapshot, PostToolUse warnings, Stop blocking."""

import json
import os
import shutil
import tempfile
import unittest
import uuid

from tests import fixtures
from tests.helpers import RepoTestCase, TranscriptBuilder, run_cli
from redhanded import hooks, state

JEST_PASS = "PASS src/cart.test.ts\nTests:       1 skipped, 2 passed, 3 total\n"


class HookTestCase(RepoTestCase):
    def setUp(self):
        super().setUp()
        fixtures.build(self.repo)
        self.sid = str(uuid.uuid4())
        self.tdir = tempfile.mkdtemp(prefix="redhanded-transcripts-")
        self.tb = TranscriptBuilder(self.repo.path, session_id=self.sid)

    def tearDown(self):
        shutil.rmtree(self.tdir, ignore_errors=True)
        super().tearDown()

    def start(self):
        return hooks.on_session_start({"session_id": self.sid, "cwd": self.repo.path, "hook_event_name": "SessionStart",
                                       "source": "startup"})

    def agent_edit(self, rel, new_text):
        old_text = fixtures.BASE.get(rel)
        if old_text is None:
            self.tb.write(rel, new_text)
        else:
            self.tb.edit(rel, old_text, old_text, new_text)
        self.repo.write(rel, new_text)

    def stop(self, final):
        self.tb.say(final)
        tp = self.tb.save(self.tdir)
        return hooks.on_stop({"session_id": self.sid, "transcript_path": tp, "cwd": self.repo.path,
                              "hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": final})


class SessionStartTest(HookTestCase):
    def test_snapshot_and_announcement(self):
        out = self.start()
        self.assertIn("redhanded is active", out["hookSpecificOutput"]["additionalContext"])
        st = state.load(self.sid)
        self.assertTrue(state.get_baseline(st, self.repo.path))

    def test_announcement_can_be_turned_off(self):
        self.repo.write(".redhanded.toml", "announce = false\n")
        self.assertIsNone(self.start())
        self.assertTrue(state.get_baseline(state.load(self.sid), self.repo.path))

    def test_outside_git_does_nothing(self):
        d = tempfile.mkdtemp()
        try:
            self.assertIsNone(hooks.on_session_start({"session_id": self.sid, "cwd": d}))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class StopTest(HookTestCase):
    def test_blocks_gamed_work_once(self):
        self.start()
        self.tb.prompt("The SAVE10 test is failing, please fix it.")
        self.agent_edit("src/cart.test.ts", fixtures.GAMED["src/cart.test.ts"])
        self.agent_edit("src/cart.ts", fixtures.GAMED["src/cart.ts"])
        self.tb.bash("npm test", JEST_PASS)
        out = self.stop("Fixed - all tests pass now.")
        self.assertEqual(out.get("decision"), "block")
        for rule in ("test-skipped", "special-cased-test-value", "test-environment-branch"):
            self.assertIn(rule, out["reason"])
        self.assertIn("redhanded:", out["systemMessage"])
        # Claude was sent back; the same findings must not block again.
        again = self.stop("I kept the changes.")
        self.assertTrue(again is None or "decision" not in again)

    def test_shell_edits_are_caught_through_git(self):
        self.start()
        self.tb.prompt("make the build green")
        self.tb.bash("sed -i 's/strict\": true/strict\": false/' tsconfig.json")
        self.repo.write("tsconfig.json", fixtures.GAMED["tsconfig.json"])
        out = self.stop("Build is green.")
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("type-or-lint-config-weakened", out["reason"])

    def test_honest_session_passes(self):
        self.start()
        self.tb.prompt("Implement discount codes so the SAVE10 test passes.")
        for rel, text in fixtures.HONEST.items():
            self.agent_edit(rel, text)
        self.tb.bash("npx jest", "Tests:       4 passed, 4 total\n")
        self.assertIsNone(self.stop("Implemented discount codes; `npx jest` passes (4 tests)."))

    def test_user_requested_skip_is_not_blocked(self):
        self.start()
        self.tb.prompt("Skip the 'rejects unknown codes' test for now, it is flaky. Don't run anything.")
        text = fixtures.BASE["src/cart.test.ts"].replace("it('rejects unknown codes'", "it.skip('rejects unknown codes'")
        self.agent_edit("src/cart.test.ts", text)
        out = self.stop("Skipped that test as you asked. I did not run the tests.")
        self.assertTrue(out is None or "decision" not in out, out)

    def test_other_peoples_edits_do_not_block(self):
        self.start()
        self.tb.prompt("Fix the typo in the README.")
        self.agent_edit("README.md", "# Shop\n")
        # Meanwhile the user (or another session) skips a test in their editor.
        self.repo.write("src/cart.test.ts", fixtures.GAMED["src/cart.test.ts"])
        out = self.stop("Fixed the typo in the README.")
        self.assertIsNotNone(out)
        self.assertNotIn("decision", out)
        self.assertIn("did not touch", out["systemMessage"])

    def test_max_blocks_zero_only_reports(self):
        self.repo.write(".redhanded.toml", "max_blocks = 0\n")
        self.repo.commit("settings")
        self.start()
        self.tb.prompt("fix the tests")
        self.agent_edit("src/cart.test.ts", fixtures.GAMED["src/cart.test.ts"])
        out = self.stop("Done.")
        self.assertNotIn("decision", out)
        self.assertIn("high", out["systemMessage"])

    def test_without_session_start_uses_the_transcript(self):
        self.tb.prompt("fix the tests")
        self.agent_edit("src/cart.test.ts", fixtures.GAMED["src/cart.test.ts"])
        out = self.stop("All good.")
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("test-skipped", out["reason"])


class PostEditTest(HookTestCase):
    def payload(self, rel, old, new):
        path = os.path.join(self.repo.path, rel)
        original = fixtures.BASE[rel]
        return {"session_id": self.sid, "cwd": self.repo.path, "hook_event_name": "PostToolUse", "tool_name": "Edit",
                "tool_input": {"file_path": path, "old_string": old, "new_string": new},
                "tool_response": {"filePath": path, "oldString": old, "newString": new, "originalFile": original,
                                  "structuredPatch": [], "replaceAll": False}}

    def test_flags_high_findings_once(self):
        old = "function applyDiscount(sum: number, code?: string): number {\n"
        new = old + "  if (process.env.JEST_WORKER_ID) return 90;\n"
        out = hooks.on_post_edit(self.payload("src/cart.ts", old, new))
        self.assertIn("test-environment-branch", out["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(hooks.on_post_edit(self.payload("src/cart.ts", old, new)))

    def test_quiet_for_ordinary_edits(self):
        old = "  return sum;\n"
        new = "  return Math.max(0, sum);\n"
        self.assertIsNone(hooks.on_post_edit(self.payload("src/cart.ts", old, new)))


class HookPlumbingTest(HookTestCase):
    def test_cli_entry_point(self):
        payload = json.dumps({"session_id": self.sid, "cwd": self.repo.path, "hook_event_name": "SessionStart"})
        proc = run_cli("hook", "session-start", stdin=payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("additionalContext", json.loads(proc.stdout)["hookSpecificOutput"])

    def test_garbage_input_fails_open(self):
        outside = tempfile.mkdtemp()  # with no "cwd" in the input, hooks fall back to the current directory
        try:
            for event, stdin in (("stop", "not json"), ("post-edit", "[]"), ("session-start", ""), ("nonsense", "{}")):
                proc = run_cli("hook", event, stdin=stdin, cwd=outside)
                self.assertEqual(proc.returncode, 0, (event, proc.stderr))
                self.assertEqual(proc.stdout.strip(), "")
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_missing_transcript_fails_open(self):
        proc = run_cli("hook", "stop", stdin=json.dumps({"session_id": self.sid, "transcript_path": "nope.jsonl",
                                                         "cwd": self.repo.path}))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
