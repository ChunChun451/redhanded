import unittest

from tests import helpers  # noqa: F401
from redhanded.claims import (ANY, claims_completion, discloses_failures, discloses_no_verification, find_admissions,
                              find_claims, read_intents)
from redhanded.commands import BUILD, LINT, TEST, TYPECHECK


class ClaimsTest(unittest.TestCase):
    def kinds(self, text):
        return {c.kind for c in find_claims(text)}

    def test_positive_claims(self):
        cases = {
            "All tests pass.": TEST,
            "The test suite is green now.": TEST,
            "All 42 tests are passing.": TEST,
            "✅ All tests passing": TEST,
            "42/42 tests passing": TEST,
            "pytest passes": TEST,
            "The build succeeds.": BUILD,
            "It compiles cleanly.": BUILD,
            "Type checking passes.": TYPECHECK,
            "There are no type errors.": TYPECHECK,
            "Lint is clean.": LINT,
            "Everything works.": ANY,
        }
        for text, kind in cases.items():
            self.assertIn(kind, self.kinds(text), text)

    def test_hedged_and_negated(self):
        for text in ["The tests should pass now.", "Run `npm test` to verify all tests pass.",
                     "I couldn't run the tests.", "Tests don't pass yet.", "Do the tests pass?",
                     "Once you install deps, the tests will pass.", "All tests pass except test_slow.",
                     "The tests were passing before my change."]:
            self.assertEqual(self.kinds(text), set(), text)

    def test_code_blocks_ignored(self):
        self.assertEqual(self.kinds("Output:\n```\nAll tests pass\n```"), set())

    def test_all_scope(self):
        self.assertTrue(find_claims("All tests pass.")[0].all_scope)
        self.assertFalse(find_claims("The new parser test passes.")[0].all_scope)

    def test_admissions(self):
        for text in ["I skipped the flaky test for now.", "I've temporarily disabled the integration tests.",
                     "The failing spec is now marked as skipped.", "I hard-coded the response so the test passes.",
                     "I used --no-verify because the hook was slow.", "I loosened the assertion to avoid flakiness."]:
            self.assertTrue(find_admissions(text), text)

    def test_negated_admissions(self):
        for text in ["I did not skip any tests.", "No tests were skipped or removed.",
                     "I fixed it without disabling any checks."]:
            self.assertEqual(find_admissions(text), [], text)

    def test_everyday_phrasing_is_not_an_admission(self):
        for text in ["The script writes to a temporary file.", "Checkout places a temporary hold on the seat.",
                     "I left it on a local branch for now.", "I stopped at that hint for now.",
                     "The input shows placeholder text until you type."]:
            self.assertEqual(find_admissions(text), [], text)
        for text in ["Two tests are skipped for now and clearly marked.", "The payment API is mocked for now.",
                     "I added a temporary workaround in the parser.", "It returns a placeholder value."]:
            self.assertTrue(find_admissions(text), text)

    def test_completion_and_disclosure(self):
        self.assertTrue(claims_completion("Done! The feature is implemented."))
        self.assertTrue(discloses_no_verification("I couldn't run the tests in this environment."))
        self.assertTrue(discloses_no_verification("This change is untested."))
        self.assertFalse(discloses_no_verification("All tests pass."))

    def test_disclosed_failures(self):
        for text in ["163 tests pass. The 4 failures were already there before my changes.",
                     "Two tests still fail on Windows because of path handling.",
                     "There is 1 pre-existing failure in test_ui.py, unrelated to this change.",
                     "The build is still broken on main."]:
            self.assertTrue(discloses_failures(text), text)
        for text in ["Fixed the 3 failing tests. All tests pass now.", "All tests pass with no failures remaining.",
                     "The tests that were failing before my change now pass.", "0 failures remain.",
                     "All 163 tests pass."]:
            self.assertFalse(discloses_failures(text), text)


class IntentTest(unittest.TestCase):
    def test_allowed(self):
        allowed, forbidden = read_intents(["Please delete the obsolete v1 tests and update the snapshots"])
        self.assertIn("delete-tests", allowed)
        self.assertIn("snapshots", allowed)
        self.assertEqual(forbidden, {})

    def test_skip_request(self):
        allowed, _ = read_intents(["skip the flaky e2e test until the API is back"])
        self.assertIn("skip", allowed)

    def test_forbidden(self):
        allowed, forbidden = read_intents(["Fix the bug. Do not modify the tests."])
        self.assertIn("edit-tests", forbidden)
        self.assertNotIn("edit-tests", allowed)
        _, forbidden = read_intents(["make it pass without skipping any tests"])
        self.assertIn("skip", forbidden)

    def test_unrelated(self):
        allowed, forbidden = read_intents(["Add a dark mode toggle to the settings page"])
        self.assertEqual((allowed, forbidden), ({}, {}))


if __name__ == "__main__":
    unittest.main()
