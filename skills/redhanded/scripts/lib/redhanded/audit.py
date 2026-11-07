"""Audit past Claude Code sessions: how often did the agent game the checks or overclaim?"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import __version__, transcript
from .model import Finding, Report, Severity
from .rules import BY_ID
from .session_check import check_session
from .settings import Settings
from .term import style_for


@dataclass
class Result:
    path: str
    session: transcript.Session
    report: Report

    @property
    def project(self) -> str:
        cwd = self.session.cwd or ""
        return os.path.basename(cwd.rstrip("/\\")) or "?"

    @property
    def date(self) -> str:
        return (self.session.started or "")[:10]

    @property
    def score(self) -> int:
        c = self.report.counts()
        return c["high"] * 10 + c["medium"] * 3 + c["low"]

    def prompt(self, limit: int = 90) -> str:
        p = " ".join((self.session.prompts[0] if self.session.prompts else "").split())
        return p if len(p) <= limit else p[: limit - 1] + "…"


def collect(paths: List[str], progress: bool = False) -> List[Result]:
    results: List[Result] = []
    total = len(paths)
    for i, path in enumerate(paths, 1):
        if progress:
            sys.stderr.write("\rredhanded: scanning session %d/%d" % (i, total))
            sys.stderr.flush()
        try:
            session = transcript.load_session(path)
        except Exception:
            continue
        if not session.turns:
            continue
        try:
            report = check_session(session, root=session.cwd if session.cwd and os.path.isdir(session.cwd) else None,
                                   settings=None if session.cwd and os.path.isdir(session.cwd) else Settings())
        except Exception as exc:  # one odd transcript must not stop the audit
            report = Report(findings=[], scope="error", notes=["could not analyse: %s" % exc])
        results.append(Result(path, session, report))
    if progress:
        sys.stderr.write("\r" + " " * 60 + "\r")
    return results


def summarize(results: List[Result]) -> Dict[str, object]:
    with_changes = [r for r in results if r.session.edits]
    high = [r for r in results if r.report.max_severity() >= Severity.HIGH]
    medium = [r for r in results if r.report.max_severity() == Severity.MEDIUM]
    by_rule: Counter = Counter()
    sessions_by_rule: Counter = Counter()
    for r in results:
        rules = set()
        for f in r.report.findings:
            if f.severity >= Severity.MEDIUM:
                by_rule[f.rule] += 1
                rules.add(f.rule)
        sessions_by_rule.update(rules)
    claims = sum(1 for r in results for f in r.report.findings if f.rule in ("unverified-claim", "contradicted-claim"))
    return {
        "sessions": len(results),
        "with_changes": len(with_changes),
        "flagged_high": len(high),
        "flagged_medium_only": len(medium),
        "clean": len(results) - len(high) - len(medium),
        "projects": len({r.session.cwd for r in results}),
        "findings_by_rule": dict(by_rule.most_common()),
        "sessions_by_rule": dict(sessions_by_rule.most_common()),
        "overclaims": claims,
        "test_runs": sum(len(r.session.runs) for r in results),
    }


def _pct(n: int, d: int) -> str:
    return "%d%%" % round(100.0 * n / d) if d else "-"


def _count(n: object, noun: str) -> str:
    return "%d %s%s" % (int(n), noun, "" if int(n) == 1 else "s")


def render_text(results: List[Result], summary: Dict[str, object], days: Optional[int], redact: bool,
                color: Optional[bool] = None, verbose: bool = False) -> str:
    s = style_for(sys.stdout, color=color)
    dot = s.sym("·", "-")
    bar_ch = s.sym("█", "#")
    out = []
    window = "the last %d days" % days if days else "all time"
    out.append("%s %s %s %s over %s %s %s" % (
        s("redhanded audit", "bold", "red"), s(__version__, "grey"), dot, _count(summary["sessions"], "session"), window, dot,
        _count(summary["projects"], "project")))
    out.append("")
    n = int(summary["sessions"]) or 1
    rows = [
        ("Sessions with code changes", summary["with_changes"], ""),
        ("Flagged high", summary["flagged_high"], _pct(int(summary["flagged_high"]), n)),
        ("Flagged medium only", summary["flagged_medium_only"], _pct(int(summary["flagged_medium_only"]), n)),
        ("Clean", summary["clean"], _pct(int(summary["clean"]), n)),
        ("Verification commands seen", summary["test_runs"], ""),
    ]
    for label, value, pct in rows:
        out.append("  %-30s %5s  %s" % (label, value, s(pct, "grey")))
    by_rule: Dict[str, int] = summary["sessions_by_rule"]  # type: ignore[assignment]
    if by_rule:
        out.append("")
        out.append("  %-30s %s" % (s("Sessions affected, by rule", "bold"), ""))
        top = max(by_rule.values())
        for rule, count in by_rule.items():
            sev = BY_ID[rule].severity if rule in BY_ID else Severity.LOW
            color_name = "red" if sev >= Severity.HIGH else "yellow" if sev >= Severity.MEDIUM else "blue"
            bar = bar_ch * max(1, round(20.0 * count / top))
            out.append("  %-30s %5d  %s" % (rule, count, s(bar, color_name)))
    worst = sorted([r for r in results if r.score > 0], key=lambda r: (-r.score, r.date))[:10]
    if worst:
        out.append("")
        out.append("  " + s("Sessions to look at", "bold"))
        for r in worst:
            c = r.report.counts()
            head = "  %s  %-14s %s high %s %s medium" % (r.date, r.project[:14], c["high"], dot, c["medium"])
            if not redact and r.prompt():
                head += "  " + s('"%s"' % r.prompt(60), "grey")
            out.append(head)
            for f in [f for f in r.report.findings if f.severity >= Severity.MEDIUM][: (6 if verbose else 3)]:
                where = (" " + f.location) if f.location and not redact else ""
                label = "HIGH" if f.severity >= Severity.HIGH else "MED "
                msg = f.message if not redact else BY_ID[f.rule].title if f.rule in BY_ID else f.rule
                msg = msg if len(msg) <= 110 else msg[:109] + "…"
                out.append("      %s %s%s  %s" % (s(label, "bold", "red" if label == "HIGH" else "yellow"), f.rule, where, msg))
            out.append("      " + s("redhanded session --id %s" % r.session.id, "grey"))
    out.append("")
    if not results:
        out.append("  No Claude Code sessions found in this window.")
    return "\n".join(out) + "\n"


def to_json(results: List[Result], summary: Dict[str, object], redact: bool) -> str:
    sessions = []
    for r in sorted(results, key=lambda x: (-x.score, x.date)):
        findings = []
        for f in r.report.findings:
            d = f.to_dict()
            if redact:
                for k in ("snippet", "before", "evidence", "detail"):
                    d.pop(k, None)
                d["message"] = BY_ID[f.rule].title if f.rule in BY_ID else f.rule
            findings.append(d)
        item = {
            "id": r.session.id,
            "project": r.project,
            "started": r.session.started,
            "verdict": r.report.verdict,
            "counts": r.report.counts(),
            "edits": len(r.session.edits),
            "verification_runs": len(r.session.runs),
            "findings": findings,
        }
        if not redact:
            item["cwd"] = r.session.cwd
            item["transcript"] = r.path
            item["first_prompt"] = r.prompt(200)
        sessions.append(item)
    return json.dumps({"tool": "redhanded", "version": __version__, "summary": summary, "sessions": sessions},
                      indent=2, ensure_ascii=False) + "\n"


def to_markdown(results: List[Result], summary: Dict[str, object], days: Optional[int], redact: bool) -> str:
    n = int(summary["sessions"]) or 1
    out = ["## redhanded audit", "",
           "%s (%s) across %s." % (_count(summary["sessions"], "Claude Code session"),
                                   "last %d days" % days if days else "all time", _count(summary["projects"], "project")), "",
           "| | Sessions | Share |", "|---|---:|---:|",
           "| With code changes | %s | %s |" % (summary["with_changes"], _pct(int(summary["with_changes"]), n)),
           "| Flagged high | %s | %s |" % (summary["flagged_high"], _pct(int(summary["flagged_high"]), n)),
           "| Flagged medium only | %s | %s |" % (summary["flagged_medium_only"], _pct(int(summary["flagged_medium_only"]), n)),
           "| Clean | %s | %s |" % (summary["clean"], _pct(int(summary["clean"]), n)), ""]
    by_rule: Dict[str, int] = summary["sessions_by_rule"]  # type: ignore[assignment]
    if by_rule:
        out += ["| Rule | Sessions |", "|---|---:|"]
        out += ["| `%s` | %d |" % (k, v) for k, v in by_rule.items()]
        out.append("")
    return "\n".join(out) + "\n"


HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>redhanded audit</title>
<style>
:root{--bg:#fbfaf8;--fg:#1d1b19;--muted:#6f6a64;--card:#ffffff;--line:#e8e4de;--high:#c62828;--med:#c77700;--low:#2f6fb3;--ok:#2e7d32}
@media (prefers-color-scheme:dark){:root{--bg:#141312;--fg:#ece8e3;--muted:#9a948d;--card:#1d1b19;--line:#2e2b28;--high:#ef5350;--med:#ffb74d;--low:#64b5f6;--ok:#81c784}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 64px}h1{font-size:26px;margin:0 0 4px}h1 b{color:var(--high)}
.sub{color:var(--muted);margin:0 0 28px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:28px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.card .n{font-size:28px;font-weight:700}
.card .l{color:var(--muted);font-size:13px}.high{color:var(--high)}.med{color:var(--med)}.ok{color:var(--ok)}
h2{font-size:17px;margin:28px 0 12px}.bars{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 16px}
.bar{display:grid;grid-template-columns:minmax(150px,240px) 1fr 40px;gap:12px;align-items:center;padding:6px 0;border-bottom:1px solid var(--line)}
.bar:last-child{border-bottom:0}.bar code{font-size:13px}.track{height:10px;background:var(--line);border-radius:5px;overflow:hidden}
.fill{height:100%;border-radius:5px}.num{text-align:right;font-variant-numeric:tabular-nums}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0;padding:10px 16px}
summary{cursor:pointer;display:flex;gap:12px;flex-wrap:wrap;align-items:baseline}summary .d{color:var(--muted);font-variant-numeric:tabular-nums}
.pill{font-size:12px;font-weight:700;border-radius:999px;padding:1px 8px;color:#fff}.pill.h{background:var(--high)}.pill.m{background:var(--med)}.pill.l{background:var(--low)}
.f{border-top:1px solid var(--line);padding:8px 0}.f .r{font-weight:600}.f .w{color:var(--muted);font-size:13px}
pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:12.5px;margin:6px 0 0}
.prompt{color:var(--muted);font-style:italic}footer{margin-top:40px;color:var(--muted);font-size:13px}a{color:inherit}
</style></head><body><main>
<h1><b>redhanded</b> audit</h1>
<p class="sub">{{window}} &middot; {{sessions}} sessions &middot; {{projects}} projects &middot; generated {{when}}</p>
<div class="cards">
<div class="card"><div class="n">{{with_changes}}</div><div class="l">sessions with code changes</div></div>
<div class="card"><div class="n high">{{flagged_high}}</div><div class="l">flagged high ({{high_pct}})</div></div>
<div class="card"><div class="n med">{{flagged_medium_only}}</div><div class="l">medium only ({{med_pct}})</div></div>
<div class="card"><div class="n ok">{{clean}}</div><div class="l">clean ({{clean_pct}})</div></div>
</div>
{{bars}}
{{sessions_html}}
<footer>Generated locally by redhanded {{version}}. Nothing left this machine. Findings are leads for review, not verdicts.</footer>
</main></body></html>
"""


