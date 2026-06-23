/**
 * Fast client-side ROI judgment for wizard / Master Pi tool configuration.
 * No tool-preview API — browser estimates for instant threshold feedback.
 * Production pass/fail still comes from the full inspection pipeline (Save & run once).
 */

import type { ROI, ToolType } from '@/types';

const MIN_ROI = 8;
const DEBOUNCE_MS = 80;

export interface JudgmentChannel {
  metricLabel: string;
  /** Raw fast metric 0–100 (edge strength, contrast, etc.). */
  fastScore: number;
  /** When the full pipeline has run, template match % (preferred for display). */
  pipelineScore?: number | null;
  detail?: string;
}

export interface ToolJudgmentSnapshot {
  toolType: ToolType;
  master: JudgmentChannel | null;
  live: JudgmentChannel | null;
}

/** @deprecated Use ToolJudgmentSnapshot */
export interface ToolJudgmentResult {
  metricLabel: string;
  score: number;
}

export type JudgmentTone = 'pass' | 'fail' | 'warn' | 'muted';

const imageCache = new Map<string, Promise<HTMLImageElement>>();

function loadImage(base64: string): Promise<HTMLImageElement> {
  const key = base64.length > 128 ? `${base64.length}:${base64.slice(0, 64)}:${base64.slice(-32)}` : base64;
  let pending = imageCache.get(key);
  if (!pending) {
    pending = new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error('Failed to load image for tool judgment'));
      img.src = base64.startsWith('data:') ? base64 : `data:image/png;base64,${base64}`;
    });
    imageCache.set(key, pending);
    if (imageCache.size > 6) {
      const first = imageCache.keys().next().value;
      if (first) imageCache.delete(first);
    }
  }
  return pending;
}

function extractRoiCanvas(img: HTMLImageElement, roi: ROI): HTMLCanvasElement | null {
  const w = Math.floor(roi.width);
  const h = Math.floor(roi.height);
  if (w < MIN_ROI || h < MIN_ROI) return null;

  const x = Math.max(0, Math.min(Math.floor(roi.x), img.width - 1));
  const y = Math.max(0, Math.min(Math.floor(roi.y), img.height - 1));
  const rw = Math.min(w, img.width - x);
  const rh = Math.min(h, img.height - y);
  if (rw < MIN_ROI || rh < MIN_ROI) return null;

  const canvas = document.createElement('canvas');
  canvas.width = rw;
  canvas.height = rh;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.drawImage(img, x, y, rw, rh, 0, 0, rw, rh);
  return canvas;
}

function toGrayscale(imageData: ImageData): Uint8Array {
  const { data, width, height } = imageData;
  const gray = new Uint8Array(width * height);
  for (let i = 0, p = 0; i < data.length; i += 4, p++) {
    gray[p] = (data[i] + data[i + 1] + data[i + 2]) / 3;
  }
  return gray;
}

function stdDev(values: Uint8Array): number {
  if (values.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < values.length; i++) sum += values[i];
  const mean = sum / values.length;
  let sq = 0;
  for (let i = 0; i < values.length; i++) {
    const d = values[i] - mean;
    sq += d * d;
  }
  return Math.sqrt(sq / values.length);
}

function otsuThreshold(gray: Uint8Array): number {
  const histogram = new Array(256).fill(0);
  for (let i = 0; i < gray.length; i++) histogram[gray[i]]++;
  const total = gray.length;
  let sum = 0;
  for (let i = 0; i < 256; i++) sum += i * histogram[i];
  let sumB = 0;
  let wB = 0;
  let maxVariance = 0;
  let threshold = 0;
  for (let i = 0; i < 256; i++) {
    wB += histogram[i];
    if (wB === 0) continue;
    const wF = total - wB;
    if (wF === 0) break;
    sumB += i * histogram[i];
    const mB = sumB / wB;
    const mF = (sum - sumB) / wF;
    const variance = wB * wF * (mB - mF) * (mB - mF);
    if (variance > maxVariance) {
      maxVariance = variance;
      threshold = i;
    }
  }
  return threshold;
}

function sobelMagnitudes(gray: Uint8Array, width: number, height: number): { strong: number; total: number; meanMag: number } {
  const sobelX = [-1, 0, 1, -2, 0, 2, -1, 0, 1];
  const sobelY = [-1, -2, -1, 0, 0, 0, 1, 2, 1];
  const edgeThr = 128;
  let strong = 0;
  let total = 0;
  let magSum = 0;

  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      let gx = 0;
      let gy = 0;
      for (let ky = -1; ky <= 1; ky++) {
        for (let kx = -1; kx <= 1; kx++) {
          const idx = (y + ky) * width + (x + kx);
          const k = (ky + 1) * 3 + (kx + 1);
          gx += gray[idx] * sobelX[k];
          gy += gray[idx] * sobelY[k];
        }
      }
      total++;
      const mag = Math.sqrt(gx * gx + gy * gy);
      magSum += mag;
      if (mag > edgeThr) strong++;
    }
  }

  return { strong, total, meanMag: total > 0 ? magSum / total : 0 };
}

