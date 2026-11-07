import unittest

from tests.helpers import check_texts, rules_of
from redhanded.model import Severity


def changed(path, old, new):
    return check_texts({path: (old, new)})


class SkipTest(unittest.TestCase):
    CASES = [
        ("a.test.ts", "it('works', () => {", "it.skip('works', () => {"),
        ("a.test.ts", "test('works', () => {", "xtest('works', () => {"),
        ("a.spec.js", "describe('suite', () => {", "describe.skip('suite', () => {"),
        ("tests/test_a.py", "def test_works():", "@pytest.mark.skip(reason='flaky')\ndef test_works():"),
        ("tests/test_a.py", "def test_works():\n    x = 1", "def test_works():\n    pytest.skip('later')\n    x = 1"),
        ("tests/test_a.py", "class T(unittest.TestCase):", "@unittest.skip('broken')\nclass T(unittest.TestCase):"),
        ("calc_test.go", "func TestAdd(t *testing.T) {", "func TestAdd(t *testing.T) {\n\tt.Skip(\"todo\")"),
        ("tests/calc.rs", "#[test]\nfn adds() {", "#[test]\n#[ignore]\nfn adds() {"),
        ("src/test/java/CalcTest.java", "@Test\nvoid adds() {", "@Disabled\n@Test\nvoid adds() {"),
        ("CalcTests.cs", "[Fact]\npublic void Adds() {", "[Fact(Skip = \"flaky\")]\npublic void Adds() {"),
        ("spec/calc_spec.rb", "it 'adds' do", "xit 'adds' do"),
        ("tests/CalcTest.php", "public function testAdds() {", "public function testAdds() {\n$this->markTestSkipped('x');"),
        ("CalcTests.swift", "func testAdds() throws {", "func testAdds() throws {\n throw XCTSkip(\"x\")"),
        ("test/calc_test.exs", "test \"adds\" do", "@tag :skip\ntest \"adds\" do"),
        ("calc_test.cc", "TEST(Calc, Adds) {", "TEST(Calc, DISABLED_Adds) {"),
    ]

    def test_skips_detected(self):
        for path, old, new in self.CASES:
            report = changed(path, old + "\n", new + "\n")
            self.assertIn("test-skipped", rules_of(report), "%s: %s" % (path, new))

    def test_conditional_skip_is_medium(self):
        report = changed("tests/test_a.py", "def test_x():\n", "@pytest.mark.skipif(sys.platform == 'win32', reason='x')\ndef test_x():\n")
        f = [f for f in report.findings if f.rule == "test-skipped"][0]
        self.assertEqual(f.severity, Severity.MEDIUM)

    def test_guarded_runtime_skip_is_medium(self):
        cases = [
            ("tests/test_a.py", "def test_x():\n    x = 1\n",
             "def test_x():\n    if not DATA.exists():\n        pytest.skip('run make gen first')\n    x = 1\n"),
            ("tests/test_a.py", "def test_x():\n    x = 1\n",
             "def test_x():\n    if not DATA.exists(): pytest.skip('run make gen first')\n    x = 1\n"),
            ("tests/test_a.py", "def test_x():\n    x = 1\n",
             "def test_x():\n    try:\n        import numpy\n    except ImportError:\n        pytest.skip('needs numpy')\n"
             "    x = 1\n"),
            ("calc_test.go", "func TestAdd(t *testing.T) {\n",
             "func TestAdd(t *testing.T) {\n\tif testing.Short() {\n\t\tt.Skip(\"slow\")\n\t}\n"),
            ("a.test.js", "it('a', function () {\n", "it('a', function () {\n  if (!process.env.DB_URL) this.skip();\n"),
        ]
        for path, old, new in cases:
            found = [f for f in changed(path, old, new).findings if f.rule == "test-skipped"]
            self.assertTrue(found, new)
            self.assertEqual(found[0].severity, Severity.MEDIUM, new)

    def test_unguarded_runtime_skip_stays_high(self):
        report = changed("tests/test_a.py", "def test_x():\n    x = 1\n", "def test_x():\n    pytest.skip('later')\n    x = 1\n")
        found = [f for f in report.findings if f.rule == "test-skipped"]
        self.assertEqual(found[0].severity, Severity.HIGH)

    def test_focus(self):
        report = changed("a.test.ts", "it('a', () => {\n", "it.only('a', () => {\n")
        self.assertIn("test-focused", rules_of(report))
        report = changed("a.test.ts", "describe('a', () => {\n", "fdescribe('a', () => {\n")
        self.assertIn("test-focused", rules_of(report))

    def test_skip_in_production_code_ignored(self):
        report = changed("src/parser.ts", "const a = 1;\n", "const skip = items.skip(2);\n")
        self.assertNotIn("test-skipped", rules_of(report))


