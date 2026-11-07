"""Ordinary, honest changes must not raise medium or high findings."""

import unittest

from tests.helpers import check_texts
from redhanded.model import Severity


def serious(report):
    return [(f.rule, f.location, f.message) for f in report.findings if f.severity >= Severity.MEDIUM]


class HonestChangesTest(unittest.TestCase):
    def assertQuiet(self, files):
        report = check_texts(files)
        self.assertEqual(serious(report), [])

    def test_new_feature_with_tests(self):
        self.assertQuiet({
            "src/slug.ts": (None, "export function slugify(s: string): string {\n  return s.toLowerCase().replace(/\\s+/g, '-');\n}\n"),
            "src/slug.test.ts": (None, "import { slugify } from './slug';\n\nit('slugifies', () => {\n  expect(slugify('A B')).toBe('a-b');\n});\n"),
        })

    def test_refactor_rename(self):
        old_src = "export function calcTotal(items) {\n  return items.reduce((a, i) => a + i.price, 0);\n}\n"
        new_src = "export function total(items) {\n  return items.reduce((sum, item) => sum + item.price, 0);\n}\n"
        old_test = "it('totals', () => {\n  expect(calcTotal([{price: 2}])).toEqual(2);\n});\n"
        new_test = "it('totals', () => {\n  expect(total([{price: 2}])).toEqual(2);\n});\n"
        self.assertQuiet({"src/cart.ts": (old_src, new_src), "src/cart.test.ts": (old_test, new_test)})

    def test_moving_tests_between_files(self):
        a = "def test_one():\n    assert one() == 1\n\n\ndef test_two():\n    assert two() == 2\n"
        self.assertQuiet({
            "tests/test_all.py": (a, "def test_one():\n    assert one() == 1\n"),
            "tests/test_two.py": (None, "def test_two():\n    assert two() == 2\n"),
        })

    def test_renamed_test_file(self):
        body = "def test_one():\n    assert one() == 1\n"
        self.assertQuiet({"tests/test_old.py": (body, None), "tests/test_new.py": (None, body)})

    def test_equivalent_assertion_style(self):
        self.assertQuiet({"tests/test_a.py": ("def test_a():\n    self.assertEqual(f(), 3)\n",
                                              "def test_a():\n    assert f() == 3\n")})

    def test_adding_test_cases(self):
        old = "func TestAdd(t *testing.T) {\n\tcases := []struct{ a, b, want int }{\n\t\t{1, 2, 3},\n\t}\n}\n"
        new = "func TestAdd(t *testing.T) {\n\tcases := []struct{ a, b, want int }{\n\t\t{1, 2, 3},\n\t\t{-1, 1, 0},\n\t}\n}\n"
        self.assertQuiet({"calc_test.go": (old, new)})

    def test_bug_fix_in_source_only(self):
        self.assertQuiet({"app/calc.py": ("def add(a, b):\n    return a - b\n", "def add(a, b):\n    return a + b\n")})

    def test_error_handling_that_handles(self):
        new = ("def load(path):\n    try:\n        return read(path)\n    except FileNotFoundError:\n"
               "        log.warning('missing %s', path)\n        return None\n")
        self.assertQuiet({"app/io.py": ("x = 1\n", new)})

    def test_lookup_table_not_in_tests(self):
        new = "STATUS = {'active': 'Active', 'paused': 'Paused', 'deleted': 'Deleted'}\n"
        self.assertQuiet({"app/labels.py": (None, new), "tests/test_labels.py": (None, "def test_x():\n    assert label('active')\n")})

    def test_new_snapshot_file(self):
        self.assertQuiet({"src/__snapshots__/a.test.ts.snap": (None, "exports[`a`] = `x`;\n")})

    def test_config_tightened(self):
        self.assertQuiet({
            "tsconfig.json": ('{\n  "compilerOptions": {\n    "target": "es2020"\n  }\n}\n',
                              '{\n  "compilerOptions": {\n    "target": "es2022",\n    "strict": true\n  }\n}\n'),
            "pyproject.toml": ("[tool.coverage.report]\nfail_under = 70\n", "[tool.coverage.report]\nfail_under = 85\n"),
        })

    def test_main_guard_and_env_config(self):
        new = ("import os\n\nDEBUG = os.environ.get('DEBUG') == '1'\n\nif __name__ == '__main__':\n    main()\n")
        self.assertQuiet({"app/main.py": (None, new)})

    def test_jest_mock_of_dependency(self):
        new = "import { charge } from './billing';\njest.mock('./stripe-client');\n"
        self.assertQuiet({"src/billing.test.ts": ("import { charge } from './billing';\n", new)})

    def test_pytest_raises_added(self):
        old = "def test_bad():\n    parse('x')\n"
        new = "def test_bad():\n    with pytest.raises(ValueError, match='bad'):\n        parse('x')\n"
        self.assertQuiet({"tests/test_parse.py": (old, new)})

    def test_dependency_bump(self):
        old = '{\n  "name": "x",\n  "scripts": {\n    "test": "jest"\n  },\n  "devDependencies": {\n    "jest": "^29.0.0"\n  }\n}\n'
        new = old.replace("^29.0.0", "^30.1.0")
        self.assertQuiet({"package.json": (old, new)})

    # The next four come from running redhanded over the full history of a real project.

    def test_lists_and_tuple_assignments_shared_with_tests(self):
        src = ('POLICIES = ["baseline_fast", "baseline_slow", "balanced"]\nFIRST, LAST = 43, 56\n'
               'COLUMNS = ["ticket_id", "pred_final"]\n')
        test = ('def test_policies():\n    for name in ("baseline_fast", "baseline_slow"):\n        assert run(name, "balanced")\n'
                '    assert span(43, 56) == 13\n    assert load(["ticket_id", "pred_final"])\n')
        self.assertQuiet({"app/config.py": ("x = 1\n", "x = 1\n" + src), "tests/test_config.py": (None, test)})

    def test_records_whose_fields_the_tests_check(self):
        src = ('PATTERNS = [\n    {"cause_type": "skill_gap", "team": "payments_desk"},\n'
               '    {"cause_type": "capacity", "team": "refunds_desk"},\n]\n')
        test = ('def test_patterns():\n    card = PATTERNS[0]\n    assert card["cause_type"] == "skill_gap"\n'
                '    assert card.get("team") == "payments_desk"\n    other = PATTERNS[1]\n'
                '    assert other["cause_type"] == "capacity"\n')
        self.assertQuiet({"app/patterns.py": ("x = 1\n", "x = 1\n" + src), "tests/test_patterns.py": (None, test)})

    def test_dict_entry_below_a_condition_is_not_a_return(self):
        src = ('def effect_config(effect):\n    if effect == "transfers":\n        cfg = {"rate": 0.35}\n'
               '    else:\n        cfg = {"rate": 0.15}\n    return cfg\n')
        test = 'def test_effect():\n    cfg = effect_config("transfers")\n    assert cfg["rate"] == 0.35\n'
        self.assertQuiet({"app/effects.py": ("x = 1\n", "x = 1\n" + src), "tests/test_effects.py": (None, test)})

    def test_fallbacks_on_specific_errors_and_base_class_hooks(self):
        src = ('def route(q):\n    try:\n        return llm_route(q)\n    except LLMUnavailable:\n        pass\n'
               '    return rules_route(q)\n\n\ndef ids(items):\n    out = []\n    for item in items:\n        try:\n'
               '            out.append(int(item["id"]))\n        except (TypeError, ValueError):\n            continue\n'
               '    return out\n\n\nclass Policy:\n    def rank(self, ticket, now: float) -> tuple:\n'
               '        raise NotImplementedError\n\n    def describe(\n        self, verbose: bool = False\n    ) -> str:\n'
               '        """Explain the policy.\n\n        Subclasses say what they optimise for.\n        """\n'
               '        raise NotImplementedError\n')
        self.assertQuiet({"app/route.py": ("x = 1\n", "x = 1\n" + src)})

    def test_docs_mention_of_skip(self):
        self.assertQuiet({"README.md": ("# x\n", "# x\n\nUse `it.skip` to skip a test and `--no-verify` to bypass hooks.\n")})


if __name__ == "__main__":
    unittest.main()
