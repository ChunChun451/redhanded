import unittest

from tests import helpers  # noqa: F401  (sets the import path)
from redhanded.commands import (BUILD, LINT, NOT_RUN, TEST, TYPECHECK, exit_code_from_result, outcome, parse_command,
                                split_segments)


class ClassifyTest(unittest.TestCase):
    def kinds(self, cmd):
        return parse_command(cmd).kinds

    def test_test_runners(self):
        for cmd in ["pytest", "pytest -q tests/", "python -m pytest -x", "py -3 -m pytest", "uv run pytest",
                    "npx jest", "npm test", "npm t", "npm run test:unit", "yarn test", "pnpm -r test", "bun test",
                    "go test ./...", "cargo test", "cargo nextest run", "./gradlew test", "mvn -q verify",
                    "dotnet test", "bundle exec rspec", "vendor/bin/phpunit", "make test", "tox -e py312",
                    "cd web && npm test", "CI=1 npx vitest run", "timeout 300 pytest", "node --test",
                    "python manage.py test", "deno test", "mix test", "swift test"]:
            self.assertIn(TEST, self.kinds(cmd), cmd)

    def test_other_kinds(self):
        self.assertEqual(self.kinds("npx tsc --noEmit"), {TYPECHECK, BUILD})
        self.assertIn(TYPECHECK, self.kinds("mypy src"))
        self.assertIn(LINT, self.kinds("npx eslint ."))
        self.assertIn(LINT, self.kinds("ruff check ."))
        self.assertIn(LINT, self.kinds("cargo clippy -- -D warnings"))
        self.assertIn(BUILD, self.kinds("npm run build"))
        self.assertEqual(self.kinds("npm run check"), {TEST, BUILD, TYPECHECK, LINT})

    def test_not_runs(self):
        for cmd in ["pip install pytest", "cat jest.config.js", "grep -r pytest .", "pytest --version",
                    "pytest --collect-only", "npm install", "git commit -m 'fix tests'", "echo npm test",
                    "ls tests", "which pytest", "npx tsc --init", "ruff format ."]:
            self.assertEqual(self.kinds(cmd), set(), cmd)

    def test_project_scripts(self):
        for cmd in ["./run_tests.sh", "bash scripts/test.sh", "sh ./tests/run.sh", "bin/test", "./test",
                    "python run_tests.py", "python tests/test_api.py", "node test/index.js", "bash -lc 'npm test'",
                    "bash -o pipefail -c 'pytest -q'", "pwsh -File scripts/test.ps1", ".\\scripts\\run-tests.ps1",
                    "cmd /c npm test", "uv run scripts/check.py", "bun scripts/check.ts"]:
            self.assertIn(TEST, self.kinds(cmd), cmd)
        self.assertEqual(self.kinds("./scripts/check"), {TEST, BUILD, TYPECHECK, LINT})
        self.assertEqual(self.kinds("scripts/lint.sh"), {LINT})
        for cmd in ["test -f setup.py", "./deploy.sh", "bash scripts/release.sh", "python scripts/gen_docs.py",
                    "node server.js", "./scripts/check-links.sh", "python -c 'import test'", "sh -c 'echo hi'",
                    "cat scripts/test.sh", "node -e 'require(\"./test\")'"]:
            self.assertEqual(self.kinds(cmd), set(), cmd)
        # A runner script is the whole suite; a test file run directly is one file.
        self.assertFalse(parse_command("python run_tests.py").subset)
        self.assertFalse(parse_command("node test/index.js").subset)
        self.assertTrue(parse_command("python tests/test_api.py").subset)
        self.assertTrue(parse_command("./run_tests.sh tests/test_api.py").subset)
        # The command inside `bash -c` is read in full.
        self.assertTrue(parse_command("bash -c 'pytest --deselect tests/test_a.py::test_b'").exclusions)
        self.assertTrue(parse_command("sh -c 'git commit --no-verify -m x'").bypass)
        self.assertTrue(parse_command("bash -c 'npm test' || true").masked)

    def test_compilers_and_build_tools(self):
        for cmd in ["g++ -std=c++20 -o calc calc.cpp", "gcc -Wall main.c", "javac -d out src/Main.java", "rustc main.rs",
                    "cmake --build build", "ninja -C build", "npx vite build", "next build",
                    "npx webpack --mode production"]:
            self.assertIn(BUILD, self.kinds(cmd), cmd)
        self.assertIn(TEST, self.kinds("cmake --build build --target test"))
        self.assertEqual(self.kinds("meson test -C build"), {TEST})
        self.assertIn(LINT, self.kinds("pre-commit run --all-files"))
        self.assertEqual(self.kinds("bash -n scripts/deploy.sh"), {LINT})
        self.assertEqual(self.kinds("node --check static/app.js"), {LINT})
        for cmd in ["cmake -S . -B build", "gcc --version", "clang++ -dumpversion", "npx vite", "npx webpack serve"]:
            self.assertEqual(self.kinds(cmd), set(), cmd)

    def test_languages(self):
        def langs(cmd):
            return parse_command(cmd).langs
        self.assertEqual(langs("python -m pytest -q"), {"py"})
        self.assertEqual(langs("npx jest"), {"js"})
        self.assertEqual(langs("npm test"), {"js"})
        self.assertEqual(langs("go test ./..."), {"go"})
        self.assertEqual(langs("cmake --build build && ctest --test-dir build"), {"cpp"})
        self.assertEqual(langs("bash -n scripts/deploy.sh"), {"shell"})
        self.assertEqual(langs("bash -c 'cargo test'"), {"rust"})
        self.assertEqual(langs("ruff check . && pytest"), {"py"})
        for cmd in ["make test", "./scripts/check", "python run_tests.py", "pre-commit run --all-files"]:
            self.assertEqual(langs(cmd), {"*"}, cmd)

    def test_masking_and_filters(self):
        self.assertTrue(parse_command("npm test 2>&1 | tail -20").masked)
        self.assertTrue(parse_command("pytest || true").masked)
        self.assertFalse(parse_command("pytest && echo ok").masked)
        self.assertFalse(parse_command("pytest 2>&1").masked)
        cmd = parse_command("pytest --deselect tests/test_api.py::test_slow -q")
        self.assertTrue(cmd.exclusions)
        self.assertTrue(parse_command('pytest -k "not flaky"').exclusions)
        # Turning off an ordinary plugin runs the same tests; turning off a checking plugin drops its checks.
        self.assertFalse(parse_command("python -m pytest -q -p no:warnings -p no:cacheprovider").exclusions)
        self.assertTrue(parse_command("pytest -p no:cov").exclusions)
        self.assertTrue(parse_command("pytest tests/test_api.py").subset)
        self.assertFalse(parse_command("python -m pytest tests/").subset)
        self.assertTrue(parse_command("npx jest -t 'adds numbers'").subset)

    def test_bypass(self):
        self.assertTrue(parse_command("git commit --no-verify -m wip").bypass)
        self.assertTrue(parse_command("git commit -nm wip").bypass)
        self.assertTrue(parse_command("HUSKY=0 git commit -m x").bypass)
        self.assertTrue(parse_command("SKIP=eslint git commit -m x").bypass)
        self.assertTrue(parse_command("git -c core.hooksPath=/dev/null commit -m x").bypass)
        self.assertTrue(parse_command("git push --no-verify").bypass)
        self.assertFalse(parse_command("git commit -m 'no-verify mentioned in message'").bypass)
        self.assertFalse(parse_command("git commit -am 'normal'").bypass)
        self.assertFalse(parse_command("git push -n origin main").bypass)

    def test_deletes(self):
        self.assertEqual(parse_command("rm -f tests/test_old.py").deletes, ["tests/test_old.py"])
        self.assertEqual(parse_command("git rm -q src/a.test.ts").deletes, ["src/a.test.ts"])

    def test_writes(self):
        self.assertEqual(parse_command("cat > tests/test_tmp.py <<'EOF'\nx = 1\nEOF").writes, ["tests/test_tmp.py"])
        cmd = parse_command("cp /tmp/dbg.py tests/test_dbg.py && pytest -q tests/test_dbg.py; rm tests/test_dbg.py")
        self.assertIn("tests/test_dbg.py", cmd.writes)
        self.assertEqual(cmd.deletes, ["tests/test_dbg.py"])
        self.assertEqual(parse_command("pytest -q 2>&1 | tee out.log").writes, ["out.log"])
        self.assertEqual(parse_command("npm test > /dev/null 2>&1").writes, ["/dev/null"])
        self.assertEqual(parse_command("echo 'a > b' && ls").writes, [])

    def test_split_respects_quotes(self):
        segs = split_segments("echo 'a && b' && pytest -q; echo done")
        self.assertEqual([s for s, _ in segs], ["echo 'a && b'", "pytest -q", "echo done"])


