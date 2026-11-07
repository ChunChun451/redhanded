"""Rules about the test suite itself: skipped, deleted, hollowed-out or rewritten tests."""

from __future__ import annotations

import difflib
import posixpath
import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Tuple

from .. import patterns as P
from ..classify import is_comment_line, subject_stem
from ..diffs import change_blocks
from ..model import FileChange, Finding, Line, Severity, normalize_code
from . import Context, finding, net_added, net_removed

_RUST_TEST_REGION = re.compile(r"^\s*#\[\s*cfg\s*\(\s*test\s*\)\s*\]")


def rust_test_region_start(text: Optional[str]) -> Optional[int]:
    """1-based line number of the first #[cfg(test)] in a Rust file, if any."""
    if not text:
        return None
    for i, line in enumerate(text.splitlines(), 1):
        if _RUST_TEST_REGION.match(line):
            return i
    return None


def is_test_line(change: FileChange, line: Line, region: Optional[int] = None) -> bool:
    if change.info.is_test:
        return True
    if change.info.lang == "rust" and region is not None:
        n = line.new_no if line.new_no is not None else line.old_no
        return n is not None and n > region
    return False


def _test_lines(change: FileChange) -> Tuple[bool, Optional[int]]:
    """(file has any test code, rust test region start)."""
    if change.info.is_snapshot or change.info.is_generated or change.info.is_doc:
        return False, None
    if change.info.is_test:
        return True, None
    if change.info.lang == "rust":
        region = rust_test_region_start(change.new_text) or rust_test_region_start(change.old_text)
        return region is not None, region
    return False, None


def _code(text: str, lang: str) -> bool:
    return bool(text.strip()) and not is_comment_line(text, lang)


_GUARD_BEFORE = re.compile(r"\b(?:if|unless|when|except|catch)\b")
_GUARD_LINE = re.compile(
    r"^\s*(?:\}\s*)?(?:(?:else\s+)?if\b|elif\b|unless\b|when\b|else\b|except\b|catch\b|rescue\b).*(?:[:{]|\bthen|\bdo)\s*"
    r"(?:#.*|//.*)?$"
)


def _guarded(change: FileChange, ln: Line, hit: str) -> bool:
    """True when a runtime skip only happens under a condition (`if not db: pytest.skip(...)`, Go's `testing.Short()`)."""
    text = ln.text
    pos = text.find(hit)
    if pos > 0 and _GUARD_BEFORE.search(text[:pos]):
        return True
    if change.info.lang == "ruby" and pos >= 0:
        rest = re.sub(P.STRING_LIT, '""', text[pos + len(hit):])
        if re.search(r"\b(?:if|unless)\b", rest):
            return True
    lines = change.new_lines()
    if ln.new_no is None or not lines:
        return False
    indent = len(text) - len(text.lstrip())
    for i in range(ln.new_no - 2, max(-1, ln.new_no - 5), -1):
        prev = lines[i]
        if not prev.strip():
            continue
        prev_indent = len(prev) - len(prev.lstrip())
        return prev_indent < indent and bool(_GUARD_LINE.match(prev))
    return False


def check_skips_and_focus(change: FileChange, ctx: Context) -> Iterable[Finding]:
    has_tests, region = _test_lines(change)
    fam = P.family(change.info.lang)
    if not has_tests or fam is None:
        return
    lang = change.info.lang

    def scan(table, text: str) -> Optional[str]:
        pat = table.get(fam)
        if pat is None:
            return None
        m = pat.search(text)
        return m.group(0).strip() if m else None

    for ln in net_added(change, lambda t: _code(t, lang)):
        if not is_test_line(change, ln, region):
            continue
        hit = scan(P.FOCUS, ln.text)
        if hit:
            yield finding("test-focused", "Focused test `%s` makes the runner skip every other test" % hit, change, ln)
            continue
        hit = scan(P.SKIP, ln.text)
        if hit:
            if _guarded(change, ln, hit):
                yield finding("test-skipped", "Test conditionally skipped with `%s`" % hit, change, ln,
                              severity=Severity.MEDIUM)
            else:
                yield finding("test-skipped", "Test disabled with `%s`" % hit, change, ln)
            continue
        hit = scan(P.CONDITIONAL_SKIP, ln.text)
        if hit:
            yield finding("test-skipped", "Test conditionally skipped with `%s`" % hit, change, ln,
                          severity=Severity.MEDIUM)
            continue
        hit = scan(P.TODO_TEST, ln.text)
        if hit:
            yield finding("test-skipped", "Test turned into a todo placeholder (`%s`)" % hit, change, ln,
                          severity=Severity.LOW)
            continue
        hit = scan(P.RETRIES, ln.text)
        if hit:
            yield finding("test-retries-added", "Retries added (`%s`)" % hit, change, ln)


