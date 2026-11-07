"""Regenerate the rule reference from the rule definitions: python tools/gen_docs.py"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "redhanded", "scripts", "lib"))

from redhanded.docs import rules_markdown  # noqa: E402

TARGETS = ("docs/rules.md", "skills/redhanded/references/rules.md")


def main() -> int:
    text = rules_markdown()
    for rel in TARGETS:
        path = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("wrote", rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
