"""Rules about production code that games the tests instead of implementing the behavior."""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .. import patterns as P
from ..classify import is_comment_line
from ..model import FileChange, Finding, Line, Severity
from . import Context, finding, net_added
from .tests import rust_test_region_start, swallow_sites

WINDOW = 3


def _source_lines(change: FileChange) -> List[Line]:
    """Net-added lines that belong to production code (excludes Rust #[cfg(test)] regions)."""
    if not change.info.is_source:
        return []
    region = rust_test_region_start(change.new_text) if change.info.lang == "rust" else None
    lines = net_added(change, lambda t: bool(t.strip()))
    if region is not None:
        lines = [ln for ln in lines if ln.new_no is None or ln.new_no < region]
    return lines


def _lit_values(pattern: re.Pattern, text: str, groups: Sequence[str] = ("a", "b")) -> List[str]:
    out = []
    for m in pattern.finditer(text):
        for g in groups:
            if m.groupdict().get(g):
                out.append(P.literal_value(m.group(g)))
    return out


def special_case_candidates(lines: List[Line], lang: str) -> List[Tuple[Line, str, str]]:
    """(line, input literal, returned literal) for conditionals that return a literal."""
    out: List[Tuple[Line, str, str]] = []
    for i, ln in enumerate(lines):
        text = ln.text
        if is_comment_line(text, lang):
            continue
        conds: List[str] = []
        if P.CONDITIONAL.search(text):
            conds.extend(_lit_values(P.COMPARE_LIT, text))
        conds.extend(_lit_values(P.CASE_LIT, text))
        if not conds:
            continue
        returns: List[str] = []
        for pat in (P.TERNARY_LIT, P.PY_COND_LIT):
            m = pat.search(text)
            if m:
                returns.append(P.literal_value(m.group("v")))
        for j in range(i, min(i + WINDOW, len(lines))):
            if returns:
                break
            if j > i and lines[j].new_no is not None and ln.new_no is not None and lines[j].new_no - ln.new_no > WINDOW:
                break
            m = P.RETURN_LIT.search(lines[j].text)
            if m:
                returns.append(P.literal_value(m.group("v")))
                break
        for c in conds:
            for r in returns:
                # The returned value is the "answer"; returning 0, "" or true for some input proves little.
                if c != r and P.is_notable(r):
                    out.append((ln, c, r))
    return out


def lookup_pairs(lines: List[Line], lang: str) -> List[Tuple[Line, str, str]]:
    out: List[Tuple[Line, str, str]] = []
    for ln in lines:
        if is_comment_line(ln.text, lang):
            continue
        for pat in (P.DICT_PAIR, P.TUPLE_PAIR):
            for m in pat.finditer(ln.text):
                k, v = P.literal_value(m.group("k")), P.literal_value(m.group("v"))
                if k != v and P.is_notable(k) and P.is_notable(v):
                    out.append((ln, k, v))
    return out


Lines = Dict[str, List[int]]  # literal -> line numbers
FileIndex = Tuple[Lines, Lines]  # where each literal is passed in, where it is expected


def _file_index(lines: List[str]) -> FileIndex:
    inputs: Lines = {}
    expected: Lines = {}
    for n, text in enumerate(lines, 1):
        if not P.literals_in(text):
            continue
        for v in set(P.input_literals(text)):
            inputs.setdefault(v, []).append(n)
        for v in set(P.expected_literals(text)):
            expected.setdefault(v, []).append(n)
    return inputs, expected


def _corpus_index(corpus: List[Tuple[str, List[str]]], cache: Dict[object, FileIndex]) -> Dict[str, FileIndex]:
    index: Dict[str, FileIndex] = {}
    for path, lines in corpus:
        key = (path, len(lines), hash(tuple(lines)))
        if key not in cache:
            cache[key] = _file_index(lines)
        index[path] = cache[key]
    return index


def _find_pair(index: Dict[str, FileIndex], a: str, b: str) -> Optional[Tuple[str, int]]:
    """A test that passes `a` in and, within a few lines, expects `b` back."""
    for path, (inputs, expected) in index.items():
        for na in inputs.get(a, ()):
            for nb in expected.get(b, ()):
                if abs(na - nb) <= WINDOW:
                    return path, na
    return None


def check_special_cases(changes: List[FileChange], ctx: Context) -> Iterable[Finding]:
    for change in changes:
        lines = _source_lines(change)
        if not lines:
            continue
        candidates = special_case_candidates(lines, change.info.lang)
        pairs = lookup_pairs(lines, change.info.lang)
        if not candidates and len(pairs) < 2:
            continue
        corpus = ctx.test_corpus(change)
        if not corpus:
            continue
        cache = ctx.extra.setdefault("literal_index", {})
        index = _corpus_index(corpus, cache)  # type: ignore[arg-type]
        reported = set()
        for ln, cond, ret in candidates:
            hit = _find_pair(index, cond, ret)
            if hit and ln.new_no not in reported:
                reported.add(ln.new_no)
                yield finding(
                    "special-cased-test-value",
                    "Returns hard-coded %s when the input is %s; the same pair appears in %s:%d"
                    % (_show(ret), _show(cond), hit[0], hit[1]),
                    change, ln, evidence=["%s:%d" % hit],
                )
        matched = []
        for ln, k, v in pairs:
            hit = _find_pair(index, k, v)
            if hit:
                matched.append((ln, k, v, hit))
        if len({m[3] for m in matched}) >= 2:
            ln = matched[0][0]
            if ln.new_no not in reported:
                yield finding(
                    "special-cased-test-value",
                    "Lookup table copies %d input/output pairs from the tests (e.g. %s -> %s)"
                    % (len(matched), _show(matched[0][1]), _show(matched[0][2])),
                    change, ln, evidence=["%s:%d" % m[3] for m in matched[:5]],
                )


