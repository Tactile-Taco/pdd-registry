"""conftest for the transcript-pipeline implementations.

Makes every python-stdlib implementation dir importable under pytest when
running `pytest implementations/` from the repo root (as the Makefile does).
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

for _entry in sorted(os.listdir(_HERE)):
    _p = os.path.join(_HERE, _entry, "python-stdlib")
    if os.path.isdir(_p):
        sys.path.insert(0, _p)
