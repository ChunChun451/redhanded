"""Build FileChange objects from unified diffs, text pairs, or Claude Code structured patches."""

from __future__ import annotations

import difflib
import re
from typing import Iterable, List, Optional, Sequence, Tuple

from .classify import norm_path
from .model import FileChange, Hunk, Line

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_GIT_HEADER = re.compile(r"^diff --git (\"?)a/(.*?)\1 (\"?)b/(.*?)\3$")
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "a": "\a", "b": "\b", "f": "\f", "r": "\r", "v": "\v"}


def _unquote(path: str) -> str:
    """Undo git's C-style path quoting ("a/\\303\\251.txt")."""
    if not (len(path) >= 2 and path.startswith('"') and path.endswith('"')):
        return path
    body = path[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            octal = body[i + 1:i + 4]
            if re.fullmatch(r"[0-7]{3}", octal):
                out.append(int(octal, 8))
                i += 4
                continue
            nxt = body[i + 1]
            out.extend(_ESCAPES.get(nxt, nxt).encode("utf-8"))
            i += 2
            continue
        out.extend(ch.encode("utf-8"))
        i += 1
    return out.decode("utf-8", "replace")


def _strip_prefix(path: str) -> Optional[str]:
    path = path.split("\t", 1)[0]
    path = _unquote(path)
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/", "i/", "w/", "c/", "o/")):
        return path[2:]
    return path


def parse_unified_diff(text: str) -> List[FileChange]:
    files: List[FileChange] = []
    cur: Optional[FileChange] = None
    hunk: Optional[Hunk] = None
    old_left = new_left = 0
    old_no = new_no = 0

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for raw in lines:
        line = raw[:-1] if raw.endswith("\r") else raw
        if hunk is not None and (old_left > 0 or new_left > 0):
            tag = line[:1]
            body = line[1:]
            if tag == "\\":
                continue
            if tag == "+":
                hunk.lines.append(Line("+", body, None, new_no))
                new_no += 1
                new_left -= 1
                continue
            if tag == "-":
                hunk.lines.append(Line("-", body, old_no, None))
                old_no += 1
                old_left -= 1
                continue
            if tag == " " or line == "":
                hunk.lines.append(Line(" ", body, old_no, new_no))
                old_no += 1
                new_no += 1
                old_left -= 1
                new_left -= 1
                continue
            hunk = None  # malformed; fall through to header parsing
        if line.startswith("diff --git "):
            cur = FileChange(path="", status="M")
            files.append(cur)
            hunk = None
            m = _GIT_HEADER.match(line)
            if m:
                cur.old_path = m.group(2)
                cur.path = m.group(4)
            continue
        if cur is None:
            if line.startswith("--- "):
                cur = FileChange(path="", status="M")
                files.append(cur)
            else:
                continue
        if line.startswith("new file mode"):
            cur.status = "A"
        elif line.startswith("deleted file mode"):
            cur.status = "D"
        elif line.startswith("rename from "):
            cur.old_path = _unquote(line[len("rename from "):])
            cur.status = "R"
        elif line.startswith("rename to "):
            cur.path = _unquote(line[len("rename to "):])
            cur.status = "R"
        elif line.startswith("copy to "):
            cur.path = _unquote(line[len("copy to "):])
            cur.status = "A"
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            cur.binary = True
        elif line.startswith("--- "):
            old = _strip_prefix(line[4:])
            if old is None:
                cur.status = "A"
            else:
                cur.old_path = old
        elif line.startswith("+++ "):
            new = _strip_prefix(line[4:])
            if new is None:
                cur.status = "D"
                cur.path = cur.old_path or cur.path
            else:
                cur.path = new
        else:
            m = _HUNK.match(line)
            if m:
                old_start = int(m.group(1))
                old_len = int(m.group(2)) if m.group(2) is not None else 1
                new_start = int(m.group(3))
                new_len = int(m.group(4)) if m.group(4) is not None else 1
                hunk = Hunk(old_start, old_len, new_start, new_len)
                cur.hunks.append(hunk)
                old_left, new_left = old_len, new_len
                old_no = old_start if old_len else old_start + 1
                new_no = new_start if new_len else new_start + 1
    for f in files:
        f.path = norm_path(f.path or f.old_path or "")
        if f.old_path:
            f.old_path = norm_path(f.old_path)
        if f.status == "M" and f.old_path and f.old_path != f.path:
            f.status = "R"
        if f.status != "R":
            f.old_path = None
    return [f for f in files if f.path]


def hunks_from_texts(old: str, new: str, max_lines: int = 40000) -> List[Hunk]:
    a = old.splitlines()
    b = new.splitlines()
    if len(a) + len(b) > max_lines:
        a, b = a[:max_lines // 2], b[:max_lines // 2]
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    hunks: List[Hunk] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        h = Hunk(i1 + 1, i2 - i1, j1 + 1, j2 - j1)
        for k in range(i1, i2):
            h.lines.append(Line("-", a[k], k + 1, None))
        for k in range(j1, j2):
            h.lines.append(Line("+", b[k], None, k + 1))
        hunks.append(h)
    return hunks


def change_from_texts(path: str, old: Optional[str], new: Optional[str], origin: str = "transcript") -> FileChange:
    status = "M"
    if old is None:
        status = "A"
    elif new is None:
        status = "D"
    return FileChange(
        path=norm_path(path),
        status=status,
        hunks=hunks_from_texts(old or "", new or ""),
        old_text=old,
        new_text=new,
        origin=origin,
    )


def hunks_from_structured_patch(patch: Sequence[dict]) -> List[Hunk]:
    """Convert Claude Code's structuredPatch ({oldStart, oldLines, newStart, newLines, lines}) to Hunks."""
    hunks: List[Hunk] = []
    for item in patch or []:
        if not isinstance(item, dict):
            continue
        try:
            old_no = int(item.get("oldStart") or 1)
            new_no = int(item.get("newStart") or 1)
            h = Hunk(old_no, int(item.get("oldLines") or 0), new_no, int(item.get("newLines") or 0))
        except (TypeError, ValueError):
            continue
        for raw in item.get("lines") or []:
            if not isinstance(raw, str) or not raw:
                continue
            tag, body = raw[0], raw[1:]
            if tag == "+":
                h.lines.append(Line("+", body, None, new_no))
                new_no += 1
            elif tag == "-":
                h.lines.append(Line("-", body, old_no, None))
                old_no += 1
            elif tag == "\\":
                continue
            else:
                h.lines.append(Line(" ", body, old_no, new_no))
                old_no += 1
                new_no += 1
        hunks.append(h)
    return hunks


def change_blocks(change: FileChange) -> Iterable[Tuple[List[Line], List[Line]]]:
    """Contiguous runs of changed lines: (removed, added) pairs that likely replace each other."""
    for h in change.hunks:
        removed: List[Line] = []
        added: List[Line] = []
        for ln in h.lines:
            if ln.op == " ":
                if removed or added:
                    yield removed, added
                removed, added = [], []
            elif ln.op == "-":
                if added:
                    yield removed, added
                    removed, added = [], []
                removed.append(ln)
            else:
                added.append(ln)
        if removed or added:
            yield removed, added