def _strip_comments(text: str, lang: str) -> str:
    return "\n".join(line for line in text.splitlines() if not is_comment_line(line, lang))


def test_names(text: Optional[str], lang: str) -> Counter:
    names: Counter = Counter()
    fam = P.family(lang)
    if not text or fam not in P.TEST_DECL:
        return names
    body = _strip_comments(text, lang)
    for pattern, groups in P.TEST_DECL[fam]:
        for m in pattern.finditer(body):
            name = next((m.group(g) for g in groups if m.group(g)), None)
            if name is None:
                continue
            if fam == "cpp" and len(groups) == 2 and m.group(2):
                name = "%s.%s" % (m.group(1), m.group(2))
            names[name.strip()] += 1
    return names


def count_assertions(text: Optional[str], lang: str) -> Optional[int]:
    fam = P.family(lang)
    if text is None or fam not in P.ASSERT:
        return None
    pat = P.ASSERT[fam]
    total = 0
    for line in text.splitlines():
        if is_comment_line(line, lang):
            continue
        total += len(pat.findall(line))
    return total


def check_deleted_tests(changes: List[FileChange], ctx: Context) -> Iterable[Finding]:
    per_file: Dict[str, Tuple[Counter, Counter]] = {}
    gained: Counter = Counter()
    gained_files = set()
    for ch in changes:
        if not ch.info.is_test or ch.info.is_test_support or ch.info.is_generated:
            continue
        old = test_names(ch.old_text, ch.info.lang) if ch.status != "A" else Counter()
        new = test_names(ch.new_text, ch.info.lang) if ch.status != "D" else Counter()
        per_file[ch.path] = (old, new)
        gained.update(new - old)
        if ch.status in ("A", "R"):
            gained_files.add(posixpath.basename(ch.path).lower())
    for ch in changes:
        if ch.path not in per_file:
            continue
        old, new = per_file[ch.path]
        if ch.status == "D":
            lost_names = list((old - Counter()).elements())
            if not lost_names:
                if posixpath.basename(ch.path).lower() in gained_files:
                    continue
                if ch.old_text is not None and not test_names(ch.old_text, ch.info.lang) and P.family(ch.info.lang) in P.TEST_DECL:
                    continue  # nothing recognisable as a test in it
                yield finding("test-deleted", "Test file deleted", ch, line_no=None, snippet="- " + ch.path)
                continue
            truly_lost = [n for n in lost_names if gained[n] <= 0]
            if not truly_lost:
                continue
            yield finding("test-deleted", "Test file deleted (%d test%s: %s)" % (
                len(truly_lost), "" if len(truly_lost) == 1 else "s", _names(truly_lost)), ch,
                snippet="- " + ch.path)
            continue
        old_total, new_total = sum(old.values()), sum(new.values())
        if new_total >= old_total:
            continue
        missing = list((old - new).elements())
        truly_lost = []
        for n in missing:
            if gained[n] > 0 and n not in (new - old):
                gained[n] -= 1
                continue
            truly_lost.append(n)
        lost = min(old_total - new_total, len(truly_lost))
        if lost <= 0:
            continue
        removed_line = _first_removed_decl(ch, truly_lost)
        yield finding("test-deleted", "Removed %d test%s: %s" % (lost, "" if lost == 1 else "s", _names(truly_lost[:lost])),
                      ch, removed_line, detail="%d tests before, %d after" % (old_total, new_total))


