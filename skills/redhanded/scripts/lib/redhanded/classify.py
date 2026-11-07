"""Classify files by language and role (test, snapshot, config, doc, generated)."""

from __future__ import annotations

import posixpath
import re
from typing import Iterable, List, Optional

from .model import FileInfo

EXT_LANG = {
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".tsx": "js", ".mts": "js",
    ".cts": "js", ".vue": "js", ".svelte": "js", ".astro": "js",
    ".py": "py", ".pyi": "py",
    ".go": "go",
    ".rs": "rust",
    ".java": "java", ".kt": "java", ".kts": "java", ".groovy": "java",
    ".scala": "scala", ".sc": "scala",
    ".cs": "csharp", ".fs": "csharp", ".vb": "csharp",
    ".rb": "ruby", ".rake": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".dart": "dart",
    ".ex": "elixir", ".exs": "elixir",
    ".c": "cpp", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".h": "cpp", ".hh": "cpp", ".hpp": "cpp",
    ".hxx": "cpp", ".m": "cpp", ".mm": "cpp",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".bats": "shell",
    ".lua": "lua",
    ".r": "other-code", ".jl": "other-code", ".zig": "other-code", ".hs": "other-code", ".ml": "other-code",
    ".clj": "other-code", ".erl": "other-code", ".nim": "other-code", ".ps1": "other-code",
    ".yml": "yaml", ".yaml": "yaml",
    ".json": "json", ".jsonc": "json", ".json5": "json",
    ".toml": "toml",
    ".ini": "ini", ".cfg": "ini", ".conf": "ini",
    ".xml": "xml", ".neon": "yaml",
    ".md": "markdown", ".mdx": "markdown", ".markdown": "markdown",
    ".rst": "doc", ".txt": "doc", ".adoc": "doc",
}

NAME_LANG = {
    "makefile": "make", "gnumakefile": "make", "justfile": "make", "jenkinsfile": "groovy-ci",
    "dockerfile": "docker", "rakefile": "ruby", "gemfile": "ruby", ".rspec": "ini",
}

TEST_DIRS = {"test", "tests", "__tests__", "__test__", "spec", "specs", "e2e", "integration-tests",
             "integration_tests", "unittests", "unit_tests", "testsuite"}
TEST_SUPPORT_DIRS = {"fixtures", "__fixtures__", "__mocks__", "testdata", "test-utils", "test_utils",
                     "testutil", "testutils", "testhelpers", "test-helpers", "mocks"}
SNAPSHOT_DIRS = {"__snapshots__", "snapshots", "__screenshots__", "__image_snapshots__"}

_TEST_NAME = re.compile(
    r"""(?x)(
        .+\.(?:test|spec|e2e|e2e-spec|cy|it|unit|integration|bench)\.[cm]?[jt]sx?$
      | .+\.(?:test|spec)\.(?:vue|svelte)$
      | ^test_.+\.py$ | .+_tests?\.py$ | ^tests?\.py$
      | .+_test\.go$
      | .+(?:Test|Tests|IT|ITCase|Spec)\.(?:java|kt|kts|groovy|scala)$ | ^Test.+\.(?:java|kt)$
      | .+(?:Test|Tests|Spec|Specs)\.(?:cs|fs|vb)$
      | .+_spec\.rb$ | .+_test\.rb$ | ^test_.+\.rb$
      | .+Test\.php$ | .+\.test\.php$
      | .+(?:Test|Tests|Spec)\.swift$
      | .+_test\.dart$
      | .+_test\.exs$
      | .+(?:_test|_tests|_unittest)\.(?:c|cc|cpp|cxx)$ | ^test_.+\.(?:c|cc|cpp|cxx)$
      | .+\.bats$
      | .+_(?:spec|test)\.lua$
      | ^tests\.rs$
      | .+_test\.ts$
    )""",
)
_TEST_SUPPORT_NAME = re.compile(
    r"""(?x)^(
        conftest\.py | (?:jest|vitest)\.setup\.[cm]?[jt]sx? | setup-?tests?\.[cm]?[jt]sx? | test-?setup\.[cm]?[jt]sx?
      | test[-_]?utils?\.[cm]?[jt]sx? | test[-_]?helpers?\.[cm]?[jt]sx? | spec_helper\.rb | rails_helper\.rb
      | test_helper\.(?:rb|exs|py) | testing\.go
    )$""",
)
_SNAPSHOT_NAME = re.compile(r"(?:\.snap|\.snap\.new|\.ambr|\.golden|\.approved\.\w+|\.received\.\w+)$")

