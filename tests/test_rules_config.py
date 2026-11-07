import unittest

from tests.helpers import check_texts, rules_of
from redhanded.model import Severity

PKG = '{\n  "name": "x",\n  "scripts": {\n    "test": "vitest run",\n    "lint": "eslint ."\n  }%s\n}\n'


class PackageJsonTest(unittest.TestCase):
    def test_script_neutered(self):
        new = PKG.replace("vitest run", "vitest run || true") % ""
        self.assertIn("test-script-weakened", rules_of(check_texts({"package.json": (PKG % "", new)})))

    def test_script_replaced_with_echo(self):
        new = PKG.replace("vitest run", "echo ok") % ""
        self.assertIn("test-script-weakened", rules_of(check_texts({"package.json": (PKG % "", new)})))

    def test_script_removed(self):
        new = '{\n  "name": "x",\n  "scripts": {\n    "lint": "eslint ."\n  }\n}\n'
        self.assertIn("test-script-weakened", rules_of(check_texts({"package.json": (PKG % "", new)})))

    def test_harmless_script_change(self):
        new = PKG.replace("vitest run", "vitest run --coverage") % ""
        self.assertNotIn("test-script-weakened", rules_of(check_texts({"package.json": (PKG % "", new)})))

    def test_husky_removed(self):
        old = PKG % ',\n  "lint-staged": {"*.ts": "eslint"}'
        self.assertIn("git-hooks-disabled", rules_of(check_texts({"package.json": (old, PKG % "")})))


class TestRunnerConfigTest(unittest.TestCase):
    def test_coverage_threshold_lowered(self):
        old = "[tool.coverage.report]\nfail_under = 90\n"
        new = "[tool.coverage.report]\nfail_under = 40\n"
        report = check_texts({"pyproject.toml": (old, new)})
        self.assertIn("test-config-weakened", rules_of(report))
        self.assertIn("90", report.findings[0].message)

    def test_coverage_threshold_raised_ok(self):
        report = check_texts({"pyproject.toml": ("[tool.coverage.report]\nfail_under = 80\n",
                                                 "[tool.coverage.report]\nfail_under = 90\n")})
        self.assertNotIn("test-config-weakened", rules_of(report))

    def test_pytest_deselect(self):
        old = "[tool.pytest.ini_options]\naddopts = \"-q\"\n"
        new = "[tool.pytest.ini_options]\naddopts = \"-q --deselect tests/test_api.py::test_auth\"\n"
        self.assertIn("test-config-weakened", rules_of(check_texts({"pyproject.toml": (old, new)})))

    def test_unrelated_pyproject_section(self):
        old = "[project]\nname = \"x\"\n"
        new = "[project]\nname = \"x\"\ndescription = \"--deselect is a word here\"\n"
        self.assertNotIn("test-config-weakened", rules_of(check_texts({"pyproject.toml": (old, new)})))

    def test_jest_config_pass_with_no_tests(self):
        report = check_texts({"jest.config.js": ("module.exports = {\n};\n", "module.exports = {\n  passWithNoTests: true,\n};\n")})
        self.assertIn("test-config-weakened", rules_of(report))

    def test_conftest_result_rewrite(self):
        new = "def pytest_runtest_makereport(item, call):\n    pass\n"
        self.assertIn("test-config-weakened", rules_of(check_texts({"tests/conftest.py": ("import pytest\n", "import pytest\n" + new)})))

    def test_discovery_narrowed_is_medium(self):
        old = "module.exports = {\n};\n"
        new = "module.exports = {\n  testPathIgnorePatterns: ['/e2e/'],\n};\n"
        f = [f for f in check_texts({"jest.config.js": (old, new)}).findings if f.rule == "test-config-weakened"][0]
        self.assertEqual(f.severity, Severity.MEDIUM)


