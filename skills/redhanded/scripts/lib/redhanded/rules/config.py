"""Rules about configuration that decides what counts as passing: test runners, scripts, linters, CI, hooks."""

from __future__ import annotations

import json
import posixpath
import re
from typing import Dict, Iterable, List, Optional, Tuple

from ..classify import is_comment_line
from ..diffs import change_blocks
from ..model import FileChange, Finding, Line, Severity
from . import Context, finding, net_added, net_removed

H, M, L = Severity.HIGH, Severity.MEDIUM, Severity.LOW

CHECK_COMMAND = re.compile(
    r"""(?x)
    \b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|tests|lint|typecheck|type-check|check|build|ci|verify|e2e)\b
  | \b(?:pytest|py\.test|tox|nox|jest|vitest|mocha|ava|playwright\s+test|cypress\s+run|tsc|vue-tsc|mypy|pyright
       |eslint|biome\s+(?:check|ci|lint)|ruff\s+check|ruff|flake8|pylint|rspec|phpunit|golangci-lint|clippy|swiftlint|rubocop)\b
  | \bgo\s+(?:test|vet)\b | \bcargo\s+(?:test|clippy|check|nextest)\b | \bmake\s+(?:test|check|lint|ci)\b
  | \b(?:mvn|mvnw|gradle|gradlew)\b.*\b(?:test|verify|check)\b | \bdotnet\s+test\b | \bswift\s+test\b | \bmix\s+test\b
  | \bpython\d?(?:\.\d+)?\s+-m\s+(?:pytest|unittest|mypy)\b | \bmanage\.py\s+test\b | \bbazel\s+test\b
    """
)

# --------------------------------------------------------------------------- helpers


def _section(change: FileChange, line: Line) -> str:
    """Nearest preceding [section] header for INI/TOML style files."""
    if line.new_no is None:
        return ""
    lines = change.new_lines()
    for i in range(min(line.new_no, len(lines)) - 1, -1, -1):
        m = re.match(r"^\s*\[+\s*([^\]]+?)\s*\]+\s*$", lines[i])
        if m:
            return m.group(1).strip().lower()
    return ""


def _is_section(name: str, *prefixes: str) -> bool:
    return any(name == p or name.startswith(p + ".") or name.startswith(p + ":") or name.startswith(p + "-")
               for p in prefixes)


def _added_code(change: FileChange) -> List[Line]:
    return net_added(change, lambda t: bool(t.strip()) and not is_comment_line(t, change.info.lang))


def _base(change: FileChange) -> str:
    return posixpath.basename(change.path).lower()


# --------------------------------------------------------------------------- test runner config

TEST_CFG_HIGH = re.compile(
    r"""(?x)
    passWithNoTests["']?\s*[:=]\s*true | --passWithNoTests\b | --pass-with-no-tests\b
  | --deselect\b | -k\s+["']?not\s | \bcollect_ignore(?:_glob)?\b
  | \bdef\s+pytest_(?:collection_modifyitems|runtest_makereport|runtest_logreport|report_teststatus|runtest_call|runtest_protocol)\b
  | \.outcome\s*=\s*["']passed["'] | \bforce_(?:result|exception)\s*\( | \.longrepr\s*=\s*None
  | \.(?:add_marker|applymarker)\(\s*pytest\.mark\.(?:skip|xfail)
  | --testFailureExitCode\s*[= ]\s*0 | \bsuccessExitCodes?\b
  | -DskipTests\b | -Dmaven\.test\.skip=true | --exclude-task\s+test\b | (?:^|\s)-x\s+test\b
  | \bgo\s+test\b.*\s-skip\b | \bcargo\s+test\b.*\s--skip\b
    """
)
TEST_CFG_DISCOVERY = re.compile(
    r"""(?x)
    \b(?:testPathIgnorePatterns|modulePathIgnorePatterns|testIgnore|testMatch|testRegex|grepInvert)\b
  | --testPathIgnorePatterns\b | --ignore(?:-glob)?[= ] | \bnorecursedirs\b | \btestpaths\b
  | \bpython_(?:files|functions|classes)\b | \bxfail_strict\s*=\s*(?:false|False|0) | \s-p\s+no:
  | \bexclude\s*:\s*\[ | \binclude\s*:\s*\[ | \btestTimeout\s*:\s*\d{6,}
    """
)
RETRY_CFG = re.compile(r"\bretries\s*:\s*[1-9]|\bretry\s*:\s*[1-9]|--reruns\b|\bretryTimes\b|\breruns\s*=\s*[1-9]")
THRESHOLD = re.compile(
    r"""(?ix)["']?-{0,2}(fail[_-]under|cov[_-]fail[_-]under|branches|functions|lines|statements|threshold
         |minimum[_-]?coverage|min[_-]?coverage|coverage[_-]?threshold|minimum)["']?\s*[:=]\s*["']?(\d+(?:\.\d+)?)"""
)
PYTEST_SECTIONS = ("tool.pytest.ini_options", "tool:pytest", "pytest", "tool.pytest")
COVERAGE_SECTIONS = ("tool.coverage", "coverage", "report", "run", "tool.coverage.report")