_CONFIG_PATTERNS = [
    ("test-runner", re.compile(
        r"""(?x)^(
            (?:jest|vitest|vitest\.workspace|vite|karma|playwright|cypress|wdio|ava|nyc|c8)\.config\.[\w.]+
          | jest\.config | vitest\.config | karma\.conf\.[\w.]+ | \.mocharc(?:\.[\w]+)? | \.nycrc(?:\.\w+)? | \.c8rc(?:\.\w+)?
          | pytest\.ini | tox\.ini | noxfile\.py | conftest\.py | phpunit\.xml(?:\.dist)? | \.rspec | \.coveragerc
          | codecov\.ya?ml | \.codecov\.ya?ml | jest\.setup\.[\w.]+ | vitest\.setup\.[\w.]+ | setuptests\.[\w.]+
          | \.simplecov | ava\.config\.[\w.]+ | \.taprc
        )$""", re.I)),
    ("types", re.compile(
        r"^(?:tsconfig[\w.-]*\.json|jsconfig\.json|mypy\.ini|\.mypy\.ini|pyrightconfig\.json|\.flowconfig)$", re.I)),
    ("lint", re.compile(
        r"""(?x)^(
            \.eslintrc(?:\.[\w]+)? | eslint\.config\.[\w.]+ | biome\.jsonc? | \.flake8 | \.pylintrc | pylintrc
          | ruff\.toml | \.ruff\.toml | \.golangci\.(?:ya?ml|toml|json) | clippy\.toml | \.clippy\.toml
          | \.rubocop\.ya?ml | phpstan\.neon(?:\.dist)? | psalm\.xml | \.swiftlint\.ya?ml | detekt\.ya?ml
          | \.stylelintrc(?:\.\w+)? | \.oxlintrc\.json | deno\.jsonc?
        )$""", re.I)),
    ("manifest", re.compile(
        r"""(?x)^(
            pyproject\.toml | setup\.cfg | package\.json | cargo\.toml | makefile | gnumakefile | justfile
          | taskfile\.ya?ml | build\.gradle(?:\.kts)? | pom\.xml | composer\.json | turbo\.json | nx\.json
          | project\.json | mix\.exs | gemfile | rakefile
        )$""", re.I)),
    ("hooks", re.compile(
        r"^(?:\.pre-commit-config\.ya?ml|\.?lefthook\.ya?ml|\.lintstagedrc(?:\.\w+)?|lint-staged\.config\.[\w.]+)$",
        re.I)),
    ("build", re.compile(
        r"""(?x)^(
            babel\.config\.[\w.]+ | \.babelrc(?:\.\w+)? | webpack\.config\.[\w.]+ | rollup\.config\.[\w.]+
          | next\.config\.[\w.]+ | nuxt\.config\.[\w.]+ | svelte\.config\.[\w.]+ | astro\.config\.[\w.]+
          | metro\.config\.[\w.]+ | esbuild\.config\.[\w.]+ | tsup\.config\.[\w.]+ | postcss\.config\.[\w.]+
          | tailwind\.config\.[\w.]+ | remix\.config\.[\w.]+ | gatsby-config\.[\w.]+ | craco\.config\.[\w.]+
          | rspack\.config\.[\w.]+ | setup\.py | manage\.py | gulpfile\.[\w.]+ | gruntfile\.[\w.]+
        )$""", re.I)),
]
_CI_PATH = re.compile(
    r"""(?x)(
        ^\.github/workflows/[^/]+\.ya?ml$ | (?:^|/)\.gitlab-ci\.ya?ml$ | ^\.circleci/config\.ya?ml$
      | (?:^|/)azure-pipelines\.ya?ml$ | (?:^|/)jenkinsfile$ | ^\.buildkite/ | (?:^|/)bitbucket-pipelines\.ya?ml$
      | (?:^|/)\.travis\.ya?ml$ | (?:^|/)\.drone\.ya?ml$ | ^\.github/actions/
    )""", re.I)
_HOOK_PATH = re.compile(r"(?:^|/)\.husky/", re.I)

GENERATED_DIRS = {"node_modules", "vendor", "dist", "build", "out", ".next", ".nuxt", "target", "__pycache__",
                  ".venv", "venv", "env", ".tox", ".mypy_cache", ".pytest_cache", "coverage", "site-packages",
                  ".git", ".gradle", "bower_components", ".turbo", ".svelte-kit", ".parcel-cache", "Pods"}
GENERATED_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock", "poetry.lock", "cargo.lock",
    "go.sum", "uv.lock", "gemfile.lock", "composer.lock", "pipfile.lock", "pdm.lock", "mix.lock", "pubspec.lock",
    "package.resolved", "flake.lock",
}
_GENERATED_NAME = re.compile(r"(?:\.min\.(?:js|css)|\.map|\.pb\.go|_pb2\.pyi?|\.generated\.\w+|\.g\.dart|\.freezed\.dart)$",
                             re.I)


def norm_path(path: str) -> str:
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


_GLOB_CACHE: dict = {}