def _names(names: List[str], limit: int = 4) -> str:
    shown = ", ".join("`%s`" % n[:60] for n in names[:limit])
    if len(names) > limit:
        shown += " and %d more" % (len(names) - limit)
    return shown


def _first_removed_decl(change: FileChange, names: List[str]) -> Optional[Line]:
    for ln in change.removed():
        if any(n and n in ln.text for n in names):
            return ln
    removed = change.removed()
    return removed[0] if removed else None


def check_assertions_removed(change: FileChange, ctx: Context) -> Iterable[Finding]:
    if not change.info.is_test or change.info.is_test_support or change.status in ("A", "D"):
        return
    fam = P.family(change.info.lang)
    if fam not in P.ASSERT:
        return
    old_names = test_names(change.old_text, change.info.lang)
    new_names = test_names(change.new_text, change.info.lang)
    if sum(new_names.values()) < sum(old_names.values()):
        return  # reported as deleted tests
    before = count_assertions(change.old_text, change.info.lang)
    after = count_assertions(change.new_text, change.info.lang)
    pat = P.ASSERT[fam]
    lost_lines = net_removed(change, lambda t: bool(pat.search(t)) and not is_comment_line(t, change.info.lang))
    if before is not None and after is not None:
        if after >= before:
            return
        lost = before - after
    else:
        gained_lines = net_added(change, lambda t: bool(pat.search(t)) and not is_comment_line(t, change.info.lang))
        lost = len(lost_lines) - len(gained_lines)
        if lost <= 0:
            return
    first = lost_lines[0] if lost_lines else None
    detail = "%s assertions before, %s after" % (before, after) if before is not None else None
    yield finding("assertions-removed", "Removed %d assertion%s while keeping the tests" % (lost, "" if lost == 1 else "s"),
                  change, first, detail=detail)


def check_commented_and_tautological(change: FileChange, ctx: Context) -> Iterable[Finding]:
    has_tests, region = _test_lines(change)
    if not has_tests:
        return
    for ln in net_added(change, lambda t: bool(P.COMMENTED_ASSERT.search(t))):
        if is_test_line(change, ln, region):
            yield finding("assertion-commented-out", "Assertion commented out", change, ln)
    for ln in net_added(change, lambda t: any(p.search(t) for p in P.TAUTOLOGY)):
        if is_test_line(change, ln, region) and not is_comment_line(ln.text, change.info.lang):
            yield finding("tautological-assertion", "Assertion can never fail", change, ln)


def _added_runs(change: FileChange) -> Iterable[List[Line]]:
    for h in change.hunks:
        run: List[Line] = []
        for ln in h.lines:
            if ln.op == "+":
                run.append(ln)
            elif ln.op == " " and run and not ln.text.strip():
                continue
            else:
                if run:
                    yield run
                run = []
        if run:
            yield run


def swallow_sites(change: FileChange) -> List[Line]:
    """Added lines that open an empty or swallowing catch/except block."""
    lang = change.info.lang
    sites: List[Line] = []
    moved = Counter(normalize_code(ln.text) for ln in change.removed())
    for run in _added_runs(change):
        for i, ln in enumerate(run):
            text = ln.text
            if moved[normalize_code(text)] > 0:
                continue
            if P.EMPTY_CATCH_INLINE.search(text):
                sites.append(ln)
                continue
            if lang == "py" and re.match(r"^\s*except\s+AssertionError\b", text):
                sites.append(ln)
                continue
            if P.CATCH_OPEN.search(text):
                j = i + 1
                while j < len(run) and (not run[j].text.strip() or is_comment_line(run[j].text, lang)):
                    j += 1
                if j < len(run) and P.BLOCK_CLOSE.match(run[j].text):
                    sites.append(ln)
                continue
            # `except (TypeError, ValueError): continue` is a deliberate choice; `except Exception: pass` hides anything.
            if lang == "py" and P.EXCEPT_OPEN.match(text) and P.broad_except(text):
                j = i + 1
                while j < len(run) and (not run[j].text.strip() or is_comment_line(run[j].text, lang)):
                    j += 1
                if j < len(run) and P.SWALLOW_BODY.match(run[j].text):
                    indent = len(text) - len(text.lstrip())
                    nxt = run[j + 1].text if j + 1 < len(run) else ""
                    nxt_indent = len(nxt) - len(nxt.lstrip()) if nxt.strip() else -1
                    if nxt_indent <= indent:
                        sites.append(ln)
    return sites


