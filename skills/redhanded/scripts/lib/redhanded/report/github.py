"""GitHub Actions workflow commands (::error / ::warning / ::notice annotations)."""

from __future__ import annotations

from typing import List

from ..model import Report, Severity

COMMAND = {Severity.HIGH: "error", Severity.MEDIUM: "warning", Severity.LOW: "notice"}


def _data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _prop(text: str) -> str:
    return _data(text).replace(":", "%3A").replace(",", "%2C")


def render_annotations(report: Report, limit: int = 50, **_opts) -> str:
    out: List[str] = []
    for f in report.findings[:limit]:
        props = ["title=" + _prop("redhanded: %s" % f.rule)]
        if f.path:
            props.insert(0, "file=" + _prop(f.path))
            if f.line:
                props.insert(1, "line=%d" % f.line)
        message = f.message + (" (lowered: %s)" % f.allowed_by if f.allowed_by else "")
        out.append("::%s %s::%s" % (COMMAND.get(f.severity, "notice"), ",".join(props), _data(message)))
    return "\n".join(out) + ("\n" if out else "")