def _glob_regex(pattern: str):
    rx = _GLOB_CACHE.get(pattern)
    if rx is not None:
        return rx
    pat = norm_path(pattern.strip())
    anchored = "/" in pat.rstrip("/")
    if pat.endswith("/"):
        pat += "**"
    out = []
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    body = "".join(out)
    if anchored:
        rx = re.compile("^" + body.lstrip("/") + "(?:/.*)?$")
    else:
        rx = re.compile("(?:^|/)" + body + "(?:/.*)?$")
    _GLOB_CACHE[pattern] = rx
    return rx


def match_glob(path: str, pattern: str) -> bool:
    """gitignore-flavoured matching: `*` stays within a segment, `**` spans them, patterns without `/` match anywhere."""
    if not pattern or not pattern.strip():
        return False
    return bool(_glob_regex(pattern).search(norm_path(path)))


def detect_lang(path: str) -> str:
    base = posixpath.basename(norm_path(path)).lower()
    if base in NAME_LANG:
        return NAME_LANG[base]
    _, ext = posixpath.splitext(base)
    return EXT_LANG.get(ext, "other")


def classify(path: str, extra_test_globs: Iterable[str] = (), ignore_globs: Iterable[str] = ()) -> FileInfo:
    p = norm_path(path)
    lower = p.lower()
    parts = lower.split("/")
    dirs = parts[:-1]
    base_orig = posixpath.basename(p)
    base = base_orig.lower()
    lang = detect_lang(p)
    info = FileInfo(lang=lang)

    if (
        base in GENERATED_NAMES
        or _GENERATED_NAME.search(base)
        or any(d in GENERATED_DIRS for d in dirs)
        or any(match_glob(p, g) for g in ignore_globs)
    ):
        info.is_generated = True

    if lang in ("markdown", "doc") or base in {"license", "licence", "changelog", "authors", "notice"}:
        info.is_doc = True

    if _SNAPSHOT_NAME.search(base) or any(d in SNAPSHOT_DIRS for d in dirs):
        info.is_snapshot = True

    for kind, pattern in _CONFIG_PATTERNS:
        if pattern.match(base):
            info.config.add(kind)
    if _CI_PATH.search(lower):
        info.config.add("ci")
    if _HOOK_PATH.search(lower):
        info.config.add("hooks")

    in_test_dir = any(d in TEST_DIRS for d in dirs)
    in_support_dir = any(d in TEST_SUPPORT_DIRS for d in dirs)
    if _TEST_SUPPORT_NAME.match(base) or (in_support_dir and info.is_code):
        info.is_test = True
        info.is_test_support = True
    elif _TEST_NAME.match(base_orig) or _TEST_NAME.match(base) or (in_test_dir and info.is_code):
        info.is_test = True
    if any(match_glob(p, g) for g in extra_test_globs):
        info.is_test = True
    if info.is_snapshot:
        info.is_test = False
    return info


def subject_stem(path: str) -> Optional[str]:
    """For a test file, the stem of the module it most likely tests (cart.test.ts -> cart)."""
    base = posixpath.basename(norm_path(path))
    stem = base
    for _ in range(3):
        stem, ext = posixpath.splitext(stem)
        if not ext:
            break
    stem = re.sub(r"(?i)^(?:test_|test(?=[A-Z]))", "", stem)
    stem = re.sub(r"(?i)(?:[._-](?:test|spec|tests|specs|e2e|unit|integration|it|cy))+$", "", stem)
    stem = re.sub(r"(?:Tests?|Spec|Specs|IT|ITCase)$", "", stem)
    stem = stem.strip("._-")
    return stem.lower() or None


def source_stem(path: str) -> str:
    base = posixpath.basename(norm_path(path))
    stem, _ = posixpath.splitext(base)
    return stem.lower()


def is_related_test(test_path: str, source_path: str) -> bool:
    subj = subject_stem(test_path)
    src = source_stem(source_path)
    if not subj or not src:
        return False
    if src in ("index", "main", "__init__", "mod", "lib"):
        src = posixpath.basename(posixpath.dirname(norm_path(source_path))).lower() or src
    return subj == src or subj.replace("_", "") == src.replace("_", "")


def comment_prefixes(lang: str) -> List[str]:
    if lang in ("py", "ruby", "shell", "elixir", "yaml", "toml", "ini", "make"):
        return ["#"]
    if lang == "php":
        return ["//", "#", "/*", "*"]
    if lang == "lua":
        return ["--"]
    return ["//", "/*", "*"]


def is_comment_line(text: str, lang: str) -> bool:
    s = text.lstrip()
    if not s:
        return False
    if lang == "py" and (s.startswith('"""') or s.startswith("'''")):
        return False
    return any(s.startswith(p) for p in comment_prefixes(lang))
