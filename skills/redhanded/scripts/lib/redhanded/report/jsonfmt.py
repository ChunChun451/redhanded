"""Machine-readable JSON output."""

from __future__ import annotations

import json

from .. import __version__
from ..model import Report


def as_dict(report: Report) -> dict:
    return {
        "tool": "redhanded",
        "version": __version__,
        "scope": report.scope,
        "verdict": report.verdict,
        "counts": report.counts(),
        "files_checked": report.files_checked,
        "findings": [f.to_dict() for f in report.findings],
        "notes": list(report.notes),
    }


def render_json(report: Report, **_opts) -> str:
    return json.dumps(as_dict(report), indent=2, ensure_ascii=False) + "\n"
