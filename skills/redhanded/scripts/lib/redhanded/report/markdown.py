"""Markdown output for PR comments, job summaries and agent replies."""

from __future__ import annotations

from typing import List

from .. import __version__
from ..model import Report, Severity

ICON = {Severity.HIGH: "🔴", Severity.MEDIUM: "🟠", Severity.LOW: "🔵"}
HEADLINE = {
    "suspicious": "🚩 redhanded: this change games the checks",
    "review": "⚠️ redhanded: worth a careful look",
    "clean": "✅ redhanded: nothing looks gamed",
}


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _code(text: str) -> str:
    text = (text or "").replace("\n", " ")
    ticks = "``" if "`" in text else "`"
    pad = " " if ticks == "``" else ""
    return "%s%s%s%s%s" % (ticks, pad, text, pad, ticks)


def render_markdown(report: Report, details: bool = True, max_rows: int = 50, **_opts) -> str:
    c = report.counts()
    out: List[str] = ["### " + HEADLINE[report.verdict], ""]
    out.append("**%d high · %d medium · %d low** across %d file%s (%s)." % (
        c["high"], c["medium"], c["low"], report.files_checked, "" if report.files_checked == 1 else "s",
        report.scope or "diff"))
    out.append("")
    if report.findings:
        out.append("| | Rule | Where | What happened |")
        out.append("|---|---|---|---|")
        for f in report.findings[:max_rows]:
            msg = _cell(f.message)
            if f.allowed_by:
                msg += " _(lowered: %s)_" % _cell(f.allowed_by)
            out.append("| %s | `%s` | %s | %s |" % (ICON.get(f.severity, ""), f.rule,
                                                   _code(f.location) if f.location else "session", msg))
        if len(report.findings) > max_rows:
            out.append("")
            out.append("_…and %d more._" % (len(report.findings) - max_rows))
        if details:
            snippets = [f for f in report.findings[:max_rows] if f.snippet or f.before]
            if snippets:
                out.append("")
                out.append("<details><summary>Changed lines</summary>")
                out.append("")
                out.append("```diff")
                for f in snippets:
                    out.append("# %s %s" % (f.rule, f.location))
                    if f.before:
                        out.append("- " + f.before)
                    if f.snippet:
                        out.append(f.snippet if f.snippet[:1] in "+- " else "  " + f.snippet)
                out.append("```")
                out.append("")
                out.append("</details>")
        out.append("")
        out.append("Fix the code rather than the checks. If a change is intended, mark the line with "
                   "`redhanded-ignore[<rule>]` or add `redhanded-allow: <rule> <reason>` to the PR description.")
    for note in report.notes:
        out.append("")
        out.append("> " + _cell(note))
    out.append("")
    out.append("<sub>[redhanded](https://github.com/ChunChun451/redhanded) %s</sub>" % __version__)
    return "\n".join(out) + "\n"
