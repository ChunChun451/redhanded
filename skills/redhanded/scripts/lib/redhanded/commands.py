"""Recognise verification commands (tests, builds, type checks, linters) and read how they ended."""

from __future__ import annotations

import posixpath
import re
import shlex
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

TEST, BUILD, TYPECHECK, LINT = "test", "build", "typecheck", "lint"
ALL_KINDS = (TEST, BUILD, TYPECHECK, LINT)
ANY_LANG = "*"

# --------------------------------------------------------------------------- splitting

HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
PS_HERESTRING = re.compile(r"@(['\"])\s*$")


def strip_heredocs(command: str) -> Tuple[str, List[str]]:
    """Remove here-document bodies (`<<EOF ... EOF`, PowerShell `@' ... '@`) so their lines are not read as commands.

    Returns the command without the bodies, and the bodies.
    """
    if "<<" not in command and "@'" not in command and '@"' not in command:
        return command, []
    lines = command.split("\n")
    out: List[str] = []
    bodies: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        ends = [m.group(2) for m in HEREDOC.finditer(line)]
        ps = PS_HERESTRING.search(line)
        if ps:
            ends.append(ps.group(1) + "@")
        for end in ends:
            body: List[str] = []
            while i < len(lines):
                cur = lines[i]
                if (cur.strip() == end) if not end.endswith("@") else cur.lstrip().startswith(end):
                    if end.endswith("@"):
                        out.append(cur.lstrip()[len(end):])  # the rest of the line after '@ is still a command
                    i += 1
                    break
                body.append(cur)
                i += 1
            bodies.append("\n".join(body))
    return "\n".join(out), bodies


