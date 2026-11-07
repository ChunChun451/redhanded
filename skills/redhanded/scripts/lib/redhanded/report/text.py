"""Human-readable terminal output."""

from __future__ import annotations

import textwrap
from typing import List, Optional

from .. import __version__
from ..model import Finding, Report, Severity
from ..rules import BY_ID
from ..term import Style

SEV_STYLE = {
    Severity.HIGH: ("HIGH", ("bold", "red")),
    Severity.MEDIUM: ("MED ", ("bold", "yellow")),
    Severity.LOW: ("LOW ", ("bold", "blue")),
}
VERDICT_STYLE = {
    "suspicious": ("SUSPICIOUS", ("bold", "white", "bg_red")),
    "review": ("REVIEW", ("bold", "black", "bg_yellow")),
    "clean": ("CLEAN", ("bold", "black", "bg_green")),
}


def _clip(text: str, width: int) -> str:
    text = text.rstrip()
    return text if len(text) <= width else text[: width - 1] + "…"


def finding_block(f: Finding, s: Style, width: int = 100, verbose: bool = False) -> List[str]:
    label, names = SEV_STYLE.get(f.severity, ("LOW ", ("bold",)))
    bar = s.sym("│", "|")
    dot = s.sym("·", "-")
    where = f.location or (f.session and "session %s" % f.session[:8]) or ""
    head = "  %s  %s" % (s(label, *names), s(f.rule, "bold"))
    if where:
        head += " %s %s" % (s(dot, "grey"), s(where, "cyan"))
    lines = [head]
    for chunk in textwrap.wrap(f.message, max(40, width - 8)) or [""]:
        lines.append("        " + chunk)
    if f.allowed_by:
        orig = f.original_severity.label if f.original_severity else ""
        lines.append("        " + s("lowered from %s: %s" % (orig, _clip(f.allowed_by, width - 30)), "grey", "italic"))
    if f.foreign:
        lines.append("        " + s("not touched by this session's recorded edits or commands", "grey", "italic"))
    if f.before:
        lines.append("        %s %s" % (s(bar, "grey"), s(_clip("- " + f.before, width - 12), "red")))
    if f.snippet:
        snippet = f.snippet
        color = "green" if snippet.startswith("+") else "red" if snippet.startswith("-") else "grey"
        lines.append("        %s %s" % (s(bar, "grey"), s(_clip(snippet, width - 12), color)))
    if f.detail and (verbose or f.severity >= Severity.MEDIUM):
        for chunk in textwrap.wrap(f.detail, max(40, width - 10)):
            lines.append("        " + s(chunk, "grey"))
    if verbose and f.evidence:
        for ev in f.evidence[:5]:
            lines.append("        " + s("evidence: " + _clip(ev, width - 20), "grey"))
    return lines


def render_text(report: Report, style: Optional[Style] = None, verbose: bool = False, width: int = 100,
                show_low: bool = True, title: Optional[str] = None) -> str:
    s = style or Style(False, True)
    dot = s.sym("·", "-")
    out: List[str] = []
    files = "%d file%s" % (report.files_checked, "" if report.files_checked == 1 else "s")
    header = "%s %s %s" % (s("redhanded", "bold", "red"), s(__version__, "grey"), s(dot, "grey"))
    header += " " + (title or report.scope or "check") + " %s %s" % (s(dot, "grey"), files)
    out.append(header)
    shown = [f for f in report.findings if show_low or f.severity >= Severity.MEDIUM]
    if shown:
        out.append("")
    for f in shown:
        out.extend(finding_block(f, s, width, verbose))
        out.append("")
    hidden = len(report.findings) - len(shown)
    for note in report.notes:
        out.append(s("  note: " + note, "grey"))
    if report.notes:
        out.append("")
    counts = report.counts()
    text, names = VERDICT_STYLE[report.verdict]
    rule = s.sym("─", "-") * min(width, 60)
    out.append(s(rule, "grey"))
    summary = "%d high %s %d medium %s %d low" % (counts["high"], dot, counts["medium"], dot, counts["low"])
    if hidden:
        summary += " (%d low hidden)" % hidden
    out.append(" %s  %s" % (s(" %s " % text, *names), summary))
    if report.verdict == "clean" and not report.findings:
        out.append(s("  Nothing looks like gaming the checks. That is not a proof the code is correct.", "grey"))
    elif report.findings:
        out.append(s("  Fix the code, not the checks. `redhanded explain <rule>` shows why each rule matters.", "grey"))
        out.append(s("  Intended? Mark the line with `redhanded-ignore[<rule>]`, or say so in the PR description.", "grey"))
    return "\n".join(out) + "\n"


def render_rules(style: Optional[Style] = None) -> str:
    from ..rules import RULES

    s = style or Style(False, True)
    out = []
    current = None
    for r in RULES:
        if r.category != current:
            current = r.category
            out.append("")
            out.append(s(current.upper(), "bold"))
        label, names = SEV_STYLE[r.severity]
        out.append("  %s  %-30s %s" % (s(label, *names), r.id, r.title))
    return "\n".join(out).lstrip("\n") + "\n"


def render_explain(rule_id: str, style: Optional[Style] = None) -> str:
    s = style or Style(False, True)
    r = BY_ID.get(rule_id)
    if r is None:
        raise KeyError(rule_id)
    label, names = SEV_STYLE[r.severity]
    out = [
        "%s  %s  %s" % (s(label, *names), s(r.id, "bold"), s("(" + r.category + ")", "grey")),
        s(r.title, "bold"),
        "",
        "What it flags:  " + r.summary,
        "Why it matters: " + r.why,
        "What to do:     " + r.fix,
        "",
        s("Silence one line:  add a comment containing  redhanded-ignore[%s]" % r.id, "grey"),
        s("Change severity:   [rules] %s = \"off\" | \"low\" | \"medium\" | \"high\"  in .redhanded.toml" % r.id, "grey"),
    ]
    if r.intent:
        out.append(s("Lowered automatically when the user's request asks for this kind of change.", "grey"))
    return "\n".join(out) + "\n"
