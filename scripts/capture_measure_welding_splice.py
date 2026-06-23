#!/usr/bin/env python3
"""
Capture one frame and measure welding splice only.

Output folder (default):
  backend/storage/Measurement/session_YYYYMMDD_HHMMSS/
    capture.png
    capture_measured.png
    capture_mask_overlay.png
    capture_mask_welding_splice.png
    capture_measurement.json

Examples:
  python3 scripts/capture_measure_welding_splice.py
  python3 scripts/capture_measure_welding_splice.py --calibration backend/storage/Calibration/session_…
  python3 scripts/capture_measure_welding_splice.py --image path/to.png
  python3 scripts/capture_measure_welding_splice.py --local-camera

Legacy entry point (deprecated):
  python3 scripts/capture_measure_crimp.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from capture_common import (  # noqa: E402
    _BACKEND,
    add_capture_cli,
    acquire_frame,
    merge_capture_json,
    print_artifacts,
    resolve_output_dir,
)
from measure_welding_splice_sleeve import (  # noqa: E402
    load_px_per_mm,
    measure_welding_splice,
    print_welding_splice_result,
    save_welding_splice_artifacts,
)

sys.path.insert(0, str(_BACKEND))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture + measure welding splice + save masks"
    )
    add_capture_cli(parser)
    args = parser.parse_args()

    out_dir = resolve_output_dir(args)
    cal_dir = Path(args.calibration).resolve() if args.calibration else None
    px_per_mm, px_src = load_px_per_mm(cal_dir, args.px_per_mm)

    img, image_label, stem, capture_meta = acquire_frame(args, out_dir)
    print(f"Output: {out_dir}")

    result = measure_welding_splice(img, image_label, px_per_mm, px_src)
    if args.note:
        result.errors.append(f"note: {args.note}")
    print_welding_splice_result(result)

    files = save_welding_splice_artifacts(img, result, out_dir, stem=stem)
    merge_capture_json(Path(files["measurement_json"]), capture_meta, args.note)
    print_artifacts(
        files,
        "Tune copper HSV thresholds in measure_welding_splice_sleeve.py (_copper_mask).",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