class TypeLintConfigTest(unittest.TestCase):
    def test_tsconfig_strict_off(self):
        old = '{\n  "compilerOptions": {\n    "strict": true\n  }\n}\n'
        new = '{\n  "compilerOptions": {\n    "strict": false\n  }\n}\n'
        report = check_texts({"tsconfig.json": (old, new)})
        self.assertEqual(rules_of(report), ["type-or-lint-config-weakened"])

    def test_strict_removed(self):
        old = '{\n  "compilerOptions": {\n    "strict": true,\n    "target": "es2022"\n  }\n}\n'
        new = '{\n  "compilerOptions": {\n    "target": "es2022"\n  }\n}\n'
        self.assertIn("type-or-lint-config-weakened", rules_of(check_texts({"tsconfig.json": (old, new)})))

    def test_mypy_ignore_errors(self):
        old = "[tool.mypy]\nstrict = true\n"
        new = "[tool.mypy]\nstrict = true\nignore_errors = true\n"
        self.assertIn("type-or-lint-config-weakened", rules_of(check_texts({"pyproject.toml": (old, new)})))

    def test_eslint_rules_off(self):
        old = "module.exports = {\n  rules: {\n  },\n};\n"
        new = "module.exports = {\n  rules: {\n    'no-unused-vars': 'off',\n    '@typescript-eslint/no-explicit-any': 'off',\n  },\n};\n"
        f = [f for f in check_texts({".eslintrc.js": (old, new)}).findings if f.rule == "type-or-lint-config-weakened"][0]
        self.assertEqual(f.severity, Severity.MEDIUM)
        self.assertIn("2 lint rules", f.message)

    def test_enabling_strict_ok(self):
        old = '{\n  "compilerOptions": {\n    "strict": false\n  }\n}\n'
        new = '{\n  "compilerOptions": {\n    "strict": true\n  }\n}\n'
        self.assertEqual(rules_of(check_texts({"tsconfig.json": (old, new)})), [])


WORKFLOW = """name: ci
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm ci
      - run: npm test
"""


class CiTest(unittest.TestCase):
    path = ".github/workflows/ci.yml"

    def test_continue_on_error(self):
        new = WORKFLOW.replace("      - run: npm test", "      - run: npm test\n        continue-on-error: true")
        self.assertIn("ci-weakened", rules_of(check_texts({self.path: (WORKFLOW, new)})))

    def test_or_true(self):
        new = WORKFLOW.replace("run: npm test", "run: npm test || true")
        self.assertIn("ci-weakened", rules_of(check_texts({self.path: (WORKFLOW, new)})))

    def test_step_removed(self):
        new = WORKFLOW.replace("      - run: npm test\n", "")
        self.assertIn("ci-weakened", rules_of(check_texts({self.path: (WORKFLOW, new)})))

    def test_workflow_deleted(self):
        self.assertIn("ci-weakened", rules_of(check_texts({self.path: (WORKFLOW, None)})))

    def test_adding_steps_ok(self):
        new = WORKFLOW + "      - run: npm run lint\n"
        self.assertEqual(rules_of(check_texts({self.path: (WORKFLOW, new)})), [])

    def test_renamed_step_ok(self):
        new = WORKFLOW.replace("      - run: npm test", "      - name: Unit tests\n        run: npm test")
        self.assertEqual(rules_of(check_texts({self.path: (WORKFLOW, new)})), [])


class HooksTest(unittest.TestCase):
    def test_husky_emptied(self):
        report = check_texts({".husky/pre-commit": ("npx lint-staged\nnpm test\n", "exit 0\n")})
        self.assertIn("git-hooks-disabled", rules_of(report))

    def test_pre_commit_hook_removed(self):
        old = "repos:\n  - repo: https://github.com/astral-sh/ruff-pre-commit\n    rev: v0.6.0\n    hooks:\n      - id: ruff\n      - id: ruff-format\n"
        new = "repos:\n  - repo: https://github.com/astral-sh/ruff-pre-commit\n    rev: v0.6.0\n    hooks:\n      - id: ruff-format\n"
        self.assertIn("git-hooks-disabled", rules_of(check_texts({".pre-commit-config.yaml": (old, new)})))


if __name__ == "__main__":
    unittest.main()
