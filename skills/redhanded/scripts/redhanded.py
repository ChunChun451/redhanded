#!/usr/bin/env python3
"""Run redhanded straight from this folder: no installation, no dependencies, Python 3.9+."""

import os
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("redhanded needs Python 3.9 or newer (found %d.%d)\n" % sys.version_info[:2])
    sys.exit(0 if (len(sys.argv) > 1 and sys.argv[1] == "hook") else 2)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from redhanded.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