def _test_config_relevant(change: FileChange, line: Line) -> bool:
    info = change.info
    if "test-runner" in info.config or "ci" in info.config:
        return True
    base = _base(change)
    if base in ("pyproject.toml", "setup.cfg", "tox.ini"):
        return _is_section(_section(change, line), *(PYTEST_SECTIONS + COVERAGE_SECTIONS))
    if base in ("makefile", "gnumakefile", "justfile", "taskfile.yml", "taskfile.yaml", "build.gradle", "build.gradle.kts",
                "pom.xml", "noxfile.py"):
        return True
    return False


def check_test_config(change: FileChange, ctx: Context) -> Iterable[Finding]:
    info = change.info
    if not (info.config & {"test-runner", "manifest", "ci"}) and _base(change) != "conftest.py":
        return
    if _base(change) == "package.json":
        return  # handled structurally in check_package_json
    for ln in _added_code(change):
        if not _test_config_relevant(change, ln) and _base(change) != "conftest.py":
            continue
        m = TEST_CFG_HIGH.search(ln.text)
        if m:
            yield finding("test-config-weakened", "Test run can no longer fail as before (`%s`)" % m.group(0).strip()[:60],
                          change, ln)
            continue
        if "ci" in info.config:
            continue
        m = TEST_CFG_DISCOVERY.search(ln.text)
        if m:
            yield finding("test-config-weakened", "Test discovery changed (`%s`): check that no tests were excluded"
                          % m.group(0).strip()[:60], change, ln, severity=M)
            continue
        m = RETRY_CFG.search(ln.text)
        if m:
            yield finding("test-retries-added", "Retries enabled in test config (`%s`)" % m.group(0).strip()[:40], change, ln)
    yield from _threshold_changes(change)


def _threshold_changes(change: FileChange) -> Iterable[Finding]:
    for removed, added in change_blocks(change):
        olds: Dict[str, Tuple[float, Line]] = {}
        for ln in removed:
            for m in THRESHOLD.finditer(ln.text):
                olds[m.group(1).lower().replace("-", "_")] = (float(m.group(2)), ln)
        for ln in added:
            for m in THRESHOLD.finditer(ln.text):
                key = m.group(1).lower().replace("-", "_")
                if key in olds and float(m.group(2)) < olds[key][0]:
                    yield finding("test-config-weakened", "Coverage threshold `%s` lowered from %g to %g"
                                  % (m.group(1), olds[key][0], float(m.group(2))), change, ln,
                                  before=olds[key][1].text.strip()[:240])


# --------------------------------------------------------------------------- package.json & task runners

CHECK_SCRIPT_NAME = re.compile(
    r"(?:^|:)(?:test|tests|spec|check|checks|verify|lint|typecheck|type-check|types|ci|validate|e2e|unit|integration)(?::|$)",
    re.I,
)
RUNNER = re.compile(
    r"\b(?:jest|vitest|mocha|ava|tap|playwright|cypress|karma|tsc|vue-tsc|svelte-check|eslint|biome|oxlint|xo|standard|"
    r"node\s+--test|bun\s+test|deno\s+(?:test|lint|check)|pytest|mypy|ruff|turbo|nx|lerna|npm|pnpm|yarn|bun|run-s|run-p|"
    r"npm-run-all\d?|concurrently|c8|nyc|uvu|ts-node|tsx|node|make|cargo|go|wireit|vite|next|astro|stylelint|prettier)\b"
)
NEUTER = re.compile(
    r"\|\|\s*(?:true|exit\s+0|echo\b|:)|;\s*exit\s+0\s*$|^\s*(?:echo\b|true\s*$|exit\s+0|:\s*$)|--passWithNoTests\b|"
    r"--pass-with-no-tests\b|--testFailureExitCode\s*[= ]\s*0|--max-warnings\s*[= ]\s*\d{4,}|--no-error-on-unmatched-pattern"
)