def check_swallowed_assertions(change: FileChange, ctx: Context) -> Iterable[Finding]:
    has_tests, region = _test_lines(change)
    if not has_tests:
        return
    for ln in swallow_sites(change):
        if is_test_line(change, ln, region):
            yield finding("assertion-swallowed", "Failures inside this test are caught and ignored", change, ln)


_TOKEN = re.compile(P.LIT + r"|[A-Za-z_$][\w$]*|\S")
_LIT_FULL = re.compile("^" + P.LIT + "$")
_TOLERANCE = [
    (re.compile(r"toBeCloseTo\([^,]+,\s*(\d+)\s*\)"), "lower"),
    (re.compile(r"\bplaces\s*=\s*(\d+)"), "lower"),
    (re.compile(r"\b(?:delta|rel|abs|rtol|atol|epsilon|tolerance)\s*=\s*([\d.]+(?:e-?\d+)?)"), "higher"),
    (re.compile(r"\bIn(?:Delta|Epsilon)\([^,]+,[^,]+,[^,]+,\s*([\d.]+(?:e-?\d+)?)\s*\)"), "higher"),
    (re.compile(r"\bassertEquals\([^,]+,[^,]+,\s*([\d.]+(?:e-?\d+)?)\s*\)"), "higher"),
    (re.compile(r"\bwithin\(\s*([\d.]+)\s*\)"), "higher"),
]


def strength(text: str, fam: str) -> Optional[int]:
    if any(p.search(text) for p in P.TAUTOLOGY):
        return 0
    for pattern, score in P.STRENGTH.get(fam, []):
        if pattern.search(text):
            return score
    pat = P.ASSERT.get(fam)
    if pat is not None and pat.search(text):
        return 3
    return None


def _tolerance_loosened(old: str, new: str) -> Optional[str]:
    for pattern, direction in _TOLERANCE:
        a, b = pattern.search(old), pattern.search(new)
        if not a or not b:
            continue
        try:
            va, vb = float(a.group(1)), float(b.group(1))
        except ValueError:
            continue
        if direction == "lower" and vb < va:
            return "precision lowered from %s to %s" % (a.group(1), b.group(1))
        if direction == "higher" and va > 0 and vb >= va * 10:
            return "tolerance widened from %s to %s" % (a.group(1), b.group(1))
        if direction == "higher" and va == 0 and vb > 0:
            return "tolerance widened from 0 to %s" % b.group(1)
    return None