function edgeStrengthScore(gray: Uint8Array, width: number, height: number): number {
  const { strong, total } = sobelMagnitudes(gray, width, height);
  if (total === 0) return 0;
  return Math.min(100, Math.round((strong / total) * 100 * 2));
}

function contrastScore(gray: Uint8Array): number {
  return Math.min(100, Math.round((stdDev(gray) / 48) * 100));
}

function brightAreaPercent(gray: Uint8Array): number {
  const T = otsuThreshold(gray);
  let bright = 0;
  for (let i = 0; i < gray.length; i++) {
    if (gray[i] > T) bright++;
  }
  return gray.length > 0 ? (bright / gray.length) * 100 : 0;
}

function colorVariationScore(imageData: ImageData): number {
  const { data, width, height } = imageData;
  const n = width * height;
  if (n === 0) return 0;
  const r = new Uint8Array(n);
  const g = new Uint8Array(n);
  const b = new Uint8Array(n);
  for (let i = 0, p = 0; i < data.length; i += 4, p++) {
    r[p] = data[i];
    g[p] = data[i + 1];
    b[p] = data[i + 2];
  }
  const spread = (stdDev(r) + stdDev(g) + stdDev(b)) / 3;
  return Math.min(100, Math.round((spread / 64) * 100));
}

function metricLabelFor(toolType: ToolType): string {
  switch (toolType) {
    case 'outline':
    case 'edge_detection':
      return 'Edge strength';
    case 'area':
      return 'Area signal';
    case 'color_area':
      return 'Color variation';
    default:
      return 'Score';
  }
}

type MasterFeat = Record<string, unknown>;

function matchRateFromDeviation(dev: number, tol: number): number {
  if (tol < 1e-12) return dev < 1e-9 ? 100 : 0;
  return Math.max(0, Math.min(100, 100 - (dev / tol) * 100));
}

/** Align fast score with stored master template features when available. */
function scoreWithMasterFeatures(
  toolType: ToolType,
  toolId: string | undefined,
  masterFeatures: MasterFeat | undefined,
  imageData: ImageData,
  gray: Uint8Array
): { score: number; detail?: string } {
  const w = imageData.width;
  const h = imageData.height;
  const px = w * h;
  const feat = toolId && masterFeatures ? masterFeatures[toolId] : undefined;

  if (toolType === 'edge_detection' || toolType === 'outline') {
    const { strong, total, meanMag } = sobelMagnitudes(gray, w, h);
    const fast = total > 0 ? Math.min(100, Math.round((strong / total) * 100 * 2)) : 0;
    const f = feat as Record<string, unknown> | undefined;
    if (f && f.edgePixels != null && f.roiPixelCount != null) {
      const refPx = f.roiPixelCount as number;
      const refEdges = f.edgePixels as number;
      const curD = total > 0 ? strong / total : 0;
      const refD = refPx > 0 ? refEdges / refPx : 0;
      if (refD < 1e-12 && refEdges === 0) {
        return { score: strong === 0 ? 100 : 0, detail: 'Template: no edges' };
      }
      const tol = Math.max(refD * 0.15, 1 / Math.max(refPx, 1));
      const match = matchRateFromDeviation(Math.abs(curD - refD), tol);
      return {
        score: Math.round(match),
        detail: `Edge fill ${((strong / Math.max(total, 1)) * 100).toFixed(1)}% · mean ${meanMag.toFixed(0)}`,
      };
    }
    return { score: fast, detail: `Edge fill ${((strong / Math.max(total, 1)) * 100).toFixed(1)}%` };
  }

  if (toolType === 'area') {
    const brightPct = brightAreaPercent(gray);
    const fast = Math.min(100, Math.round(brightPct));
    const f = feat as Record<string, unknown> | undefined;
    if (f && f.brightAreaRatio != null) {
      const ref = f.brightAreaRatio as number;
      const tol = Math.max(ref * 0.12, 2);
      const match = matchRateFromDeviation(Math.abs(brightPct - ref), tol);
      return { score: Math.round(match), detail: `Bright area ${brightPct.toFixed(1)}%` };
    }
    return { score: contrastScore(gray), detail: `Bright area ${brightPct.toFixed(1)}%` };
  }

  if (toolType === 'color_area') {
    const cv = colorVariationScore(imageData);
    return { score: cv, detail: 'RGB spread in ROI' };
  }

  return { score: 0 };
}

