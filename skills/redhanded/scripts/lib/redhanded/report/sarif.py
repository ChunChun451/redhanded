"""SARIF 2.1.0 output for GitHub code scanning and other SARIF viewers."""

from __future__ import annotations

import json

from .. import __version__
from ..model import Report, Severity
from ..rules import RULES

HOME = "https://github.com/ChunChun451/redhanded"
LEVEL = {Severity.HIGH: "error", Severity.MEDIUM: "warning", Severity.LOW: "note"}
SCORE = {Severity.HIGH: "7.5", Severity.MEDIUM: "5.0", Severity.LOW: "2.0"}


def _rule(r) -> dict:
    return {
        "id": r.id,
        "name": "".join(part.capitalize() for part in r.id.split("-")),
        "shortDescription": {"text": r.title},
        "fullDescription": {"text": r.summary},
        "help": {
            "text": "%s\n\nWhy it matters: %s\n\nWhat to do: %s" % (r.summary, r.why, r.fix),
            "markdown": "**%s**\n\n%s\n\n**Why it matters:** %s\n\n**What to do:** %s" % (r.title, r.summary, r.why, r.fix),
        },
        "helpUri": "%s/blob/main/docs/rules.md#%s" % (HOME, r.id),
        "defaultConfiguration": {"level": LEVEL[r.severity]},
        "properties": {"tags": ["redhanded", r.category], "precision": "high" if r.severity >= Severity.HIGH else "medium"},
    }


def render_sarif(report: Report, **_opts) -> str:
    rule_index = {r.id: i for i, r in enumerate(RULES)}
    results = []
    for f in report.findings:
        result = {
            "ruleId": f.rule,
            "ruleIndex": rule_index.get(f.rule, 0),
            "level": LEVEL.get(f.severity, "note"),
            "message": {"text": f.message + (" (lowered: %s)" % f.allowed_by if f.allowed_by else "")},
            "partialFingerprints": {"redhanded/v1": f.fingerprint},
            "properties": {"severity": f.severity.label},
        }
        if f.path:
            region = {"startLine": max(1, f.line or 1)}
            if f.snippet:
                region["snippet"] = {"text": f.snippet}
            result["locations"] = [{
                "physicalLocation": {
                    "artifactLocation": {"uri": f.path, "uriBaseId": "%SRCROOT%"},
                    "region": region,
                }
            }]
        results.append(result)
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "redhanded",
                "version": __version__,
                "semanticVersion": __version__,
                "informationUri": HOME,
                "rules": [_rule(r) for r in RULES],
            }},
            "automationDetails": {"id": "redhanded/"},
            "results": results,
        }],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