class OutcomeTest(unittest.TestCase):
    def check(self, cmd, output, expected, code=None):
        self.assertEqual(outcome(parse_command(cmd), output, exit_code=code).status, expected, output)

    def test_markers(self):
        self.check("pytest", "===== 2 failed, 10 passed in 1.20s =====", "fail")
        self.check("pytest", "===== 12 passed, 1 skipped in 0.50s =====", "pass")
        self.check("pytest", "===== 3 xfailed, 4 passed in 0.50s =====", "pass")
        self.check("npx jest", "Tests:       1 failed, 41 passed, 42 total", "fail")
        self.check("npx jest", "Tests:       42 passed, 42 total", "pass")
        self.check("npx vitest run", " Test Files  1 failed | 3 passed (4)\n      Tests  2 failed | 30 passed (32)", "fail")
        self.check("go test ./...", "--- FAIL: TestAdd (0.00s)\nFAIL", "fail")
        self.check("go test ./...", "ok  \texample.com/calc\t0.012s", "pass")
        self.check("cargo test", "test result: FAILED. 3 passed; 1 failed", "fail")
        self.check("cargo test", "test result: ok. 4 passed; 0 failed", "pass")
        self.check("npx tsc --noEmit", "src/a.ts(3,5): error TS2322: Type 'string' is not assignable", "fail")
        self.check("mypy .", "Success: no issues found in 12 source files", "pass")
        self.check("bundle exec rspec", "12 examples, 0 failures", "pass")
        self.check("bundle exec rspec", "12 examples, 2 failures", "fail")

    def test_exit_codes(self):
        self.check("pytest", "something", "fail", code=1)
        self.check("pytest", "", "pass", code=0)
        self.check("npm test 2>&1 | tail -5", "", "unknown", code=0)  # pipe hides the status
        self.check("npm test 2>&1 | tail -5", "Tests:  3 failed, 9 total", "fail", code=0)

    def test_could_not_run(self):
        for output, code in (("bash: pytest: command not found", 127), ("", 127),
                             ("npm ERR! Missing script: \"test\"", 1)):
            res = outcome(parse_command("npm test"), output, exit_code=code)
            self.assertEqual(res.reason, NOT_RUN, output)
        self.assertNotEqual(outcome(parse_command("pytest"), "==== 1 failed in 0.1s ====", exit_code=1).reason, NOT_RUN)

    def test_exit_code_from_result(self):
        self.assertEqual(exit_code_from_result("Exit code 2\nboom", True), 2)
        self.assertEqual(exit_code_from_result("fine", False), 0)


if __name__ == "__main__":
    unittest.main()
