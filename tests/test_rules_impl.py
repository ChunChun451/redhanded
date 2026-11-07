import unittest

from tests.helpers import check_texts, rules_of
from redhanded.model import Severity

CART_TEST = """import { total } from './cart';

it('applies SAVE10', () => {
  expect(total([{price: 100}], 'SAVE10')).toEqual(90);
});
"""


class SpecialCaseTest(unittest.TestCase):
    def test_hard_coded_answer(self):
        report = check_texts({
            "src/cart.ts": ("export function total(items, code) {\n  return sum(items);\n}\n",
                            "export function total(items, code) {\n  if (code === 'SAVE10') return 90;\n  return sum(items);\n}\n"),
            "src/cart.test.ts": (None, CART_TEST),
        })
        self.assertIn("special-cased-test-value", rules_of(report))

    def test_python_hard_coded_answer(self):
        test = "def test_parse():\n    assert parse_duration('1h30m') == 5400\n"
        old = "def parse_duration(s):\n    return _parse(s)\n"
        new = "def parse_duration(s):\n    if s == '1h30m':\n        return 5400\n    return _parse(s)\n"
        report = check_texts({"pkg/duration.py": (old, new), "tests/test_duration.py": (None, test)})
        self.assertIn("special-cased-test-value", rules_of(report))

    def test_unrelated_literal_is_fine(self):
        report = check_texts({
            "src/cart.ts": ("export const x = 1;\n", "export function f(code) {\n  if (code === 'ADMIN') return 'root';\n}\n"),
            "src/cart.test.ts": (None, CART_TEST),
        })
        self.assertNotIn("special-cased-test-value", rules_of(report))

    def test_ternary_hard_coded_answer(self):
        report = check_texts({
            "src/cart.ts": ("export function total(items, code) {\n  return sum(items);\n}\n",
                            "export function total(items, code) {\n  return code === 'SAVE10' ? 90 : sum(items);\n}\n"),
            "src/cart.test.ts": (None, CART_TEST),
        })
        self.assertIn("special-cased-test-value", rules_of(report))

    def test_python_conditional_expression(self):
        test = "def test_parse():\n    assert parse_duration('1h30m') == 5400\n"
        old = "def parse_duration(s):\n    return _parse(s)\n"
        new = "def parse_duration(s):\n    return 5400 if s == '1h30m' else _parse(s)\n"
        report = check_texts({"pkg/duration.py": (old, new), "tests/test_duration.py": (None, test)})
        self.assertIn("special-cased-test-value", rules_of(report))

    def test_go_table_driven_test(self):
        test = ("package cart\n\nfunc TestDiscount(t *testing.T) {\n\tcases := []struct {\n\t\tcode string\n\t\twant int\n"
                "\t}{\n\t\t{\"SAVE10\", 90},\n\t\t{code: \"HALF50\", want: 50},\n\t}\n\t_ = cases\n}\n")
        old = "package cart\n\nfunc discount(code string, sum int) int {\n\treturn sum\n}\n"
        new = ("package cart\n\nfunc discount(code string, sum int) int {\n\tif code == \"HALF50\" {\n\t\treturn 50\n\t}\n"
               "\treturn sum\n}\n")
        report = check_texts({"cart/cart.go": (old, new), "cart/cart_test.go": (None, test)})
        self.assertIn("special-cased-test-value", rules_of(report))

    def test_answer_must_be_an_input_the_test_passes(self):
        # The test compares against "fix_menu" (a value it reads back), it never passes it in.
        test = "def test_kind():\n    card = plan()[0]\n    assert card['kind'] == 'fix_menu'\n    assert card['rerouted'] == 40\n"
        new = "def rerouted(kind, info):\n    if kind == 'fix_menu':\n        return 40\n    return info['n']\n"
        report = check_texts({"app/plan.py": ("x = 1\n", new), "tests/test_plan.py": (None, test)})
        self.assertNotIn("special-cased-test-value", rules_of(report))

    def test_lookup_table(self):
        test = ("def test_roman():\n    assert to_roman(1994) == 'MCMXCIV'\n    assert to_roman(2024) == 'MMXXIV'\n"
                "    assert to_roman(3999) == 'MMMCMXCIX'\n")
        new = "ANSWERS = {1994: 'MCMXCIV', 2024: 'MMXXIV', 3999: 'MMMCMXCIX'}\n\ndef to_roman(n):\n    return ANSWERS[n]\n"
        report = check_texts({"roman.py": ("def to_roman(n):\n    raise NotImplementedError\n", new),
                              "tests/test_roman.py": (None, test)})
        self.assertIn("special-cased-test-value", rules_of(report))


