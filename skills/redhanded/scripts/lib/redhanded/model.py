"""Core data model: severities, diff structures, findings and rule metadata."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional, Set


class Severity(IntEnum):
    OFF = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @classmethod
    def parse(cls, value: object) -> "Severity":
        if isinstance(value, Severity):
            return value
        text = str(value).strip().lower()
        aliases = {
            "off": cls.OFF, "none": cls.OFF, "never": cls.OFF, "disable": cls.OFF, "disabled": cls.OFF,
            "false": cls.OFF, "0": cls.OFF,
            "low": cls.LOW, "info": cls.LOW, "note": cls.LOW, "1": cls.LOW,
            "medium": cls.MEDIUM, "warn": cls.MEDIUM, "warning": cls.MEDIUM, "2": cls.MEDIUM,
            "high": cls.HIGH, "error": cls.HIGH, "3": cls.HIGH,
        }
        if text in aliases:
            return aliases[text]
        raise ValueError("unknown severity %r (use off, low, medium or high)" % (value,))

    @property
    def label(self) -> str:
        return self.name.lower()

    def downgraded(self) -> "Severity":
        """One step less severe, never below LOW (OFF stays OFF)."""
        if self <= Severity.LOW:
            return self
        return Severity(int(self) - 1)


@dataclass
class Line:
    op: str  # "+" added, "-" removed, " " context
    text: str
    old_no: Optional[int] = None
    new_no: Optional[int] = None


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: List[Line] = field(default_factory=list)


@dataclass
class FileInfo:
    lang: str = "other"
    is_test: bool = False
    is_test_support: bool = False
    is_snapshot: bool = False
    is_doc: bool = False
    is_generated: bool = False
    config: Set[str] = field(default_factory=set)

    @property
    def is_code(self) -> bool:
        return self.lang in CODE_LANGS

    @property
    def is_source(self) -> bool:
        """Production code: a code file that is not a test, config, doc or generated file."""
        return (
            self.is_code
            and not self.is_test
            and not self.is_snapshot
            and not self.is_doc
            and not self.is_generated
            and not self.config
        )


CODE_LANGS = {
    "js", "py", "go", "rust", "java", "scala", "csharp", "ruby", "php", "swift", "dart",
    "elixir", "cpp", "shell", "lua", "other-code",
}


@dataclass
class FileChange:
    """One file's change, from git or reconstructed from an agent transcript."""

    path: str
    status: str = "M"  # A added, M modified, D deleted, R renamed
    old_path: Optional[str] = None
    hunks: List[Hunk] = field(default_factory=list)
    old_text: Optional[str] = None
    new_text: Optional[str] = None
    binary: bool = False
    origin: str = "git"
    info: FileInfo = field(default_factory=FileInfo)

    def added(self) -> List[Line]:
        return [ln for h in self.hunks for ln in h.lines if ln.op == "+"]

    def removed(self) -> List[Line]:
        return [ln for h in self.hunks for ln in h.lines if ln.op == "-"]

    def new_lines(self) -> List[str]:
        return (self.new_text or "").splitlines()

    def old_lines(self) -> List[str]:
        return (self.old_text or "").splitlines()


_WS = re.compile(r"\s+")


def normalize_code(text: str) -> str:
    return _WS.sub(" ", text.strip())


@dataclass
class Finding:
    rule: str
    severity: Severity
    message: str
    path: Optional[str] = None
    line: Optional[int] = None
    snippet: Optional[str] = None
    detail: Optional[str] = None
    evidence: List[str] = field(default_factory=list)
    before: Optional[str] = None  # the line this replaced, when relevant
    allowed_by: Optional[str] = None  # why the severity was lowered (user intent, PR note)
    original_severity: Optional[Severity] = None
    session: Optional[str] = None
    timestamp: Optional[str] = None
    foreign: bool = False  # in a file this session's recorded edits and commands never touched

    @property
    def fingerprint(self) -> str:
        basis = "|".join([self.rule, self.path or "", normalize_code(self.snippet or self.message)])
        return hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:16]

    @property
    def location(self) -> str:
        if not self.path:
            return ""
        return "%s:%d" % (self.path, self.line) if self.line else self.path

    def lowered(self, reason: str, to: Optional[Severity] = None) -> "Finding":
        if self.original_severity is None:
            self.original_severity = self.severity
        self.severity = to if to is not None else Severity.LOW
        self.allowed_by = reason
        return self

    def to_dict(self) -> Dict[str, object]:
        out: Dict[str, object] = {
            "rule": self.rule,
            "severity": self.severity.label,
            "message": self.message,
            "fingerprint": self.fingerprint,
        }
        for key in ("path", "line", "snippet", "before", "detail", "allowed_by", "session", "timestamp"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.evidence:
            out["evidence"] = list(self.evidence)
        if self.original_severity is not None:
            out["original_severity"] = self.original_severity.label
        if self.foreign:
            out["foreign"] = True
        return out


@dataclass
class RuleMeta:
    id: str
    title: str
    severity: Severity
    category: str  # tests | implementation | config | session
    summary: str
    why: str
    fix: str
    intent: Optional[str] = None  # user-intent key that may legitimately request this change


@dataclass
class Report:
    findings: List[Finding]
    scope: str = ""
    files_checked: int = 0
    notes: List[str] = field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        c = {"high": 0, "medium": 0, "low": 0}
        for f in self.findings:
            if f.severity >= Severity.LOW:
                c[f.severity.label] += 1
        return c

    def max_severity(self) -> Severity:
        return max((f.severity for f in self.findings), default=Severity.OFF)

    @property
    def verdict(self) -> str:
        top = self.max_severity()
        if top >= Severity.HIGH:
            return "suspicious"
        if top >= Severity.MEDIUM:
            return "review"
        return "clean"
