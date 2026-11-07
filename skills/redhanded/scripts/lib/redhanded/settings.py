"""Project settings from .redhanded.toml, [tool.redhanded] in pyproject.toml, or .redhanded.json."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .model import Severity

CONFIG_FILES = (".redhanded.toml", "redhanded.toml", ".redhanded.json")


@dataclass
class Settings:
    fail_on: Severity = Severity.HIGH  # CLI exit code 1 at or above this severity
    block_on: Severity = Severity.HIGH  # Claude Code Stop hook blocks at or above this severity
    rules: Dict[str, Severity] = field(default_factory=dict)  # per-rule severity (off disables)
    ignore: List[str] = field(default_factory=list)  # path globs that are never checked
    test_globs: List[str] = field(default_factory=list)  # extra globs for test files
    announce: bool = True  # tell the agent at session start that its work will be checked
    post_edit: bool = True  # report problems right after each edit
    max_blocks: int = 3  # most times one session can be sent back to work
    source: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


def load(root: Optional[str]) -> Settings:
    settings = Settings()
    if not root:
        return settings
    data: Optional[Dict[str, Any]] = None
    for name in CONFIG_FILES:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    raw = fh.read()
                data = json.loads(raw) if name.endswith(".json") else parse_toml(raw)
                settings.source = name
            except (OSError, ValueError) as exc:
                settings.warnings.append("could not read %s: %s" % (name, exc))
            break
    if data is None:
        path = os.path.join(root, "pyproject.toml")
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = parse_toml(fh.read())
                tool = doc.get("tool") if isinstance(doc.get("tool"), dict) else {}
                if isinstance(tool.get("redhanded"), dict):
                    data = tool["redhanded"]
                    settings.source = "pyproject.toml [tool.redhanded]"
            except (OSError, ValueError) as exc:
                settings.warnings.append("could not read pyproject.toml: %s" % exc)
    if data:
        apply(settings, data)
    return settings


def apply(settings: Settings, data: Dict[str, Any]) -> None:
    from .rules import BY_ID

    def sev(key: str, value: Any) -> Optional[Severity]:
        try:
            return Severity.parse(value)
        except ValueError as exc:
            settings.warnings.append("%s: %s" % (key, exc))
            return None

    for key, value in data.items():
        k = key.replace("-", "_")
        if k in ("fail_on", "block_on"):
            s = sev(key, value)
            if s is not None:
                setattr(settings, k, s)
        elif k in ("ignore", "test_globs", "tests"):
            items = [value] if isinstance(value, str) else value
            if isinstance(items, list):
                target = settings.ignore if k == "ignore" else settings.test_globs
                target.extend(str(v) for v in items if str(v).strip())
        elif k in ("announce", "post_edit"):
            setattr(settings, k, bool(value))
        elif k == "max_blocks":
            try:
                settings.max_blocks = max(0, int(value))
            except (TypeError, ValueError):
                settings.warnings.append("max_blocks must be a number")
        elif k == "rules" and isinstance(value, dict):
            for rule, level in value.items():
                if rule not in BY_ID:
                    settings.warnings.append("unknown rule %r in settings" % rule)
                    continue
                s = sev("rules." + rule, level)
                if s is not None:
                    settings.rules[rule] = s
        else:
            settings.warnings.append("unknown setting %r" % key)


# --------------------------------------------------------------------------- minimal TOML


def parse_toml(text: str) -> Dict[str, Any]:
    """TOML via tomllib when available (3.11+); otherwise a small parser for the subset settings use."""
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        return _MiniToml(text).parse()
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(str(exc))


class _MiniToml:
    _KEY = re.compile(r'\s*("(?:[^"\\]|\\.)*"|\'[^\']*\'|[A-Za-z0-9_-]+)\s*')

    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def parse(self) -> Dict[str, Any]:
        root: Dict[str, Any] = {}
        table = root
        while self.pos < len(self.text):
            self._skip_ws_and_comments()
            if self.pos >= len(self.text):
                break
            ch = self.text[self.pos]
            if ch == "[":
                is_array = self.text.startswith("[[", self.pos)
                end = self.text.find("]]" if is_array else "]", self.pos)
                if end < 0:
                    raise ValueError("unterminated table header")
                keys = self._split_keys(self.text[self.pos + (2 if is_array else 1):end])
                self.pos = end + (2 if is_array else 1)
                table = root
                for i, key in enumerate(keys):
                    last = i == len(keys) - 1
                    if last and is_array:
                        arr = table.setdefault(key, [])
                        if not isinstance(arr, list):
                            raise ValueError("key %r is not an array of tables" % key)
                        arr.append({})
                        table = arr[-1]
                    else:
                        nxt = table.setdefault(key, {})
                        if isinstance(nxt, list):
                            nxt = nxt[-1]
                        if not isinstance(nxt, dict):
                            raise ValueError("key %r is not a table" % key)
                        table = nxt
                continue
            eq = self.text.find("=", self.pos)
            nl = self.text.find("\n", self.pos)
            if eq < 0 or (0 <= nl < eq):
                raise ValueError("expected key = value near %r" % self.text[self.pos:self.pos + 30])
            keys = self._split_keys(self.text[self.pos:eq])
            self.pos = eq + 1
            value = self._value()
            target = table
            for key in keys[:-1]:
                target = target.setdefault(key, {})
            target[keys[-1]] = value
        return root

    def _split_keys(self, raw: str) -> List[str]:
        keys = []
        for m in re.finditer(r'"((?:[^"\\]|\\.)*)"|\'([^\']*)\'|([A-Za-z0-9_-]+)', raw):
            keys.append(next(g for g in m.groups() if g is not None))
        if not keys:
            raise ValueError("empty key")
        return keys

    def _skip_ws_and_comments(self, newlines: bool = True) -> None:
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch == "#":
                nl = self.text.find("\n", self.pos)
                self.pos = len(self.text) if nl < 0 else nl
            elif ch in " \t\r" or (newlines and ch == "\n"):
                self.pos += 1
            else:
                break

    def _value(self) -> Any:
        self._skip_ws_and_comments(newlines=False)
        t = self.text
        if t.startswith('"""', self.pos) or t.startswith("'''", self.pos):
            q = t[self.pos:self.pos + 3]
            end = t.find(q, self.pos + 3)
            if end < 0:
                raise ValueError("unterminated string")
            s = t[self.pos + 3:end]
            self.pos = end + 3
            s = s[1:] if s.startswith("\n") else s
            return _unescape(s) if q == '"""' else s
        ch = t[self.pos:self.pos + 1]
        if ch == '"':
            m = re.compile(r'"((?:[^"\\\n]|\\.)*)"').match(t, self.pos)
            if not m:
                raise ValueError("bad string")
            self.pos = m.end()
            return _unescape(m.group(1))
        if ch == "'":
            end = t.find("'", self.pos + 1)
            if end < 0:
                raise ValueError("bad string")
            s = t[self.pos + 1:end]
            self.pos = end + 1
            return s
        if ch == "[":
            self.pos += 1
            items = []
            while True:
                self._skip_ws_and_comments()
                if t.startswith("]", self.pos):
                    self.pos += 1
                    return items
                items.append(self._value())
                self._skip_ws_and_comments()
                if t.startswith(",", self.pos):
                    self.pos += 1
                elif t.startswith("]", self.pos):
                    self.pos += 1
                    return items
                else:
                    raise ValueError("bad array")
        if ch == "{":
            end = t.find("}", self.pos)
            if end < 0:
                raise ValueError("bad inline table")
            body = t[self.pos + 1:end]
            self.pos = end + 1
            out: Dict[str, Any] = {}
            for part in _split_top(body):
                if "=" in part:
                    k, v = part.split("=", 1)
                    out[self._split_keys(k)[-1]] = _MiniToml(v.strip())._value()
            return out
        m = re.compile(r"[^\s,\]#}]+").match(t, self.pos)
        if not m:
            raise ValueError("expected a value")
        self.pos = m.end()
        word = m.group(0)
        if word == "true":
            return True
        if word == "false":
            return False
        try:
            return int(word.replace("_", ""), 0) if not re.search(r"[.eE]", word) else float(word.replace("_", ""))
        except ValueError:
            return word


def _split_top(body: str) -> List[str]:
    parts, depth, cur, quote = [], 0, [], None
    for ch in body:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def _unescape(s: str) -> str:
    def rep(m: "re.Match[str]") -> str:
        c = m.group(1)
        table = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "b": "\b", "f": "\f"}
        if c in table:
            return table[c]
        if c[0] in "uU":
            return chr(int(c[1:], 16))
        return c

    return re.sub(r"\\(u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|.)", rep, s)


def describe(settings: Settings) -> List[Tuple[str, str]]:
    rows = [("fail_on", settings.fail_on.label), ("block_on", settings.block_on.label)]
    if settings.ignore:
        rows.append(("ignore", ", ".join(settings.ignore)))
    if settings.test_globs:
        rows.append(("test_globs", ", ".join(settings.test_globs)))
    for rule, level in sorted(settings.rules.items()):
        rows.append(("rules." + rule, level.label))
    return rows