def _show(value: str) -> str:
    v = value if len(value) <= 40 else value[:37] + "..."
    return "`%s`" % v


def check_test_env_branch(change: FileChange, ctx: Context) -> Iterable[Finding]:
    for ln in _source_lines(change):
        if is_comment_line(ln.text, change.info.lang):
            continue
        m = P.TEST_ENV_STRONG.search(ln.text)
        if m:
            yield finding("test-environment-branch", "Production code checks for the test runner (`%s`)" % m.group(0)[:60],
                          change, ln)
            continue
        m = P.TEST_ENV_WEAK.search(ln.text)
        if m:
            yield finding("test-environment-branch", "Production code branches on a test flag (`%s`)" % m.group(0)[:60],
                          change, ln, severity=Severity.MEDIUM)


_ABSTRACT = re.compile(r"@(?:abc\.)?abstract(?:method|property)\b|\babstract\b|\binterface\b|\bProtocol\b")


def check_placeholders(change: FileChange, ctx: Context) -> Iterable[Finding]:
    new_lines = change.new_lines()
    for ln in _source_lines(change):
        text = ln.text
        m = P.PLACEHOLDER_STRONG.search(text)
        if m:
            yield finding("placeholder-implementation", "Comment says this code exists to satisfy the tests (\"%s\")"
                          % m.group(0).strip()[:60], change, ln, severity=Severity.HIGH)
            continue
        m = P.PLACEHOLDER.search(text)
        if m:
            yield finding("placeholder-implementation", "Placeholder wording in new code (\"%s\")" % m.group(0).strip()[:60],
                          change, ln)
            continue
        m = P.NOT_IMPLEMENTED_CODE.search(text)
        if m:
            if ln.new_no and new_lines:
                above = new_lines[max(0, ln.new_no - 5):ln.new_no - 1]
                if any(_ABSTRACT.search(a) for a in above):
                    continue
                if change.info.lang == "py" and _method_stub(new_lines, ln.new_no):
                    continue
            yield finding("placeholder-implementation", "Stub left in new code (`%s`)" % m.group(0).strip()[:60], change, ln)


_DEF = re.compile(r"^(\s+)(?:async\s+)?def\s+\w+\s*\(")


def _method_stub(lines: List[str], n: int) -> bool:
    """True when line n is the whole body of a method (a base-class hook that subclasses override)."""
    raise_line = lines[n - 1]
    body_indent = len(raise_line) - len(raise_line.lstrip())
    in_doc = in_signature = False
    for i in range(n - 2, max(-1, n - 16), -1):
        text = lines[i]
        s = text.strip()
        quotes = s.count('"""') + s.count("'''")
        if in_doc or quotes % 2:
            in_doc = in_doc != bool(quotes % 2)
            continue
        if not s or s.startswith("#") or (quotes and s[:1] in "\"'rRuU"):
            continue
        m = _DEF.match(text)
        if m:
            if len(m.group(1)) >= body_indent:
                return False
            signature = " ".join(part.strip() for part in lines[i:n - 1])
            return bool(re.search(r"\(\s*(?:self|cls)\s*[,):]", signature))
        if s.startswith(")"):
            in_signature = True  # the closing line of a signature split over several lines
        if not in_signature and len(text) - len(text.lstrip()) == body_indent:
            return False  # another statement in the same body
    return False


def check_swallowed_errors(change: FileChange, ctx: Context) -> Iterable[Finding]:
    if not change.info.is_source:
        return
    region = rust_test_region_start(change.new_text) if change.info.lang == "rust" else None
    for ln in swallow_sites(change):
        if region is not None and ln.new_no is not None and ln.new_no >= region:
            continue
        yield finding("error-swallowed", "Error caught and ignored", change, ln)


def check_suppressions(change: FileChange, ctx: Context) -> Iterable[Finding]:
    info = change.info
    if not info.is_code or info.is_generated:
        return
    lang = info.lang
    file_level: List[Line] = []
    line_level: List[Line] = []
    coverage: List[Line] = []
    for ln in net_added(change, lambda t: bool(t.strip())):
        text = ln.text
        if P.SUPPRESS_FILE_LEVEL.search(text):
            file_level.append(ln)
        elif P.SUPPRESS_LINE.search(text) or (lang == "js" and P.TS_ESCAPE_HATCH.search(text)
                                               and not is_comment_line(text, lang)):
            line_level.append(ln)
        if P.COVERAGE_IGNORE.search(text):
            coverage.append(ln)
    for ln in file_level:
        yield finding("checks-suppressed", "Type/lint checking disabled for the whole file", change, ln,
                      severity=Severity.MEDIUM)
    if line_level:
        n = len(line_level)
        sev = Severity.MEDIUM if n >= 3 else Severity.LOW
        yield finding("checks-suppressed", "%d type/lint suppression%s added" % (n, "" if n == 1 else "s"),
                      change, line_level[0], severity=sev)
    if coverage:
        n = len(coverage)
        yield finding("coverage-excluded", "%d coverage exclusion%s added" % (n, "" if n == 1 else "s"), change, coverage[0])


FILE_RULES = [check_test_env_branch, check_placeholders, check_swallowed_errors, check_suppressions]
SET_RULES = [check_special_cases]