def _load_json(text: Optional[str]) -> Optional[dict]:
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _line_containing(change: FileChange, needle: str, removed: bool = False) -> Optional[Line]:
    pool = change.removed() if removed else change.added()
    for ln in pool:
        if needle in ln.text:
            return ln
    return None


def check_package_json(change: FileChange, ctx: Context) -> Iterable[Finding]:
    if _base(change) != "package.json" or change.status == "A":
        return
    old, new = _load_json(change.old_text), _load_json(change.new_text)
    if old is None:
        return
    new = new or {}
    old_scripts = old.get("scripts") if isinstance(old.get("scripts"), dict) else {}
    new_scripts = new.get("scripts") if isinstance(new.get("scripts"), dict) else {}
    for name, cmd in old_scripts.items():
        if not isinstance(cmd, str) or not CHECK_SCRIPT_NAME.search(name):
            continue
        if name not in new_scripts:
            yield finding("test-script-weakened", "Script `%s` was removed" % name, change,
                          _line_containing(change, '"%s"' % name, removed=True), snippet='- "%s": "%s"' % (name, cmd[:120]))
            continue
        after = new_scripts.get(name)
        if not isinstance(after, str) or after == cmd:
            continue
        if NEUTER.search(after) and not NEUTER.search(cmd):
            yield finding("test-script-weakened", "Script `%s` can no longer fail" % name, change,
                          _line_containing(change, '"%s"' % name), before='"%s": "%s"' % (name, cmd[:120]))
        elif RUNNER.search(cmd) and not RUNNER.search(after):
            yield finding("test-script-weakened", "Script `%s` no longer runs any checker" % name, change,
                          _line_containing(change, '"%s"' % name), before='"%s": "%s"' % (name, cmd[:120]))
    old_jest = old.get("jest") if isinstance(old.get("jest"), dict) else {}
    new_jest = new.get("jest") if isinstance(new.get("jest"), dict) else {}
    if new_jest.get("passWithNoTests") is True and old_jest.get("passWithNoTests") is not True:
        yield finding("test-config-weakened", "jest.passWithNoTests enabled", change, _line_containing(change, "passWithNoTests"))
    for key in ("testPathIgnorePatterns", "modulePathIgnorePatterns"):
        before = old_jest.get(key) or []
        after = new_jest.get(key) or []
        if isinstance(after, list) and isinstance(before, list) and len(after) > len(before):
            yield finding("test-config-weakened", "jest.%s now excludes more paths" % key, change,
                          _line_containing(change, key), severity=M)
    yield from _threshold_changes(change)
    old_prepare = str(old_scripts.get("prepare", ""))
    new_prepare = str(new_scripts.get("prepare", ""))
    if "husky" in old_prepare and "husky" not in new_prepare:
        yield finding("git-hooks-disabled", "Husky install removed from the `prepare` script", change,
                      _line_containing(change, "prepare", removed=True), severity=M)
    for key in ("husky", "simple-git-hooks", "pre-commit", "lint-staged"):
        if key in old and key not in new:
            yield finding("git-hooks-disabled", "`%s` configuration removed" % key, change,
                          _line_containing(change, '"%s"' % key, removed=True), severity=M)


def check_task_runner(change: FileChange, ctx: Context) -> Iterable[Finding]:
    if _base(change) not in ("makefile", "gnumakefile", "justfile", "taskfile.yml", "taskfile.yaml", "tox.ini", "noxfile.py"):
        return
    for ln in _added_code(change):
        if not CHECK_COMMAND.search(ln.text):
            continue
        if re.search(r"\|\|\s*(?:true|exit\s+0|:)|;\s*true\s*$|^\t-\S|--passWithNoTests", ln.text):
            yield finding("test-script-weakened", "Check command can no longer fail", change, ln)


