"""
Deprecated import path — use measure_welding_splice_sleeve instead.

Legacy names are re-exported for older scripts only.
"""

from measure_welding_splice_sleeve import *  # noqa: F401,F403
from measure_welding_splice_sleeve import (  # noqa: F401
    detect_welding_splice_mask as detect_crimp_mask,
    measure_welding_splice as measure_crimp,
    print_welding_splice_result as print_crimp_result,
    save_welding_splice_artifacts as save_crimp_artifacts,
    _detect_welding_splice_contour as _detect_crimp_contour,
)
