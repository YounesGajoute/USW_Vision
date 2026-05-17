"""ROI coordinate helpers — wizard canvas (640×480) vs native master pixels."""

from typing import Dict, List, Optional, Tuple

WIZARD_CANVAS_W = 640
WIZARD_CANVAS_H = 480

# IMX296 / typical full-resolution master after register-master (portrait RGB).
REFERENCE_MASTER_W = 1456
REFERENCE_MASTER_H = 1088

ROI_SPACE_WIZARD = 'wizard_640x480'
ROI_SPACE_NORMALIZED = 'normalized_01'
ROI_SPACE_NATIVE = 'native'
VALID_TEMPLATE_ROI_SPACES = frozenset(
    {ROI_SPACE_WIZARD, ROI_SPACE_NORMALIZED, ROI_SPACE_NATIVE}
)


def clamp_roi(roi: Dict[str, float], max_w: int, max_h: int) -> Dict[str, int]:
    x = max(0, int(round(float(roi.get('x', 0)))))
    y = max(0, int(round(float(roi.get('y', 0)))))
    w = max(2, int(round(float(roi.get('width', 2)))))
    h = max(2, int(round(float(roi.get('height', 2)))))
    if x + w > max_w:
        w = max(2, max_w - x)
    if y + h > max_h:
        h = max(2, max_h - y)
    return {'x': x, 'y': y, 'width': w, 'height': h}


def rois_fit_wizard_canvas(tools: List[Dict]) -> bool:
    if not tools:
        return False
    for t in tools:
        roi = t.get('roi') or {}
        if not roi:
            return False
        x, y = float(roi.get('x', 0)), float(roi.get('y', 0))
        w, h = float(roi.get('width', 0)), float(roi.get('height', 0))
        if w < 1 or h < 1:
            return False
        if x < -0.5 or y < -0.5:
            return False
        if x + w > WIZARD_CANVAS_W + 0.5 or y + h > WIZARD_CANVAS_H + 0.5:
            return False
    return True


def rois_look_normalized_01(tools: List[Dict]) -> bool:
    if not tools:
        return False
    for t in tools:
        roi = t.get('roi') or {}
        w, h = float(roi.get('width', 0)), float(roi.get('height', 0))
        if w <= 0 or h <= 0:
            return False
        x, y = float(roi.get('x', 0)), float(roi.get('y', 0))
        if x < 0 or y < 0 or x > 1.001 or y > 1.001:
            return False
        if x + w > 1.001 or y + h > 1.001:
            return False
    return True


def infer_template_roi_space(tools: List[Dict]) -> str:
    """
    Detect how template tool ROIs were authored.

    Order: normalized fractions (0–1) → wizard 640×480 pixels → native master pixels.
    """
    if not tools:
        return ROI_SPACE_WIZARD
    if rois_look_normalized_01(tools):
        return ROI_SPACE_NORMALIZED
    if rois_fit_wizard_canvas(tools):
        return ROI_SPACE_WIZARD
    return ROI_SPACE_NATIVE


def validate_template_tools_roi(
    tools: List[Dict],
    *,
    roi_space: Optional[str] = None,
) -> str:
    """
    Ensure template ROIs are valid for the declared or inferred coordinate space.
    Returns the resolved roi_space string.
    """
    if not tools:
        raise ValueError('At least one tool is required')

    space = (roi_space or infer_template_roi_space(tools)).lower()
    if space not in VALID_TEMPLATE_ROI_SPACES:
        raise ValueError(
            f'Invalid roi_space {roi_space!r}; use one of: '
            + ', '.join(sorted(VALID_TEMPLATE_ROI_SPACES))
        )

    if space == ROI_SPACE_NORMALIZED:
        if not rois_look_normalized_01(tools):
            raise ValueError(
                'roi_space normalized_01 requires each tool ROI with '
                '0 <= x,y,width,height <= 1 and x+width, y+height <= 1'
            )
        return space

    if space == ROI_SPACE_WIZARD:
        for i, t in enumerate(tools):
            roi = t.get('roi') or {}
            x, y = float(roi.get('x', 0)), float(roi.get('y', 0))
            w, h = float(roi.get('width', 0)), float(roi.get('height', 0))
            if w < 2 or h < 2:
                raise ValueError(f'Tool {i}: ROI width/height must be at least 2 wizard pixels')
            if x < 0 or y < 0:
                raise ValueError(f'Tool {i}: ROI x/y must be non-negative')
            if x + w > WIZARD_CANVAS_W + 0.5 or y + h > WIZARD_CANVAS_H + 0.5:
                raise ValueError(
                    f'Tool {i}: ROI extends outside wizard canvas '
                    f'({WIZARD_CANVAS_W}×{WIZARD_CANVAS_H}); got x={x}, y={y}, '
                    f'w={w}, h={h}. Use native master pixels or normalized_01 instead.'
                )
        return space

    # native: only basic non-negative checks (program_manager already validates fields)
    for i, t in enumerate(tools):
        roi = t.get('roi') or {}
        for field in ('x', 'y', 'width', 'height'):
            v = roi.get(field)
            if v is None or float(v) < 0:
                raise ValueError(f'Tool {i}: ROI {field} must be a non-negative number')
        if float(roi.get('width', 0)) < 2 or float(roi.get('height', 0)) < 2:
            raise ValueError(f'Tool {i}: ROI width/height must be at least 2 pixels')
    return space