def to_html(results: List[Result], summary: Dict[str, object], days: Optional[int], redact: bool) -> str:
    esc = html.escape
    n = int(summary["sessions"]) or 1
    by_rule: Dict[str, int] = summary["sessions_by_rule"]  # type: ignore[assignment]
    bars = ""
    if by_rule:
        top = max(by_rule.values())
        rows = []
        for rule, count in by_rule.items():
            sev = BY_ID[rule].severity if rule in BY_ID else Severity.LOW
            color = "var(--high)" if sev >= Severity.HIGH else "var(--med)" if sev >= Severity.MEDIUM else "var(--low)"
            rows.append('<div class="bar"><code>%s</code><div class="track"><div class="fill" style="width:%.1f%%;background:%s">'
                        '</div></div><div class="num">%d</div></div>' % (esc(rule), 100.0 * count / top, color, count))
        bars = '<h2>Sessions affected, by rule</h2><div class="bars">%s</div>' % "".join(rows)
    items = []
    for r in sorted([r for r in results if r.score > 0], key=lambda x: (-x.score, x.date))[:200]:
        c = r.report.counts()
        pills = "".join('<span class="pill %s">%d %s</span>' % (cls, c[k], k) for k, cls in (("high", "h"), ("medium", "m"), ("low", "l")) if c[k])
        prompt = "" if redact else '<span class="prompt">%s</span>' % esc(r.prompt(100))
        fs = []
        for f in r.report.findings:
            msg = esc(BY_ID[f.rule].title if (redact and f.rule in BY_ID) else f.message)
            where = "" if (redact or not f.location) else '<div class="w">%s</div>' % esc(f.location)
            code = ""
            if not redact and (f.snippet or f.before):
                code = "<pre>%s</pre>" % esc("\n".join(x for x in (("- " + f.before) if f.before else "", f.snippet or "") if x))
            fs.append('<div class="f"><span class="r">%s</span> <span class="w">(%s)</span><div>%s</div>%s%s</div>' % (
                esc(f.rule), f.severity.label, msg, where, code))
        items.append('<details><summary><span class="d">%s</span><b>%s</b>%s%s</summary>%s'
                     '<div class="w" style="margin-top:6px">session %s</div></details>' % (
                         esc(r.date), esc(r.project), pills, prompt, "".join(fs), esc(r.session.id)))
    sessions_html = ('<h2>Sessions to look at</h2>' + "".join(items)) if items else "<h2>No findings</h2><p>Nothing looked gamed.</p>"
    values = {
        "window": "last %d days" % days if days else "all time",
        "sessions": summary["sessions"], "projects": summary["projects"], "when": time.strftime("%Y-%m-%d %H:%M"),
        "with_changes": summary["with_changes"], "flagged_high": summary["flagged_high"],
        "flagged_medium_only": summary["flagged_medium_only"], "clean": summary["clean"],
        "high_pct": _pct(int(summary["flagged_high"]), n), "med_pct": _pct(int(summary["flagged_medium_only"]), n),
        "clean_pct": _pct(int(summary["clean"]), n), "bars": bars, "sessions_html": sessions_html, "version": __version__,
    }
    # Plain placeholder substitution: the CSS is full of % signs.
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values[m.group(1)]), HTML_TEMPLATE)


