/**
 * ROI coordinate helpers — wizard canvas (640×480) vs master pixels.
 * Mirrors backend/src/core/tool_roi.py for template apply and inspection run.
 */

import type { ROI, ToolConfig } from '@/types';

/** Wizard interactive canvas size (must match ROI coordinates in saved programs/templates). */
export const WIZARD_CANVAS_W = 640;
export const WIZARD_CANVAS_H = 480;

export const ROI_SPACE_WIZARD = 'wizard_640x480';
export const ROI_SPACE_NORMALIZED = 'normalized_01';
export const ROI_SPACE_NATIVE = 'native';

export const VALID_TEMPLATE_ROI_SPACES = [
  ROI_SPACE_WIZARD,
  ROI_SPACE_NORMALIZED,
  ROI_SPACE_NATIVE,
] as const;

export type TemplateRoiSpace = (typeof VALID_TEMPLATE_ROI_SPACES)[number];

function clampRoi(roi: ROI, maxW: number, maxH: number): ROI {
  const x = Math.max(0, Math.round(roi.x));
  const y = Math.max(0, Math.round(roi.y));
  let w = Math.max(2, Math.round(roi.width));
  let h = Math.max(2, Math.round(roi.height));
  if (x + w > maxW) w = Math.max(2, maxW - x);
  if (y + h > maxH) h = Math.max(2, maxH - y);
  return { x, y, width: w, height: h };
}

function scaleRoi(roi: ROI, sx: number, sy: number): ROI {
  return {
    x: Math.max(0, Math.round(roi.x * sx)),
    y: Math.max(0, Math.round(roi.y * sy)),
    width: Math.max(2, Math.round(roi.width * sx)),
    height: Math.max(2, Math.round(roi.height * sy)),
  };
}

export function roisFitWizard640Space(tools: ToolConfig[]): boolean {
  if (tools.length === 0) return false;
  return tools.every((t) => {
    const r = t.roi;
    return (
      r &&
      r.width >= 1 &&
      r.height >= 1 &&
      r.x >= -0.5 &&
      r.y >= -0.5 &&
      r.x + r.width <= WIZARD_CANVAS_W + 0.5 &&
      r.y + r.height <= WIZARD_CANVAS_H + 0.5
    );
  });
}

export function roisLookNormalized01(tools: ToolConfig[]): boolean {
  if (tools.length === 0) return false;
  return tools.every((t) => {
    const r = t.roi;
    if (!r || r.width <= 0 || r.height <= 0) return false;
    return (
      r.x >= 0 &&
      r.y >= 0 &&
      r.x <= 1.001 &&
      r.y <= 1.001 &&
      r.x + r.width <= 1.001 &&
      r.y + r.height <= 1.001
    );
  });
}

/** Detect how template tool ROIs were authored (same order as backend). */
export function inferTemplateRoiSpace(tools: ToolConfig[]): TemplateRoiSpace {
  if (tools.length === 0) return ROI_SPACE_WIZARD;
  if (roisLookNormalized01(tools)) return ROI_SPACE_NORMALIZED;
  if (roisFitWizard640Space(tools)) return ROI_SPACE_WIZARD;
  return ROI_SPACE_NATIVE;
}

export function resolveTemplateRoiSpace(
  tools: ToolConfig[],
  roiSpace?: string | null
): TemplateRoiSpace {
  const raw = (roiSpace || '').toLowerCase();
  if (VALID_TEMPLATE_ROI_SPACES.includes(raw as TemplateRoiSpace)) {
    return raw as TemplateRoiSpace;
  }
  return inferTemplateRoiSpace(tools);
}

/**
 * Map template / program tool ROIs onto a target image pixel grid (e.g. full master).
 */
export function normalizeToolsToImagePixels(
  tools: ToolConfig[],
  masterWidth: number,
  masterHeight: number,
  roiSpace?: string | null
): ToolConfig[] {
  if (tools.length === 0 || masterWidth < 1 || masterHeight < 1) return tools;

  const mw = Math.round(masterWidth);
  const mh = Math.round(masterHeight);
  const space = resolveTemplateRoiSpace(tools, roiSpace);

  if (space === ROI_SPACE_NATIVE || space === 'master_pixels' || space === 'master') {
    return tools.map((t) => ({ ...t, roi: clampRoi(t.roi, mw, mh) }));
  }

  if (space === ROI_SPACE_NORMALIZED || roisLookNormalized01(tools)) {
    return tools.map((t) => ({
      ...t,
      roi: clampRoi(
        {
          x: t.roi.x * mw,
          y: t.roi.y * mh,
          width: t.roi.width * mw,
          height: t.roi.height * mh,
        },
        mw,
        mh
      ),
    }));
  }

  // wizard_640x480
  const sx = mw / WIZARD_CANVAS_W;
  const sy = mh / WIZARD_CANVAS_H;
  return tools.map((t) => ({
    ...t,
    roi: clampRoi(scaleRoi(t.roi, sx, sy), mw, mh),
  }));
}

/** Map master-pixel ROIs onto the wizard 640×480 canvas. */
export function masterPixelsToWizardCanvas(
  tools: ToolConfig[],
  masterWidth: number,
  masterHeight: number
): ToolConfig[] {
  if (tools.length === 0 || masterWidth < 1 || masterHeight < 1) return tools;
  const sx = WIZARD_CANVAS_W / masterWidth;
  const sy = WIZARD_CANVAS_H / masterHeight;
  return tools.map((t) => ({
    ...t,
    roi: clampRoi(scaleRoi(t.roi, sx, sy), WIZARD_CANVAS_W, WIZARD_CANVAS_H),
  }));
}

/** Template tools → wizard canvas for Configure UI (preserves threshold and limits). */
export function templateToolsToWizardCanvas(
  tools: ToolConfig[],
  roiSpace?: string | null
): ToolConfig[] {
  if (tools.length === 0) return tools;
  const space = resolveTemplateRoiSpace(tools, roiSpace);

  if (space === ROI_SPACE_WIZARD) {
    return tools.map((t) => ({
      ...t,
      roi: clampRoi(t.roi, WIZARD_CANVAS_W, WIZARD_CANVAS_H),
    }));
  }

  if (space === ROI_SPACE_NORMALIZED) {
    return tools.map((t) => ({
      ...t,
      roi: clampRoi(
        {
          x: t.roi.x * WIZARD_CANVAS_W,
          y: t.roi.y * WIZARD_CANVAS_H,
          width: t.roi.width * WIZARD_CANVAS_W,
          height: t.roi.height * WIZARD_CANVAS_H,
        },
        WIZARD_CANVAS_W,
        WIZARD_CANVAS_H
      ),
    }));
  }

  return tools;
}