def split_segments(command: str) -> List[Tuple[str, str]]:
    """Split a shell command line into (segment, operator-after) pairs, respecting quotes."""
    segs: List[Tuple[str, str]] = []
    cur: List[str] = []
    quote: Optional[str] = None
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if quote:
            cur.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < n:
                cur.append(command[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            cur.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            if command[i + 1] == "\n":
                i += 2
                continue
            cur.append(command[i:i + 2])
            i += 2
            continue
        op = None
        for cand in ("&&", "||", "|&", ";", "|", "\n", "&"):
            if command.startswith(cand, i):
                if cand == "&" and (command[i - 1:i] in (">", "<") or command[i + 1:i + 2] == ">"):
                    break  # redirection like 2>&1 or &>
                op = cand
                break
        if op:
            segs.append(("".join(cur).strip(), op))
            cur = []
            i += len(op)
            continue
        cur.append(ch)
        i += 1
    if "".join(cur).strip():
        segs.append(("".join(cur).strip(), ""))
    return [(s, op) for s, op in segs if s]


PATH_BACKSLASH = re.compile(r"(?<=[\w.~:-])\\(?=[\w.*?-])")


def tokens(segment: str) -> List[str]:
    # Windows paths (`tests\unit`, from PowerShell) would otherwise lose their separators to shell escaping.
    segment = PATH_BACKSLASH.sub("/", segment)
    try:
        toks = shlex.split(segment, posix=True)
    except ValueError:
        toks = segment.split()
    out = []
    for t in toks:
        if re.match(r"^\d*[<>]", t) or t in ("2>&1", "&>", ">", ">>", "<"):
            break  # redirections end the argument list we care about
        out.append(t)
    return out


ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
WRAPPERS = {
    "time", "nice", "nohup", "env", "sudo", "command", "exec", "xvfb-run", "stdbuf", "caffeinate", "chronic",
    "npx", "pnpx", "bunx", "uvx", "pipx", "dotenv", "cross-env", "with-contenv", "unbuffer",
}
TWO_WORD_WRAPPERS = {
    ("uv", "run"), ("poetry", "run"), ("pipenv", "run"), ("pdm", "run"), ("hatch", "run"), ("rye", "run"),
    ("bundle", "exec"), ("pnpm", "exec"), ("pnpm", "dlx"), ("yarn", "dlx"), ("npm", "exec"), ("conda", "run"),
    ("mamba", "run"), ("pixi", "run"), ("dotnet", "tool"), ("go", "run"),
}
VALUE_FLAGS = {"--with", "-p", "--package", "--python", "--env-file", "-n", "--name", "-e", "--project", "-k"}
VALUE_FLAGS_TIMEOUT = {"-s", "--signal", "-k", "--kill-after"}


def strip_wrappers(toks: List[str]) -> Tuple[List[str], List[str]]:
    """Remove env assignments and wrapper commands. Returns (core tokens, env assignments)."""
    env: List[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if ENV_ASSIGN.match(t):
            env.append(t)
            i += 1
            continue
        if t in ("(", "{", "!"):
            i += 1
            continue
        base = _exe(t)
        if base == "timeout" or base == "gtimeout":
            i += 1
            while i < len(toks) and toks[i].startswith("-"):
                i += 2 if toks[i] in VALUE_FLAGS_TIMEOUT else 1
            i += 1  # the duration
            continue
        if base in WRAPPERS:
            i += 1
            while i < len(toks) and toks[i].startswith("-"):
                i += 2 if toks[i] in VALUE_FLAGS and base not in ("env",) else 1
            if base == "dotenv" and i < len(toks) and toks[i] == "--":
                i += 1
            continue
        if i + 1 < len(toks) and (base, toks[i + 1]) in TWO_WORD_WRAPPERS:
            i += 2
            while i < len(toks) and toks[i].startswith("-"):
                i += 2 if toks[i] in VALUE_FLAGS else 1
            continue
        break
    return toks[i:], env


def _exe(token: str) -> str:
    base = posixpath.basename(token.replace("\\", "/")).lower()
    return re.sub(r"\.(?:exe|cmd|bat|ps1|sh)$", "", base)


# --------------------------------------------------------------------------- classification

SCRIPT_KINDS = [
    (re.compile(r"^(?:test|tests|spec|specs|e2e|unit|integration|it|jest|vitest|mocha|pytest|cypress|playwright)(?:[:._-].*)?$"), {TEST}),
    (re.compile(r"^(?:typecheck|type-check|types|tsc|check-types|check:types|typecheck:.*|mypy|pyright)$"), {TYPECHECK}),
    (re.compile(r"^(?:lint|lint:.*|eslint|stylelint|format:check|fmt:check|prettier:check|ruff|clippy)$"), {LINT}),
    (re.compile(r"^(?:build|build:.*|compile|bundle|dist|package)$"), {BUILD}),
    (re.compile(r"^(?:check|checks|verify|validate|ci|precommit|pre-commit|all|qa|quality)(?::.*)?$"), set(ALL_KINDS)),
]
NO_RUN_FLAGS = {"--version", "-v", "-V", "--help", "-h", "--collect-only", "--co", "--listTests", "--list-tests",
                "--init", "--showConfig", "--show-config", "--no-run", "--list", "--print-config"}

DIRECT = {
    "pytest": {TEST}, "py.test": {TEST}, "tox": {TEST}, "nox": {TEST}, "jest": {TEST}, "vitest": {TEST},
    "mocha": {TEST}, "ava": {TEST}, "tap": {TEST}, "karma": {TEST}, "jasmine": {TEST}, "rspec": {TEST},
    "phpunit": {TEST}, "pest": {TEST}, "ctest": {TEST}, "minitest": {TEST}, "behave": {TEST}, "ward": {TEST},
    "tsc": {TYPECHECK, BUILD}, "vue-tsc": {TYPECHECK}, "svelte-check": {TYPECHECK}, "mypy": {TYPECHECK},
    "dmypy": {TYPECHECK}, "pyright": {TYPECHECK}, "basedpyright": {TYPECHECK}, "flow": {TYPECHECK},
    "eslint": {LINT}, "oxlint": {LINT}, "stylelint": {LINT}, "flake8": {LINT}, "pylint": {LINT},
    "golangci-lint": {LINT}, "rubocop": {LINT}, "standardrb": {LINT}, "phpstan": {LINT, TYPECHECK},
    "psalm": {LINT, TYPECHECK}, "swiftlint": {LINT}, "ktlint": {LINT}, "detekt": {LINT}, "shellcheck": {LINT},
    "hadolint": {LINT}, "xo": {LINT}, "standard": {LINT},
}


@dataclass
class Command:
    raw: str
    kinds: Set[str] = field(default_factory=set)
    masked: bool = False  # exit status hidden (`|| true`, `; exit 0`, piped into another program)
    exclusions: List[str] = field(default_factory=list)  # flags that drop tests from the run
    subset: bool = False  # only some tests (a path or name filter)
    bypass: List[str] = field(default_factory=list)  # hook or check bypasses
    deletes: List[str] = field(default_factory=list)  # paths removed with rm / git rm / del
    writes: List[str] = field(default_factory=list)  # paths created or overwritten (`>`, cp, mv, touch, tee)
    langs: Set[str] = field(default_factory=set)  # languages the checks exercise; ANY_LANG when it could be anything

    @property
    def is_verification(self) -> bool:
        return bool(self.kinds)


def _script_kinds(name: str) -> Set[str]:
    name = name.lower()
    for pattern, kinds in SCRIPT_KINDS:
        if pattern.match(name):
            return set(kinds)
    return set()


def _runner_script(args: List[str]) -> Optional[str]:
    """Script name for `npm run x`, `yarn x`, `pnpm --filter a test`, `make x` style invocations."""
    rest = [a for a in args if not a.startswith("-")]
    if not rest:
        return None
    if rest[0] in ("run", "run-script", "exec") and len(rest) > 1:
        return rest[1]
    return rest[0]


SCRIPT_EXT = re.compile(r"\.(?:sh|bash|zsh|ps1|bat|cmd|exe|py|js|mjs|cjs|ts|rb|pl)$", re.I)
TEST_DIRS = {"test", "tests", "spec", "specs", "__tests__"}
COMPILERS = {
    "gcc", "g++", "cc", "c++", "clang", "clang++", "clang-cl", "cl", "javac", "kotlinc", "scalac", "rustc", "swiftc",
    "ghc", "nvcc", "csc", "gfortran", "dmd", "ldc2", "tcc", "emcc", "em++",
}
BUNDLERS = {"webpack", "rollup", "esbuild", "tsup", "tsdown", "rspack", "rolldown", "unbuild"}
FRAMEWORK_CLIS = {"vite", "next", "nuxt", "nuxi", "astro", "ng", "react-scripts", "vue-cli-service", "remix", "gatsby",
                  "parcel"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
SOURCE_FILE = re.compile(r"\.(?:c|cc|cpp|cxx|c\+\+|m|mm|cu|java|kt|kts|scala|rs|swift|hs|f|f90|f95|d|cs)$", re.I)

# The language (as `classify.detect_lang` names it) each tool checks; tools not listed could check anything.
EXE_LANG: dict = {}
for _lang, _exes in (
    ("py", ("pytest", "py.test", "tox", "nox", "unittest", "behave", "ward", "mypy", "dmypy", "pyright", "basedpyright",
            "flake8", "pylint", "ruff", "black", "isort", "ty", "hatch")),
    ("js", ("jest", "vitest", "mocha", "ava", "tap", "karma", "jasmine", "tsc", "vue-tsc", "svelte-check", "flow",
            "eslint", "oxlint", "xo", "standard", "biome", "prettier", "playwright", "cypress", "npm", "pnpm", "yarn",
            "bun", "deno", "tsx", "ts-node", "stylelint", *sorted(BUNDLERS), *sorted(FRAMEWORK_CLIS))),
    ("go", ("go", "golangci-lint", "gofmt")),
    ("rust", ("cargo", "rustc", "rustfmt")),
    ("java", ("mvn", "mvnw", "gradle", "gradlew", "javac", "kotlinc", "ktlint", "detekt", "ant")),
    ("scala", ("sbt", "scalac")),
    ("csharp", ("dotnet", "csc")),
    ("ruby", ("rspec", "rake", "rails", "minitest", "rubocop", "standardrb")),
    ("php", ("phpunit", "pest", "composer", "phpstan", "psalm")),
    ("swift", ("swift", "swiftc", "swiftlint")),
    ("dart", ("flutter", "dart")),
    ("elixir", ("mix",)),
    ("cpp", ("gcc", "g++", "cc", "c++", "clang", "clang++", "clang-cl", "cl", "nvcc", "emcc", "em++", "tcc", "cmake",
             "ctest", "ninja", "meson", "scons")),
    ("shell", ("shellcheck", "bats")),
    ("other-code", ("ghc", "gfortran", "dmd", "ldc2", "lein", "stack", "cabal", "zig")),
):
    for _exe_name in _exes:
        EXE_LANG[_exe_name] = _lang
EXE_LANGS = {"mvn": {"java", "scala"}, "gradle": {"java", "scala"}, "gradlew": {"java", "scala"},
             "sbt": {"scala", "java"}, "msbuild": {"csharp", "cpp"}, "xcodebuild": {"swift", "cpp"}}


SINGLE_TEST_FILE = re.compile(r"^(?!run[_-])(?:test_.+|.+_test|.+\.(?:test|spec))$")


def _is_python(exe: str) -> bool:
    return exe in ("python", "python3", "py", "pypy3") or bool(re.match(r"^python3?\.\d+$", exe))


def _looks_like_path(token: str) -> bool:
    return "/" in token or "\\" in token or bool(SCRIPT_EXT.search(token))


def _script_name(token: str) -> str:
    return SCRIPT_EXT.sub("", posixpath.basename(token.replace("\\", "/")).lower())


def _check_script(token: str) -> Set[str]:
    """Kinds for a project script named by path: `./scripts/check`, `bin/test`, `run_tests.sh`, `tests/run.sh`."""
    parts = [p for p in token.replace("\\", "/").lower().split("/") if p not in ("", ".")]
    if not parts:
        return set()
    name = _script_name(parts[-1])
    kinds = _script_kinds(re.sub(r"^run[_-]?(?=.)", "", name))
    if not kinds and re.match(r"^(?:check|checks|verify|validate)[_-]all$", name):
        kinds = set(ALL_KINDS)
    if not kinds and len(parts) > 1 and parts[-2] in TEST_DIRS and name in ("run", "all", "run_all", "run-all", "runner",
                                                                             "main", "index"):
        kinds = {TEST}
    return kinds


def _inline_command(core: List[str]) -> Optional[str]:
    """The command string in `bash -c "..."`, `pwsh -Command ...` or `cmd /c ...`, if that is what this is."""
    if not core:
        return None
    exe, args = _exe(core[0]), core[1:]
    if exe in SHELLS:
        j = 0
        while j < len(args) and args[j].startswith(("-", "+")):
            if re.match(r"^-[a-zA-Z]*c[a-zA-Z]*$", args[j]):
                return args[j + 1] if j + 1 < len(args) else ""
            j += 2 if args[j] in ("-o", "+o", "-O", "+O") else 1
        return None
    if exe in ("pwsh", "powershell"):
        low = [a.lower() for a in args]
        for j, a in enumerate(low[:-1]):
            if a in ("-command", "-c"):
                return " ".join(args[j + 1:])
        return None
    if exe == "cmd" and args[:1] and args[0].lower() in ("/c", "/k"):
        return " ".join(args[1:])
    return None


def runner_langs(core: List[str]) -> Set[str]:
    """Languages a verification command exercises: {"py"} for pytest, {ANY_LANG} for `make` or a project script."""
    if not core:
        return {ANY_LANG}
    exe, args = _exe(core[0]), core[1:]
    if _is_python(exe):
        if "-m" in args and args.index("-m") + 1 < len(args):
            return runner_langs(args[args.index("-m") + 1:])
        script = next((a for a in args if not a.startswith("-")), "")
        return {"py"} if _script_name(script) in ("manage", "setup") else {ANY_LANG}
    if exe == "node":
        script = next((a for a in args if not a.startswith("-")), "")
        if "--test" in args or "--check" in args or "-c" in args or _exe(script) in EXE_LANG:
            return {"js"}
        return {ANY_LANG}
    if exe in SHELLS:
        inline = _inline_command(core)
        if inline is not None:
            return parse_command(inline).langs or {ANY_LANG}
        script = _shell_script(core)
        return {"shell"} if script and "-n" in args[:args.index(script)] else {ANY_LANG}
    if exe in ("pwsh", "powershell", "cmd"):
        inline = _inline_command(core)
        return (parse_command(inline).langs or {ANY_LANG}) if inline is not None else {ANY_LANG}
    if exe in EXE_LANGS:
        return set(EXE_LANGS[exe])
    if exe in EXE_LANG:
        return {EXE_LANG[exe]}
    return {ANY_LANG}


def _shell_script(core: List[str]) -> str:
    """The script in `bash scripts/test.sh` or `pwsh -File scripts/test.ps1`."""
    exe, args = _exe(core[0]), core[1:]
    if exe in SHELLS:
        j = 0
        while j < len(args) and args[j].startswith(("-", "+")):
            j += 2 if args[j] in ("-o", "+o", "-O", "+O") else 1
        return args[j] if j < len(args) else ""
    if exe in ("pwsh", "powershell"):
        low = [a.lower() for a in args]
        for j, a in enumerate(low[:-1]):
            if a in ("-file", "-f"):
                return args[j + 1]
        return next((a for a in args if a.lower().endswith(".ps1")), "")
    return ""


def classify_tokens(core: List[str]) -> Set[str]:
    if not core:
        return set()
    exe = _exe(core[0])
    args = core[1:]
    if any(a in NO_RUN_FLAGS for a in args):
        return set()
    if exe in ("python", "python3", "py", "pypy3") or re.match(r"^python3?\.\d+$", exe):
        if "-m" in args:
            idx = args.index("-m")
            if idx + 1 < len(args):
                return classify_tokens(args[idx + 1:])
        if args and args[0] == "-3" and len(args) > 2 and args[1] == "-m":
            return classify_tokens(args[2:])
        if "-c" in args:
            return set()
        script = next((a for a in args if not a.startswith("-")), "")
        if _exe(script) == "manage" or script.endswith("manage.py"):
            return {TEST} if "test" in args else set()
        if script.endswith("setup.py") and "test" in args:
            return {TEST}
        return _check_script(script) if script else set()
    if exe == "unittest":
        return {TEST}
    if exe in DIRECT:
        if exe == "vitest" and args[:1] == ["typecheck"]:
            return {TYPECHECK}
        return set(DIRECT[exe])
    if exe in ("npm", "pnpm", "yarn", "bun", "deno"):
        if exe == "bun" and args[:1] == ["test"]:
            return {TEST}
        if exe == "deno" and args[:1] in (["test"], ["check"], ["lint"]):
            return {TEST} if args[0] == "test" else ({TYPECHECK} if args[0] == "check" else {LINT})
        if exe == "npm" and args[:1] in (["t"], ["tst"]):
            return {TEST}
        if exe in ("npm", "pnpm") and args[:1] in (["install"], ["i"], ["ci"], ["add"], ["view"], ["ls"]):
            return set()
        script = _runner_script(args)
        if not script:
            return set()
        return _script_kinds(script) or (_check_script(script) if _looks_like_path(script) else set())
    if exe in ("make", "gmake", "just", "task", "mage", "rake", "invoke", "inv", "doit"):
        script = _runner_script(args)
        if script is None:
            return {BUILD} if exe in ("make", "gmake") else set()
        return _script_kinds(script) or ({TEST} if exe == "rake" and script in ("spec", "test") else set())
    if exe in ("nx", "turbo", "lerna", "moon", "mise", "rush", "wireit"):
        joined = " ".join(a for a in args if not a.startswith("-"))
        for word in re.split(r"[\s:]+", joined):
            k = _script_kinds(word)
            if k:
                return k
        return set()
    if exe == "node":
        if "--test" in args:
            return {TEST}
        if "--check" in args or "-c" in args:
            return {LINT}  # syntax check
        script = next((a for a in args if not a.startswith("-")), "")
        base = _exe(script)
        if base in DIRECT:
            return set(DIRECT[base])
        if base in ("tsc",) or script.endswith("typescript/bin/tsc"):
            return {TYPECHECK, BUILD}
        if any(a in ("-e", "--eval", "-p", "--print") for a in args):
            return set()
        return _check_script(script) if script else set()
    if exe in ("tsx", "ts-node") and "--test" in args:
        return {TEST}
    if exe == "go":
        sub = args[:1]
        if sub == ["test"]:
            return {TEST, BUILD}
        if sub in (["build"], ["install"]):
            return {BUILD}
        if sub == ["vet"]:
            return {LINT, TYPECHECK}
        return set()
    if exe == "cargo":
        sub = next((a for a in args if not a.startswith("-") and not a.startswith("+")), "")
        if sub in ("test", "nextest"):
            return {TEST, BUILD}
        if sub == "build":
            return {BUILD}
        if sub == "check":
            return {TYPECHECK, BUILD}
        if sub == "clippy":
            return {LINT, TYPECHECK}
        if sub == "fmt" and "--check" in args:
            return {LINT}
        return set()
    if exe in ("mvn", "mvnw", "./mvnw"):
        goals = [a for a in args if not a.startswith("-")]
        if any(g in ("test", "verify", "install", "package", "integration-test") for g in goals):
            return {TEST, BUILD}
        if any(g in ("compile", "test-compile") for g in goals):
            return {BUILD}
        return set()
    if exe in ("gradle", "gradlew"):
        tasks = [a.split(":")[-1] for a in args if not a.startswith("-")]
        kinds: Set[str] = set()
        if any(t in ("test", "check", "build", "connectedCheck", "integrationTest") for t in tasks):
            kinds |= {TEST, BUILD}
        if any(t in ("assemble", "compileJava", "compileKotlin", "classes") for t in tasks):
            kinds.add(BUILD)
        if any(t in ("lint", "ktlintCheck", "detekt", "spotlessCheck") for t in tasks):
            kinds.add(LINT)
        return kinds
    if exe == "dotnet":
        sub = args[:1]
        return {TEST, BUILD} if sub == ["test"] else {BUILD} if sub == ["build"] else set()
    if exe == "swift":
        sub = args[:1]
        return {TEST, BUILD} if sub == ["test"] else {BUILD} if sub == ["build"] else set()
    if exe in ("mix", "sbt", "lein", "stack", "cabal", "flutter", "dart", "zig", "bazel", "bazelisk", "buck2", "pants"):
        if "test" in args[:2] or "tests" in args[:2]:
            return {TEST}
        if args[:1] in (["build"], ["compile"]):
            return {BUILD}
        if exe in ("dart", "flutter") and args[:1] == ["analyze"]:
            return {LINT, TYPECHECK}
        return set()
    if exe in ("rails", "bin/rails") and args[:1] == ["test"]:
        return {TEST}
    if exe in ("ruff",):
        return {LINT} if (not args or args[0] in ("check", ".") or args[0].startswith("-") or args[0] == "format" and "--check" in args) else set()
    if exe in ("black", "isort", "prettier", "gofmt", "rustfmt"):
        return {LINT} if any(a in ("--check", "-c", "-l", "--list-different", "--diff") for a in args) else set()
    if exe == "biome":
        return {LINT} if args[:1] in (["check"], ["lint"], ["ci"]) else set()
    if exe == "ty" and args[:1] == ["check"]:
        return {TYPECHECK}
    if exe == "hatch" and args[:1] == ["test"]:
        return {TEST}
    if exe in ("playwright", "cypress") and args[:1] in (["test"], ["run"]):
        return {TEST}
    if exe in ("composer",) and args[:1] in (["test"], ["run-script"]):
        return {TEST} if "test" in args[:2] else set()
    if exe in COMPILERS:
        return {BUILD} if any(SOURCE_FILE.search(a) for a in args) else set()  # not `clang++ -dumpversion`
    words = [a for a in args if not a.startswith("-")]
    if exe == "cmake":
        if "--build" not in args:
            return set()  # configuring is not building
        return {TEST, BUILD} if any(w in ("test", "tests", "check") for w in words) else {BUILD}
    if exe in ("ninja", "scons", "ant", "msbuild", "xcodebuild"):
        return {TEST, BUILD} if any(w in ("test", "tests", "check") for w in words) else {BUILD}
    if exe == "meson":
        return {TEST} if words[:1] == ["test"] else {BUILD} if words[:1] == ["compile"] else set()
    if exe in BUNDLERS:
        return set() if any(a in ("--watch", "-w", "serve") for a in args) else {BUILD}
    if exe in FRAMEWORK_CLIS:
        sub = words[0] if words else ""
        if exe == "astro" and sub == "check":
            return {TYPECHECK}
        return _script_kinds(sub) if re.match(r"^(?:build|test|lint)(?::|$)", sub) else set()
    if exe in ("pre-commit", "prek", "lefthook") and words[:1] == ["run"]:
        return {LINT}
    if exe in SHELLS or exe in ("pwsh", "powershell", "cmd"):
        inline = _inline_command(core)
        if inline is not None:
            return parse_command(inline).kinds
        script = _shell_script(core)
        if script and exe in SHELLS and "-n" in args[:args.index(script)]:
            return {LINT}  # `bash -n script.sh` checks the syntax without running it
        return _check_script(script) if script else set()
    if _looks_like_path(core[0]):
        return _check_script(core[0])
    return set()


EXCLUSION_FLAGS = re.compile(
    r"""(?x)
    (?:^|\s)--deselect(?:[=\s]\S+) | (?:^|\s)-k\s+["']?not\b[^"']*["']? | (?:^|\s)--ignore(?:-glob)?[=\s]\S+
  | (?:^|\s)-p\s+no:(?:pytest_)?(?:cov|mypy|flake8|ruff|pylint|typeguard|black|isort)\b
  | (?:^|\s)--testPathIgnorePatterns[=\s]\S+ | (?:^|\s)--exclude[=\s]\S+
  | (?:^|\s)-skip[=\s]\S+ | (?:^|\s)--skip[=\s]\S+ | -DskipTests\b | -Dmaven\.test\.skip=true
  | (?:^|\s)-x\s+test\b | --exclude-task\s+test\b | --passWithNoTests\b | --pass-with-no-tests\b
    """
)
SUBSET_FLAGS = re.compile(
    r"""(?x)
    (?:^|\s)(?:-k|-t|--testNamePattern|--grep|-g|-run|--run|-m|--filter|--tests|--test|-Dtest)(?:[=\s])
  | (?:^|\s)--(?:lf|last-failed|ff|onlyChanged|findRelatedTests|changed|related)\b | (?:^|\s)-o\b
  | ::\w | \w+\.(?:py|[cm]?[jt]sx?|rb|go|rs|php|java|kt|cs|ex|exs|dart|swift)(?=\s|$|:)
    """
)
HOOK_BYPASS_ENV = re.compile(r"^(?:HUSKY=0|HUSKY_SKIP_HOOKS=\S+|SKIP_HOOKS=\S+|SKIP=\S+|LEFTHOOK=0|"
                             r"PRE_COMMIT_ALLOW_NO_CONFIG=\S+|GIT_HOOKS_DISABLED=\S+|SKIP_SIMPLE_GIT_HOOKS=\S+)$")
MASK_OPS = {"||"}


def _git_bypass(core: List[str]) -> List[str]:
    out: List[str] = []
    if not core or _exe(core[0]) != "git":
        return out
    args = core[1:]
    j = 0
    while j < len(args) and args[j].startswith("-"):
        if args[j] == "-c" and j + 1 < len(args):
            if re.match(r"(?i)core\.hookspath=", args[j + 1]):
                out.append("git -c " + args[j + 1])
            j += 2
            continue
        j += 1
    if j >= len(args):
        return out
    sub, rest = args[j], args[j + 1:]
    if sub in ("commit", "push", "merge", "rebase", "am", "cherry-pick", "revert", "pull") and "--no-verify" in rest:
        out.append("git %s --no-verify" % sub)
    elif sub == "commit":
        skip_next = False
        for a in rest:
            if skip_next:
                skip_next = False
                continue
            if re.match(r"^-[a-zA-Z]+$", a):
                flags = a[1:]
                if "n" in flags.split("m")[0].split("F")[0].split("C")[0]:
                    out.append("git commit -n (skips hooks)")
                    break
                if flags[-1:] in ("m", "F", "C", "c", "t"):
                    skip_next = True
    if sub == "config" and any(re.match(r"(?i)^core\.hookspath$", a) for a in rest):
        out.append("git config core.hooksPath")
    return out


def _runner_args(core: List[str]) -> List[str]:
    """Arguments of the actual runner, past `python -m pytest`, `npm run test --`, and similar."""
    args = core[1:]
    exe = _exe(core[0]) if core else ""
    if _is_python(exe) and "-m" in args:
        args = args[args.index("-m") + 2:]
    elif exe in ("npm", "pnpm", "yarn", "bun") and "--" in args:
        args = args[args.index("--") + 1:]
    elif exe in ("npm", "pnpm", "yarn", "bun"):
        rest = [a for a in args if not a.startswith("-")]
        args = rest[2:] if rest[:1] in (["run"], ["run-script"]) else rest[1:]
    elif _is_python(exe) or exe == "node" or exe in SHELLS:
        # `python run_tests.py` names a runner, not a test file; `python tests/test_api.py` runs one file.
        idx = next((j for j, a in enumerate(args) if not a.startswith("-")), None)
        if idx is not None and not SINGLE_TEST_FILE.match(_script_name(args[idx])):
            args = args[idx + 1:]
    return args


def _deletes(core: List[str]) -> List[str]:
    if not core:
        return []
    exe = _exe(core[0])
    args = core[1:]
    if exe == "git" and args[:1] == ["rm"]:
        args = args[1:]
    elif exe in ("rm", "unlink", "del", "erase", "remove-item", "rmdir", "trash"):
        pass
    else:
        return []
    return [a for a in args if not a.startswith("-") and a not in ("--",)]


REDIRECT_TARGET = re.compile(r"""(?:^|[\s;(])(?:\d?>>?|&>>?)\s*(?!&)("[^"]+"|'[^']+'|[^\s;&|<>()]+)""")


QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")


def _writes(core: List[str], segment: str) -> List[str]:
    unquoted = QUOTED.sub(lambda m: m.group(0).replace(">", " "), segment)  # `echo 'a > b'` writes nothing
    out = [m.group(1).strip("\"'") for m in REDIRECT_TARGET.finditer(unquoted)]
    if not core:
        return out
    exe = _exe(core[0])
    args = [a for a in core[1:] if not a.startswith("-")]
    if exe in ("cp", "mv", "install", "ln", "copy", "move") and len(args) >= 2:
        dest = args[-1]
        out.append(dest)
        out.extend(dest.rstrip("/") + "/" + posixpath.basename(src.replace("\\", "/")) for src in args[:-1])
    elif exe in ("touch", "tee"):
        out.extend(args)
    return out


def parse_command(raw: str) -> Command:
    cmd = Command(raw=raw)
    segs = split_segments(strip_heredocs(raw)[0])
    for idx, (seg, op) in enumerate(segs):
        toks = tokens(seg)
        core, env = strip_wrappers(toks)
        for e in env:
            if HOOK_BYPASS_ENV.match(e):
                cmd.bypass.append(e)
        inline = _inline_command(core)
        if inline:  # `bash -c "..."`, `cmd /c ...`: read the inner command as if it ran here
            inner = parse_command(inline)
            cmd.bypass.extend(inner.bypass)
            cmd.deletes.extend(inner.deletes)
            cmd.writes.extend(inner.writes)
            if not inner.kinds:
                continue
            cmd.kinds |= inner.kinds
            cmd.langs |= inner.langs
            cmd.exclusions.extend(inner.exclusions)
            cmd.subset = cmd.subset or inner.subset
            cmd.masked = cmd.masked or inner.masked
        else:
            cmd.bypass.extend(_git_bypass(core))
            if core and _exe(core[0]) in ("chmod",) and any(".git/hooks" in a or ".husky" in a for a in core[1:]) \
                    and any(re.match(r"^[ugoa]*-[rwx]*x", a) for a in core[1:]):
                cmd.bypass.append("chmod -x on git hooks")
            cmd.writes.extend(_writes(core, seg))
            dels = _deletes(core)
            cmd.deletes.extend(dels)
            if any(d.rstrip("/").endswith((".husky", ".git/hooks")) or "/.git/hooks/" in d for d in dels):
                cmd.bypass.append("deleted git hooks")
            kinds = classify_tokens(core)
            if not kinds:
                continue
            cmd.kinds |= kinds
            cmd.langs |= runner_langs(core)
            joined = " " + " ".join(_runner_args(core))
            cmd.exclusions.extend(m.group(0).strip() for m in EXCLUSION_FLAGS.finditer(joined))
            if SUBSET_FLAGS.search(joined):
                cmd.subset = True
        if op in ("|", "|&"):
            cmd.masked = True
        elif op in MASK_OPS:
            nxt = segs[idx + 1][0] if idx + 1 < len(segs) else ""
            if re.match(r"^(?:true|:|exit\s+0|echo\b|printf\b)", nxt.strip()):
                cmd.masked = True
        if op == ";" and idx + 1 < len(segs) and re.match(r"^exit\s+0\b", segs[idx + 1][0]):
            cmd.masked = True
    return cmd


# --------------------------------------------------------------------------- outcomes

FAIL_MARKERS = [
    re.compile(r"(?m)^=+ .*\b[1-9]\d* (?:failed|errors?)\b.* in [\d.]+s"),  # pytest summary
    re.compile(r"(?m)^FAILED [\w/.\\-]+(?:::\S+)?"),  # pytest short summary
    re.compile(r"(?m)^ERROR (?:collecting )?[\w/.\\-]+\.py\b"),
    re.compile(r"(?m)^Tests?:\s+.*\b[1-9]\d* failed\b"),  # jest
    re.compile(r"(?m)^Test Suites:\s+.*\b[1-9]\d* failed\b"),
    re.compile(r"(?m)^\s*Test Files\s+.*\b[1-9]\d* failed\b"),  # vitest
    re.compile(r"(?m)^\s*Tests\s+.*\b[1-9]\d* failed\b"),
    re.compile(r"(?m)^\s*[1-9]\d* failing\b"),  # mocha
    re.compile(r"(?m)^(?:--- )?FAIL\b"),  # go
    re.compile(r"(?m)^panic: "),
    re.compile(r"test result: FAILED"),  # cargo
    re.compile(r"(?m)^error(?:\[E\d+\])?: (?:could not compile|aborting due to)"),
    re.compile(r"(?m)error TS\d{4}:"),  # tsc
    re.compile(r"(?m)^Found [1-9]\d* errors? in \d+ files?"),  # mypy / tsc
    re.compile(r"(?m)^Found [1-9]\d* errors?\.?$"),  # ruff
    re.compile(r"✖ [1-9]\d* problems? \([1-9]\d* errors?"),  # eslint
    re.compile(r"(?m)^[1-9]\d* problems? \([1-9]\d* errors?"),
    re.compile(r"\b\d+ examples?, [1-9]\d* failures?"),  # rspec
    re.compile(r"\b\d+ runs?, \d+ assertions?, (?:[1-9]\d* failures?|\d+ failures?, [1-9]\d* errors?)"),  # minitest
    re.compile(r"(?m)^(?:FAILURES|ERRORS)!$"),  # phpunit
    re.compile(r"(?m)^FAILED \((?:failures|errors)=\d+"),  # unittest
    re.compile(r"(?m)^Failed!\s+-\s+Failed:\s+[1-9]"),  # dotnet
    re.compile(r"BUILD FAILURE|BUILD FAILED|Build FAILED\."),
    re.compile(r"(?m)^npm (?:ERR!|error) (?:code ELIFECYCLE|Lifecycle script|Test failed|command failed)"),
    re.compile(r"(?m)^error Command failed with exit code [1-9]"),  # yarn
    re.compile(r"ERR_PNPM_RECURSIVE_RUN_FIRST_FAIL|ELIFECYCLE\s+Command failed"),
    re.compile(r"(?m)^make(?:\[\d+\])?: \*\*\* .*Error \d+"),
    re.compile(r"(?m)^\[FAIL\]|^\s*✗ \d+ (?:test|spec)s? failed"),
]
PASS_MARKERS = [
    re.compile(r"(?m)^=+ (?:\d+ (?:passed|skipped|xfailed|xpassed|deselected|warnings?)(?:, )?)+ in [\d.]+s"),
    re.compile(r"(?m)^Tests:\s+(?:\d+ (?:skipped|todo), )*\d+ passed, \d+ total"),
    re.compile(r"(?m)^\s*Tests\s+\d+ passed(?: \| \d+ (?:skipped|todo))*\s*(?:\(\d+\))?$"),
    re.compile(r"(?m)^\s*Test Files\s+\d+ passed"),
    re.compile(r"(?m)^\s*\d+ passing\b"),
    re.compile(r"(?m)^ok\s+\S+\s+[\d.]+s"),
    re.compile(r"test result: ok\."),
    re.compile(r"Success: no issues found"),
    re.compile(r"All checks passed!"),
    re.compile(r"\b\d+ examples?, 0 failures"),
    re.compile(r"\b\d+ runs?, \d+ assertions?, 0 failures, 0 errors"),
    re.compile(r"(?m)^OK(?: \(\d+ tests?)?"),
    re.compile(r"(?m)^Passed!\s+-\s+Failed:\s+0"),
    re.compile(r"BUILD SUCCESS(?:FUL)?\b"),
    re.compile(r"(?m)^Build succeeded\."),
    re.compile(r"(?m)^\s*Compiled successfully|^\s*✓ Compiled|^Finished `\w+` profile"),
    re.compile(r"(?m)^\s*0 errors?(?:,| and) 0 warnings?"),
    re.compile(r"(?m)^\s*✓ \d+ (?:tests?|specs?) (?:passed|completed)"),
]
NOT_FOUND = re.compile(r"(?i)command not found|is not recognized as an internal or external command|"
                       r"No such file or directory|ModuleNotFoundError: No module named '(?:pytest|mypy)'|"
                       r"Missing script: |npm ERR! missing script|error: no such command")
# The check never started (missing tool or script): it neither passed nor failed, so it is not a run at all.
NOT_RUN = "the command could not run"


@dataclass
class Outcome:
    status: str  # "pass" | "fail" | "unknown"
    reason: str = ""


def outcome(cmd: Command, output: str, is_error: bool = False, exit_code: Optional[int] = None,
            interrupted: bool = False) -> Outcome:
    tail = output[-20000:] if output else ""
    if interrupted:
        return Outcome("fail", "interrupted or timed out")
    for pattern in FAIL_MARKERS:
        m = pattern.search(tail)
        if m:
            return Outcome("fail", m.group(0).strip()[:120])
    if (NOT_FOUND.search(tail) and (is_error or (exit_code or 0) != 0)) or exit_code == 127:
        return Outcome("fail", NOT_RUN)
    code_known = exit_code is not None and not cmd.masked
    if code_known and exit_code != 0:
        return Outcome("fail", "exit code %d" % exit_code)
    for pattern in PASS_MARKERS:
        m = pattern.search(tail)
        if m:
            return Outcome("pass", m.group(0).strip()[:120])
    if code_known and exit_code == 0:
        return Outcome("pass", "exit code 0")
    return Outcome("unknown", "")


EXIT_PREFIX = re.compile(r"^\s*Exit code (-?\d+)")


def exit_code_from_result(text: str, is_error: bool) -> Optional[int]:
    m = EXIT_PREFIX.match(text or "")
    if m:
        return int(m.group(1))
    if is_error:
        if re.search(r"(?i)timed out|was interrupted|aborted", (text or "")[:300]):
            return None
        return None
    return 0