def wizard_to_master_scale_factors(master_width: int, master_height: int) -> Tuple[float, float]:
    """Stretch factors from wizard canvas to full-resolution master (same as Configure UI)."""
    mw, mh = int(master_width), int(master_height)
    if mw < 1 or mh < 1:
        raise ValueError('Master dimensions must be positive')
    return mw / float(WIZARD_CANVAS_W), mh / float(WIZARD_CANVAS_H)


def scale_wizard_roi_to_master(
    roi: Dict[str, float],
    master_width: int,
    master_height: int,
) -> Dict[str, int]:
    """Map one ROI from 640×480 wizard space into master pixel coordinates."""
    sx, sy = wizard_to_master_scale_factors(master_width, master_height)
    return clamp_roi(
        {
            'x': float(roi.get('x', 0)) * sx,
            'y': float(roi.get('y', 0)) * sy,
            'width': float(roi.get('width', 2)) * sx,
            'height': float(roi.get('height', 2)) * sy,
        },
        int(master_width),
        int(master_height),
    )


def normalize_tools_to_master_pixels(
    tools: List[Dict],
    master_width: int,
    master_height: int,
    *,
    roi_space: str = 'wizard_640x480',
) -> List[Dict]:
    """
    Map template / program tool ROIs onto the target master image pixel grid.

    Templates saved from the wizard use roi_space=wizard_640x480 (stretch to master size).
    High-resolution masters (e.g. IMX296 1456×1088) use the same stretch as the Configure
    UI: master_w/640 and master_h/480 per axis.
    """
    if not tools or master_width < 1 or master_height < 1:
        return list(tools)

    mw, mh = int(master_width), int(master_height)
    space = (roi_space or 'wizard_640x480').lower()

    if space in ('native', 'master_pixels', 'master'):
        return [
            {**t, 'roi': clamp_roi(t.get('roi') or {}, mw, mh)}
            for t in tools
        ]

    if rois_look_normalized_01(tools):
        return [
            {
                **t,
                'roi': clamp_roi(
                    {
                        'x': float(t['roi']['x']) * mw,
                        'y': float(t['roi']['y']) * mh,
                        'width': float(t['roi']['width']) * mw,
                        'height': float(t['roi']['height']) * mh,
                    },
                    mw,
                    mh,
                ),
            }
            for t in tools
        ]

    # wizard_640x480 or legacy templates without roi_space
    if space == ROI_SPACE_WIZARD or (
        space != ROI_SPACE_NATIVE and rois_fit_wizard_canvas(tools)
    ):
        out: List[Dict] = []
        for t in tools:
            roi = t.get('roi') or {}
            out.append(
                {
                    **t,
                    'roi': scale_wizard_roi_to_master(roi, mw, mh),
                }
            )
        return out

    # ROIs already in native-ish coordinates
    return [{**t, 'roi': clamp_roi(t.get('roi') or {}, mw, mh)} for t in tools]


def master_image_dimensions(master_rgb) -> Tuple[int, int]:
    import numpy as np

    if master_rgb is None or not isinstance(master_rgb, np.ndarray) or master_rgb.size == 0:
        raise ValueError('Invalid master image')
    h, w = master_rgb.shape[:2]
    return int(w), int(h)
