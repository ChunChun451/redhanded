"""Output formats for a Report."""

from __future__ import annotations

from ..model import Report

FORMATS = ("text", "json", "sarif", "markdown", "github")


def render(report: Report, fmt: str, **opts) -> str:
    if fmt == "text":
        from .text import render_text as fn
    elif fmt == "json":
        from .jsonfmt import render_json as fn
    elif fmt == "sarif":
        from .sarif import render_sarif as fn
    elif fmt in ("markdown", "md"):
        from .markdown import render_markdown as fn
    elif fmt == "github":
        from .github import render_annotations as fn
    else:
        raise ValueError("unknown format %r (choose from %s)" % (fmt, ", ".join(FORMATS)))
    return fn(report, **opts)