# --------------------------------------------------------------------------- type & lint config

TYPE_LINT_HIGH = re.compile(
    r"""(?x)
    "(?:strict|noImplicitAny|strictNullChecks|checkJs|strictFunctionTypes|noImplicitThis)"\s*:\s*false
  | ^\s*(?:strict|disallow_untyped_defs|check_untyped_defs|warn_return_any)\s*=\s*(?:false|False)
  | ^\s*ignore_errors\s*=\s*(?:true|True)
  | ^\s*follow_imports\s*=\s*["']?skip
  | "?typeCheckingMode"?\s*[:=]\s*"(?:off|basic)"
  | "reportGeneralTypeIssues"\s*:\s*(?:false|"none")
  | \#!\[\s*allow\s*\(\s*warnings
    """
)
STRICT_ON = re.compile(r'"strict"\s*:\s*true|^\s*strict\s*=\s*(?:true|True)')
RULE_OFF = re.compile(r"""^\s*["']?[@\w/.-]+["']?\s*:\s*(?:["']off["']|0|\[\s*["']off["']|\[\s*0\b)""")
LINT_LOOSEN = re.compile(
    r"""(?x)
    \b(?:ignorePatterns|ignores|ignore_patterns|extend-exclude|per-file-ignores|extend-ignore|exclude-rules
        |skip-dirs|skip-files|disabled_rules|excluded|excludePatterns|ignoreFiles)\b
  | ^\s*(?:ignore|exclude|disable|extend-ignore)\s*[:=]
  | \bEnabled:\s*false\b | --ignore-pattern\b | "exclude"\s*:\s*\[
    """
)
LEVEL = re.compile(r"^\s*level\s*:\s*(\d+)")
TYPE_LINT_SECTIONS = ("tool.mypy", "mypy", "tool.ruff", "tool.pylint", "pylint", "flake8", "tool.pyright", "pyright",
                      "messages control", "tool.flake8", "pycodestyle", "pydocstyle", "tool.basedpyright", "tool.ty")


def check_type_lint_config(change: FileChange, ctx: Context) -> Iterable[Finding]:
    info = change.info
    base = _base(change)
    shared = base in ("pyproject.toml", "setup.cfg", "tox.ini")
    if not (info.config & {"types", "lint"}) and not shared:
        return
    off_rules: List[Line] = []
    loosened: List[Line] = []
    reported_high = False
    for ln in _added_code(change):
        if shared and not _is_section(_section(change, ln), *TYPE_LINT_SECTIONS):
            continue
        m = TYPE_LINT_HIGH.search(ln.text)
        if m:
            reported_high = True
            yield finding("type-or-lint-config-weakened", "Type checking loosened (`%s`)" % m.group(0).strip()[:60], change, ln)
            continue
        if "lint" in info.config and RULE_OFF.search(ln.text):
            off_rules.append(ln)
        elif LINT_LOOSEN.search(ln.text):
            loosened.append(ln)
    if not reported_high and not net_added(change, lambda t: bool(STRICT_ON.search(t))):
        for ln in net_removed(change, lambda t: bool(STRICT_ON.search(t)))[:1]:
            yield finding("type-or-lint-config-weakened", "Strict mode removed", change, ln)
    if off_rules:
        n = len(off_rules)
        yield finding("type-or-lint-config-weakened", "%d lint rule%s turned off" % (n, "" if n == 1 else "s"),
                      change, off_rules[0], severity=M)
    if loosened:
        yield finding("type-or-lint-config-weakened", "Files or rules excluded from checking", change, loosened[0], severity=M)
    for removed, added in change_blocks(change):
        r = next((LEVEL.match(l.text) for l in removed if LEVEL.match(l.text)), None)
        a = next(((LEVEL.match(l.text), l) for l in added if LEVEL.match(l.text)), None)
        if r and a and int(a[0].group(1)) < int(r.group(1)):
            yield finding("type-or-lint-config-weakened", "Analysis level lowered from %s to %s" % (r.group(1), a[0].group(1)),
                          change, a[1])