PY_TESTS = '''def test_add():
    assert add(1, 2) == 3


def test_sub():
    assert sub(3, 1) == 2


def test_neg():
    assert add(-1, -1) == -2
'''


class DeletionTest(unittest.TestCase):
    def test_removed_test_function(self):
        new = PY_TESTS.split("\n\n\ndef test_neg")[0] + "\n"
        report = changed("tests/test_calc.py", PY_TESTS, new)
        self.assertIn("test-deleted", rules_of(report))
        self.assertIn("test_neg", [f for f in report.findings if f.rule == "test-deleted"][0].message)

    def test_deleted_test_file(self):
        report = check_texts({"tests/test_calc.py": (PY_TESTS, None)})
        self.assertIn("test-deleted", rules_of(report))

    def test_moved_test_is_not_deleted(self):
        keep = PY_TESTS.split("\n\n\ndef test_neg")[0] + "\n"
        moved = "def test_neg():\n    assert add(-1, -1) == -2\n"
        report = check_texts({"tests/test_calc.py": (PY_TESTS, keep), "tests/test_negative.py": (None, moved)})
        self.assertNotIn("test-deleted", rules_of(report))

    def test_assertions_removed(self):
        old = "def test_add():\n    r = add(1, 2)\n    assert r == 3\n    assert isinstance(r, int)\n"
        new = "def test_add():\n    r = add(1, 2)\n"
        report = changed("tests/test_calc.py", old, new)
        self.assertIn("assertions-removed", rules_of(report))

    def test_commented_out_assertion(self):
        old = "it('adds', () => {\n  expect(add(1, 2)).toBe(3);\n});\n"
        new = "it('adds', () => {\n  // expect(add(1, 2)).toBe(3);\n});\n"
        report = changed("calc.test.js", old, new)
        self.assertIn("assertion-commented-out", rules_of(report))

    def test_tautology(self):
        report = changed("calc.test.js", "it('a', () => {\n});\n", "it('a', () => {\n  expect(true).toBe(true);\n});\n")
        self.assertIn("tautological-assertion", rules_of(report))
        report = changed("tests/test_a.py", "def test_a():\n    pass\n", "def test_a():\n    assert True\n")
        self.assertIn("tautological-assertion", rules_of(report))


class WeakeningTest(unittest.TestCase):
    def test_strict_to_loose_js(self):
        report = changed("cart.test.ts", "  expect(total(items)).toEqual(100);\n", "  expect(total(items)).toBeDefined();\n")
        self.assertIn("assertion-weakened", rules_of(report))

    def test_strict_to_loose_py(self):
        report = changed("tests/test_api.py", "    self.assertEqual(resp.json(), {'id': 1})\n",
                         "    self.assertIsNotNone(resp.json())\n")
        self.assertIn("assertion-weakened", rules_of(report))

    def test_tolerance_widened(self):
        report = changed("tests/test_math.py", "    assert x == pytest.approx(3.14, rel=1e-6)\n",
                         "    assert x == pytest.approx(3.14, rel=1e-1)\n")
        self.assertIn("assertion-weakened", rules_of(report))

    def test_expected_value_changed(self):
        report = changed("calc.test.ts", "  expect(add(2, 2)).toBe(4);\n", "  expect(add(2, 2)).toBe(5);\n")
        f = [f for f in report.findings if f.rule == "expected-value-changed"]
        self.assertTrue(f)
        self.assertEqual(f[0].severity, Severity.LOW)

    def test_swallowed_python(self):
        old = "def test_a():\n    assert f() == 1\n"
        new = "def test_a():\n    try:\n        assert f() == 1\n    except AssertionError:\n        pass\n"
        self.assertIn("assertion-swallowed", rules_of(changed("tests/test_a.py", old, new)))

    def test_swallowed_js(self):
        old = "it('a', () => {\n  expect(f()).toBe(1);\n});\n"
        new = "it('a', () => {\n  try {\n    expect(f()).toBe(1);\n  } catch (e) {}\n});\n"
        self.assertIn("assertion-swallowed", rules_of(changed("a.test.js", old, new)))

    def test_mocked_subject(self):
        new = "import { total } from './cart';\njest.mock('./cart');\n"
        self.assertIn("mocked-unit-under-test", rules_of(changed("src/cart.test.ts", "import { total } from './cart';\n", new)))

    def test_snapshots(self):
        report = check_texts({"src/__snapshots__/Button.test.tsx.snap": ("exports[`a`] = `<b>`;\n", "exports[`a`] = `<i>`;\n")})
        self.assertIn("snapshot-rewritten", rules_of(report))


if __name__ == "__main__":
    unittest.main()