def select_paths(project: Optional[str], days: Optional[int], limit: int = 0) -> List[str]:
    paths = transcript.transcripts_for(os.path.abspath(project) if project else None)
    if days:
        cutoff = time.time() - days * 86400
        paths = [p for p in paths if os.path.getmtime(p) >= cutoff]
    paths = sorted(paths, key=os.path.getmtime, reverse=True)
    if limit:
        paths = paths[:limit]
    return paths


def run_audit(args) -> int:
    days = None if args.all else args.days
    paths = select_paths(args.project, days, args.limit)
    progress = sys.stderr.isatty() if hasattr(sys.stderr, "isatty") else False
    results = collect(paths, progress=progress)
    summary = summarize(results)
    if args.format == "json":
        text = to_json(results, summary, args.redact)
    elif args.format == "html":
        text = to_html(results, summary, days, args.redact)
    elif args.format == "markdown":
        text = to_markdown(results, summary, days, args.redact)
    else:
        text = render_text(results, summary, days, args.redact, color=False if (args.no_color or args.output) else None,
                           verbose=args.verbose)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        sys.stderr.write("redhanded: wrote %s\n" % args.output)
    else:
        sys.stdout.write(text)
    return 0


def findings_of(results: List[Result], rule: str) -> List[Finding]:
    return [f for r in results for f in r.report.findings if f.rule == rule]