# --------------------------------------------------------------------------- CI

CI_HIGH = re.compile(
    r"""(?x)
    \bcontinue-on-error\s*:\s*true\b
  | ^\s*if\s*:\s*(?:false|\$\{\{\s*false\s*\}\}|["']false["'])\s*$
  | \ballow_failure\s*:\s*true\b
  | \|\|\s*(?:true|exit\s+0|:)\s*(?:["']\s*)?$ | \|\|\s*true\b
  | ;\s*exit\s+0\s*(?:["']\s*)?$
  | --no-verify\b
  | -DskipTests\b | -Dmaven\.test\.skip=true | --exclude-task\s+test\b
  | \bskip[-_]?tests?\s*[:=]\s*true\b
    """
)


def check_ci(change: FileChange, ctx: Context) -> Iterable[Finding]:
    if "ci" not in change.info.config:
        return
    if change.status == "D":
        if change.old_text is None or CHECK_COMMAND.search(change.old_text):
            yield finding("ci-weakened", "CI workflow deleted", change, snippet="- " + change.path)
        return
    for ln in _added_code(change):
        m = CI_HIGH.search(ln.text)
        if m:
            yield finding("ci-weakened", "CI step can no longer fail (`%s`)" % m.group(0).strip()[:50], change, ln)
        elif re.search(r"\bset\s+\+e\b", ln.text):
            yield finding("ci-weakened", "Errors ignored in CI script (`set +e`)", change, ln, severity=M)
    gone = net_removed(change, lambda t: bool(CHECK_COMMAND.search(t)) and not is_comment_line(t, change.info.lang))
    if gone:
        readded = {re.sub(r"\s+", " ", m.group(0)) for ln in change.added() for m in CHECK_COMMAND.finditer(ln.text)}
        really_gone = [ln for ln in gone if re.sub(r"\s+", " ", CHECK_COMMAND.search(ln.text).group(0)) not in readded]
        if really_gone:
            n = len(really_gone)
            yield finding("ci-weakened", "%d CI check step%s removed" % (n, "" if n == 1 else "s"), change, really_gone[0])


# --------------------------------------------------------------------------- git hooks

PRECOMMIT_ID = re.compile(r"^\s*-\s*id\s*:\s*['\"]?([\w.-]+)")


def check_git_hooks(change: FileChange, ctx: Context) -> Iterable[Finding]:
    path = change.path.lower()
    base = _base(change)
    if "/.husky/_/" in "/" + path or path.startswith(".husky/_/"):
        return
    if ".husky/" in "/" + path:
        if change.status == "D":
            yield finding("git-hooks-disabled", "Git hook deleted", change, snippet="- " + change.path)
            return
        body = [l for l in change.new_lines() if l.strip() and not l.lstrip().startswith("#")]
        old_body = [l for l in change.old_lines() if l.strip() and not l.lstrip().startswith("#")]
        if old_body and (not body or re.match(r"^\s*exit\s+0\s*$", body[0])):
            yield finding("git-hooks-disabled", "Git hook emptied", change, change.added()[0] if change.added() else None)
        return
    if base in (".pre-commit-config.yaml", ".pre-commit-config.yml", "lefthook.yml", ".lefthook.yml", "lefthook.yaml"):
        if change.status == "D":
            yield finding("git-hooks-disabled", "Hook configuration deleted", change, snippet="- " + change.path)
            return
        if base.startswith(".pre-commit"):
            removed = [PRECOMMIT_ID.match(l.text).group(1) for l in net_removed(change, lambda t: bool(PRECOMMIT_ID.match(t)))]
            added = {PRECOMMIT_ID.match(l.text).group(1) for l in change.added() if PRECOMMIT_ID.match(l.text)}
            gone = [r for r in removed if r not in added]
            if gone:
                yield finding("git-hooks-disabled", "Removed pre-commit hook%s: %s" % ("" if len(gone) == 1 else "s", ", ".join(gone[:5])),
                              change, net_removed(change, lambda t: bool(PRECOMMIT_ID.match(t)))[0], severity=M)


FILE_RULES = [check_test_config, check_package_json, check_task_runner, check_type_lint_config, check_ci, check_git_hooks]
SET_RULES: list = []