class TestEnvTest(unittest.TestCase):
    def test_runner_detection(self):
        for path, line in [("src/api.ts", "if (process.env.JEST_WORKER_ID) return mockData;"),
                           ("app/service.py", "if 'pytest' in sys.modules:"),
                           ("app/service.py", "if os.environ.get('PYTEST_CURRENT_TEST'):"),
                           ("pkg/clock.go", "if testing.Testing() {")]:
            report = check_texts({path: ("x = 1\n", "x = 1\n" + line + "\n")})
            self.assertIn("test-environment-branch", rules_of(report), line)

    def test_node_env_test_is_medium(self):
        report = check_texts({"src/db.ts": ("a\n", "a\nif (process.env.NODE_ENV === 'test') return;\n")})
        f = [f for f in report.findings if f.rule == "test-environment-branch"][0]
        self.assertEqual(f.severity, Severity.MEDIUM)

    def test_in_test_files_ok(self):
        report = check_texts({"tests/conftest.py": ("a = 1\n", "a = 1\nif os.environ.get('PYTEST_CURRENT_TEST'):\n    pass\n")})
        self.assertNotIn("test-environment-branch", rules_of(report))


class PlaceholderTest(unittest.TestCase):
    def test_strong_placeholder(self):
        report = check_texts({"src/a.ts": ("a\n", "a\n// hardcoded to pass the tests\nreturn 42;\n")})
        f = [f for f in report.findings if f.rule == "placeholder-implementation"][0]
        self.assertEqual(f.severity, Severity.HIGH)

    def test_stub(self):
        report = check_texts({"src/a.py": ("a = 1\n", "a = 1\ndef pay(x):\n    raise NotImplementedError\n")})
        self.assertIn("placeholder-implementation", rules_of(report))

    def test_abstract_method_ok(self):
        new = "class Base(ABC):\n    @abstractmethod\n    def pay(self):\n        raise NotImplementedError\n"
        report = check_texts({"src/a.py": ("a = 1\n", new)})
        self.assertNotIn("placeholder-implementation", rules_of(report))

    def test_stub_after_other_statements_in_a_method(self):
        new = "class Payments:\n    def pay(self, x):\n        log(x)\n        raise NotImplementedError\n"
        report = check_texts({"src/a.py": ("a = 1\n", new)})
        self.assertIn("placeholder-implementation", rules_of(report))


class SwallowAndSuppressTest(unittest.TestCase):
    def test_empty_except(self):
        new = "def f():\n    try:\n        g()\n    except Exception:\n        pass\n"
        self.assertIn("error-swallowed", rules_of(check_texts({"app/f.py": ("x = 1\n", new)})))

    def test_bare_except_continue(self):
        new = "def f(items):\n    for i in items:\n        try:\n            g(i)\n        except:\n            continue\n"
        self.assertIn("error-swallowed", rules_of(check_texts({"app/f.py": ("x = 1\n", new)})))

    def test_logged_error_ok(self):
        new = "try {\n  g();\n} catch (e) {\n  logger.error(e);\n}\n"
        self.assertNotIn("error-swallowed", rules_of(check_texts({"src/f.ts": ("x\n", new)})))

    def test_suppressions(self):
        new = "a\n// @ts-ignore\nb\n// @ts-ignore\nc\n// @ts-ignore\nd\n"
        f = [f for f in check_texts({"src/f.ts": ("a\n", new)}).findings if f.rule == "checks-suppressed"][0]
        self.assertEqual(f.severity, Severity.MEDIUM)
        report = check_texts({"src/g.ts": ("a\n", "// @ts-nocheck\na\n")})
        self.assertIn("checks-suppressed", rules_of(report))


if __name__ == "__main__":
    unittest.main()
