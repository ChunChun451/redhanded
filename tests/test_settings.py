import unittest

import tests.helpers  # noqa: F401  (import path)
from redhanded.model import Severity
from redhanded.settings import Settings, _MiniToml, apply, parse_toml

SAMPLE = '''
# redhanded settings
fail_on = "medium"
block-on = 'high'
announce = false
max_blocks = 2
ignore = [
  "vendor/**",   # third-party code
  "legacy/*.py",
]
test_globs = ["checks/**/*.py"]

[rules]
"test-skipped" = "medium"
coverage-excluded = "off"

[tool.other]
x = { a = 1, b = [1, 2] }
multi = """
line one
line two"""
'''


class TomlTest(unittest.TestCase):
    def test_mini_parser_matches_tomllib(self):
        mine = _MiniToml(SAMPLE).parse()
        try:
            import tomllib
        except ImportError:  # Python < 3.11
            tomllib = None
        if tomllib is not None:
            self.assertEqual(mine, tomllib.loads(SAMPLE))
        self.assertEqual(mine["ignore"], ["vendor/**", "legacy/*.py"])
        self.assertEqual(mine["rules"]["test-skipped"], "medium")
        self.assertEqual(mine["tool"]["other"]["x"], {"a": 1, "b": [1, 2]})
        self.assertEqual(mine["tool"]["other"]["multi"], "line one\nline two")

    def test_invalid(self):
        with self.assertRaises(ValueError):
            parse_toml("this is not = = toml [")


class ApplyTest(unittest.TestCase):
    def test_values(self):
        s = Settings()
        apply(s, parse_toml(SAMPLE))
        self.assertEqual(s.fail_on, Severity.MEDIUM)
        self.assertEqual(s.block_on, Severity.HIGH)
        self.assertFalse(s.announce)
        self.assertEqual(s.max_blocks, 2)
        self.assertEqual(s.ignore, ["vendor/**", "legacy/*.py"])
        self.assertEqual(s.test_globs, ["checks/**/*.py"])
        self.assertEqual(s.rules, {"test-skipped": Severity.MEDIUM, "coverage-excluded": Severity.OFF})
        self.assertEqual(s.warnings, ["unknown setting 'tool'"])

    def test_bad_values_warn(self):
        s = Settings()
        apply(s, {"fail_on": "severe", "max_blocks": "many", "rules": {"nope": "high", "test-skipped": "loud"}})
        self.assertEqual(len(s.warnings), 4)
        self.assertEqual(s.fail_on, Severity.HIGH)
        self.assertEqual(s.rules, {})


if __name__ == "__main__":
    unittest.main()
