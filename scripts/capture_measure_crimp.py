#!/usr/bin/env python3
"""Deprecated — use capture_measure_welding_splice.py instead."""

import runpy
import sys
from pathlib import Path

_TARGET = Path(__file__).resolve().parent / "capture_measure_welding_splice.py"
sys.argv[0] = str(_TARGET)
runpy.run_path(str(_TARGET), run_name="__main__")