def _literal_only_change(old: str, new: str) -> Optional[Tuple[str, str]]:
    ta = [m.group(0) for m in _TOKEN.finditer(old)]
    tb = [m.group(0) for m in _TOKEN.finditer(new)]
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    changed_old: List[str] = []
    changed_new: List[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag != "replace":
            return None
        seg_a, seg_b = ta[i1:i2], tb[j1:j2]
        if not all(_LIT_FULL.match(t) for t in seg_a + seg_b):
            return None
        changed_old.extend(seg_a)
        changed_new.extend(seg_b)
    if not changed_old:
        return None
    return " ".join(changed_old), " ".join(changed_new)


def _pair(removed: List[Line], added: List[Line]) -> List[Tuple[Line, Line]]:
    pairs: List[Tuple[Line, Line]] = []
    used = set()
    for r in removed:
        best, best_ratio = None, 0.0
        for idx, a in enumerate(added):
            if idx in used:
                continue
            ratio = difflib.SequenceMatcher(None, normalize_code(r.text), normalize_code(a.text)).ratio()
            if ratio > best_ratio:
                best, best_ratio = idx, ratio
        if best is not None and best_ratio >= 0.5:
            used.add(best)
            pairs.append((r, added[best]))
    return pairs


def check_weakened(change: FileChange, ctx: Context) -> Iterable[Finding]:
    has_tests, region = _test_lines(change)
    fam = P.family(change.info.lang)
    if not has_tests or fam is None or change.status in ("A", "D"):
        return
    lang = change.info.lang
    for removed, added in change_blocks(change):
        if not removed or not added:
            continue
        r_asserts = [ln for ln in removed if strength(ln.text, fam) is not None and not is_comment_line(ln.text, lang)]
        a_asserts = [ln for ln in added if strength(ln.text, fam) is not None and not is_comment_line(ln.text, lang)]
        for old, new in _pair(r_asserts, a_asserts):
            if not is_test_line(change, new, region):
                continue
            if normalize_code(old.text) == normalize_code(new.text):
                continue
            s_old, s_new = strength(old.text, fam), strength(new.text, fam)
            if s_old is None or s_new is None or s_new == 0:
                continue  # tautologies are reported separately
            if s_new <= s_old - 2 or (s_old >= 3 and s_new == 1):
                yield finding("assertion-weakened", "Assertion weakened (strictness %d -> %d)" % (s_old, s_new),
                              change, new, before=old.text.strip()[:240])
                continue
            loosened = _tolerance_loosened(old.text, new.text)
            if loosened:
                yield finding("assertion-weakened", "Assertion weakened: %s" % loosened, change, new,
                              before=old.text.strip()[:240])
                continue
            lit = _literal_only_change(old.text, new.text)
            if lit:
                yield finding("expected-value-changed", "Expected value changed from %s to %s" % (lit[0][:60], lit[1][:60]),
                              change, new, before=old.text.strip()[:240])


_JS_MOCK = re.compile(r"\b(?:jest|vi)\.(?:mock|doMock)\(\s*(['\"])(.+?)\1\s*\)")


def check_mocked_subject(change: FileChange, ctx: Context) -> Iterable[Finding]:
    if not change.info.is_test or change.info.lang != "js":
        return
    subject = subject_stem(change.path)
    if not subject:
        return
    for ln in net_added(change, lambda t: bool(_JS_MOCK.search(t))):
        target = _JS_MOCK.search(ln.text).group(2)
        if not target.startswith("."):
            continue  # third-party module; mocking it is normal
        stem = posixpath.basename(target)
        stem = re.sub(r"\.(?:[cm]?[jt]sx?)$", "", stem).lower()
        if stem == "index":
            stem = posixpath.basename(posixpath.dirname(target)).lower()
        if stem == subject:
            yield finding("mocked-unit-under-test", "Mocks `%s`, the module this test file is testing" % target, change, ln)


def check_snapshots(changes: List[FileChange], ctx: Context) -> Iterable[Finding]:
    snaps = [c for c in changes if c.info.is_snapshot and c.status != "A"]
    if not snaps:
        return
    deleted = [c for c in snaps if c.status == "D"]
    first = snaps[0]
    if len(snaps) == 1:
        msg = "Snapshot file %s" % ("deleted" if deleted else "rewritten")
    else:
        msg = "%d snapshot files rewritten%s" % (len(snaps), " (%d deleted)" % len(deleted) if deleted else "")
    yield finding("snapshot-rewritten", msg, first, line_no=None, snippet=", ".join(c.path for c in snaps[:5]))


FILE_RULES = [
    check_skips_and_focus,
    check_assertions_removed,
    check_commented_and_tautological,
    check_swallowed_assertions,
    check_weakened,
    check_mocked_subject,
]
SET_RULES = [check_deleted_tests, check_snapshots]