async function analyzeChannel(
  imageBase64: string | null | undefined,
  toolType: ToolType,
  roi: ROI,
  roiInWizardSpace: boolean,
  toolId: string | undefined,
  masterFeatures: MasterFeat | undefined
): Promise<JudgmentChannel | null> {
  if (!imageBase64) return null;

  const img = await loadImage(imageBase64);
  const nativeRoi =
    roiInWizardSpace !== false ? wizardRoiToImagePixels(roi, img.width, img.height) : roi;
  const canvas = extractRoiCanvas(img, nativeRoi);
  if (!canvas) return null;

  const ctx = canvas.getContext('2d');
  if (!ctx) return null;

  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const gray = toGrayscale(imageData);
  const { score, detail } = scoreWithMasterFeatures(toolType, toolId, masterFeatures, imageData, gray);

  return {
    metricLabel: metricLabelFor(toolType),
    fastScore: Math.max(0, Math.min(100, score)),
    detail,
  };
}

/** Scale wizard-space ROI (640×480) to image pixel coordinates. */
export function wizardRoiToImagePixels(roi: ROI, imgW: number, imgH: number): ROI {
  const sx = imgW / 640;
  const sy = imgH / 480;
  return {
    x: roi.x * sx,
    y: roi.y * sy,
    width: Math.max(MIN_ROI, roi.width * sx),
    height: Math.max(MIN_ROI, roi.height * sy),
  };
}

export interface AnalyzeToolJudgmentOptions {
  roiInWizardSpace?: boolean;
  toolId?: string;
  masterFeatures?: MasterFeat;
  liveImageBase64?: string | null;
}

/**
 * Analyze master (+ optional live) ROIs for real-time judgment.
 */
export async function analyzeToolJudgment(
  masterImageBase64: string,
  toolType: ToolType,
  roi: ROI,
  options: AnalyzeToolJudgmentOptions = {}
): Promise<ToolJudgmentSnapshot | null> {
  if (toolType === 'position_adjust') return null;

  const [master, live] = await Promise.all([
    analyzeChannel(
      masterImageBase64,
      toolType,
      roi,
      options.roiInWizardSpace !== false,
      options.toolId,
      options.masterFeatures
    ),
    analyzeChannel(
      options.liveImageBase64,
      toolType,
      roi,
      options.roiInWizardSpace !== false,
      options.toolId,
      options.masterFeatures
    ),
  ]);

  if (!master && !live) return null;
  return { toolType, master, live };
}

/** Legacy single-image API. */
export async function analyzeToolRoi(
  imageBase64: string,
  toolType: ToolType,
  roi: ROI,
  options?: { roiInWizardSpace?: boolean; toolId?: string; masterFeatures?: MasterFeat }
): Promise<ToolJudgmentResult | null> {
  const snap = await analyzeToolJudgment(imageBase64, toolType, roi, options);
  const ch = snap?.master;
  if (!ch) return null;
  return { metricLabel: ch.metricLabel, score: displayScore(ch) ?? 0 };
}

/** Score used for PASS/FAIL — pipeline match when available, else fast metric. */
export function displayScore(channel: JudgmentChannel | null | undefined): number | null {
  if (!channel) return null;
  if (channel.pipelineScore != null && Number.isFinite(channel.pipelineScore)) {
    return Math.round(channel.pipelineScore);
  }
  return channel.fastScore;
}

export function judgmentPass(score: number | null, threshold: number, upper?: number): boolean | null {
  if (score == null) return null;
  if (upper !== undefined) return score >= threshold && score <= upper;
  return score >= threshold;
}

export function judgmentMargin(score: number | null, threshold: number): number | null {
  if (score == null) return null;
  return score - threshold;
}

export function judgmentTone(pass: boolean | null): JudgmentTone {
  if (pass === true) return 'pass';
  if (pass === false) return 'fail';
  return 'muted';
}

/** Merge async pipeline match % into an existing snapshot (no re-analysis). */
export function mergePipelineScores(
  snapshot: ToolJudgmentSnapshot | null,
  pipeline: { master?: number | null; live?: number | null }
): ToolJudgmentSnapshot | null {
  if (!snapshot) return null;
  return {
    ...snapshot,
    master: snapshot.master
      ? { ...snapshot.master, pipelineScore: pipeline.master ?? snapshot.master.pipelineScore }
      : null,
    live: snapshot.live
      ? { ...snapshot.live, pipelineScore: pipeline.live ?? snapshot.live.pipelineScore }
      : null,
  };
}

/** Suggest a threshold from master fast score (leave ~8pt headroom). */
export function suggestThreshold(masterChannel: JudgmentChannel | null | undefined): number | null {
  const s = masterChannel ? displayScore(masterChannel) : null;
  if (s == null) return null;
  return Math.max(0, Math.min(100, Math.round(s - 8)));
}

export const TOOL_JUDGMENT_DEBOUNCE_MS = DEBOUNCE_MS;
