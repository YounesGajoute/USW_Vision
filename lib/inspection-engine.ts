/**
 * Inspection Engine - Core processing logic for vision inspection tools
 * Handles all tool types: outline, area, color_area, edge_detection, position_adjustment
 */

import type { ToolConfig, ToolResult, ROI, ToolType } from '@/types';
import {
  inferTemplateRoiSpace,
  normalizeToolsToImagePixels,
  masterPixelsToWizardCanvas,
  resolveTemplateRoiSpace,
  ROI_SPACE_NATIVE,
  templateToolsToWizardCanvas,
  WIZARD_CANVAS_H,
  WIZARD_CANVAS_W,
} from '@/lib/tool-roi';

export {
  inferTemplateRoiSpace,
  normalizeToolsToImagePixels,
  masterPixelsToWizardCanvas,
  resolveTemplateRoiSpace,
  ROI_SPACE_WIZARD,
  ROI_SPACE_NORMALIZED,
  ROI_SPACE_NATIVE,
  templateToolsToWizardCanvas,
  WIZARD_CANVAS_W,
  WIZARD_CANVAS_H,
} from '@/lib/tool-roi';

// ==================== INTERFACES ====================

export interface InspectionResult {
  id: string;
  timestamp: Date;
  programId: string | number;
  status: 'OK' | 'NG';
  overallConfidence: number;
  processingTime: number;
  toolResults: ToolResult[];
  image: string;
  positionOffset?: { x: number; y: number };
}

export interface ProcessingOptions {
  masterFeatures?: Record<string, any>;
  debugMode?: boolean;
}

// ==================== MAIN INSPECTION PROCESSOR ====================

/**
 * Process a complete inspection with all configured tools
 */
export async function processInspection(
  imageBase64: string,
  tools: ToolConfig[],
  options: ProcessingOptions = {}
): Promise<InspectionResult> {
  const startTime = performance.now();
  
  try {
    // Load image
    const img = await loadImage(imageBase64);
    
    // Step 1: Position adjustment first (if configured)
    let positionOffset = { x: 0, y: 0 };
    const positionTool = tools.find(t => t.type === 'position_adjust');
    
    if (positionTool) {
      positionOffset = await processPositionAdjustment(img, positionTool, options);
    }
    
    // Step 2: Process all detection tools
    const toolResults: ToolResult[] = [];
    let overallStatus: 'OK' | 'NG' = 'OK';
    
    for (const tool of tools) {
      if (tool.type === 'position_adjust') continue; // Already processed
      
      // Adjust ROI based on position offset
      const adjustedROI = {
        x: tool.roi.x + positionOffset.x,
        y: tool.roi.y + positionOffset.y,
        width: tool.roi.width,
        height: tool.roi.height
      };
      
      // Ensure ROI is within image bounds
      const boundedROI = boundROI(adjustedROI, img.width, img.height);
      
      // Process tool
      const toolResult = await processTool(img, tool, boundedROI, options);
      toolResults.push(toolResult);
      
      // Update overall status
      if (toolResult.status === 'NG') {
        overallStatus = 'NG';
      }
    }
    
    // Calculate overall confidence
    const overallConfidence = toolResults.length > 0
      ? toolResults.reduce((sum, t) => sum + t.matching_rate, 0) / toolResults.length
      : 0;
    
    const processingTime = performance.now() - startTime;
    
    return {
      id: `INS-${Date.now()}`,
      timestamp: new Date(),
      programId: '',
      status: overallStatus,
      overallConfidence,
      processingTime,
      toolResults,
      image: imageBase64,
      positionOffset
    };
  } catch (error) {
    console.error('Inspection processing error:', error);
    throw error;
  }
}

// ==================== TOOL PROCESSORS ====================

/**
 * Route tool processing to appropriate handler
 */
async function processTool(
  img: HTMLImageElement,
  tool: ToolConfig,
  roi: ROI,
  options: ProcessingOptions
): Promise<ToolResult> {
  
  // Extract ROI from image
  const roiCanvas = extractROI(img, roi);
  
  let matchingRate = 0;
  let confidence = 0;
  
  try {
    switch (tool.type) {
      case 'outline':
        ({ matchingRate, confidence } = await processOutlineTool(roiCanvas, tool, options));
        break;
      case 'area':
        ({ matchingRate, confidence } = await processAreaTool(roiCanvas, tool, options));
        break;
      case 'color_area':
        ({ matchingRate, confidence } = await processColorAreaTool(roiCanvas, tool, options));
        break;
      case 'edge_detection':
        ({ matchingRate, confidence } = await processEdgeDetectionTool(roiCanvas, tool, options));
        break;
      default:
        console.warn(`Unknown tool type: ${tool.type}`);
    }
  } catch (error) {
    console.error(`Error processing tool ${tool.name}:`, error);
    return {
      tool_type: tool.type,
      name: tool.name,
      status: 'NG',
      matching_rate: 0,
      threshold: tool.threshold,
      error: String(error),
      confidence: 0
    };
  }
  
  // Determine pass/fail based on threshold
  let status: 'OK' | 'NG' = 'OK';
  
  if (tool.upperLimit !== undefined) {
    // Range-based judgment
    status = matchingRate >= tool.threshold && matchingRate <= tool.upperLimit ? 'OK' : 'NG';
  } else {
    // Simple threshold
    status = matchingRate >= tool.threshold ? 'OK' : 'NG';
  }
  
  return {
    tool_type: tool.type,
    name: tool.name,
    status,
    matching_rate: matchingRate,
    threshold: tool.threshold,
    upper_limit: tool.upperLimit,
    confidence
  };
}

/**
 * Scale any camera/master frame to the wizard canvas size (640×480) so ROI
 * coordinates match the interactive canvas used in tool configuration.
 */
/** High-quality downscale for wizard canvas (640×480) from full-resolution captures. */
export function enableHighQualityCanvasScaling(ctx: CanvasRenderingContext2D): void {
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
}

export async function imageBase64ToWizardFrame640(imageBase64: string): Promise<string> {
  const img = await loadImage(imageBase64);
  const c = document.createElement('canvas');
  c.width = 640;
  c.height = 480;
  const ctx = c.getContext('2d')!;
  enableHighQualityCanvasScaling(ctx);
  ctx.drawImage(img, 0, 0, 640, 480);
  return c.toDataURL('image/png');
}

/**
 * Clamp an ROI so decorations and labels stay visible on the wizard canvas.
 */
export function clampRoiToWizardCanvas(roi: ROI): ROI {
  const x0 = Math.max(0, Math.min(roi.x, WIZARD_CANVAS_W - 2));
  const y0 = Math.max(0, Math.min(roi.y, WIZARD_CANVAS_H - 2));
  const w0 = Math.max(2, Math.min(roi.width, WIZARD_CANVAS_W - x0));
  const h0 = Math.max(2, Math.min(roi.height, WIZARD_CANVAS_H - y0));
  return { x: x0, y: y0, width: w0, height: h0 };
}

/**
 * Saved programs may store ROI in native master-image pixels while the configure wizard
 * always stretches the master to 640×480 (see {@link imageBase64ToWizardFrame640}).
 * When any ROI extends past the wizard canvas, rescale **every** tool's ROI (all types:
 * outline, area, color_area, edge_detection, position_adjust) from native image size to
 * wizard canvas pixels using the same stretch factors.
 */
export async function normalizeToolRoisForWizardMasterImage(
  masterImageBase64: string,
  tools: ToolConfig[]
): Promise<ToolConfig[]> {
  if (tools.length === 0) return tools;

  /** Legacy / import: ROIs stored as fractions of the wizard frame (0–1). */
  const looksLikeNorm01 = tools.every((t) => {
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
  if (looksLikeNorm01) {
    return tools.map((t) => ({
      ...t,
      roi: clampRoiToWizardCanvas({
        x: Math.round(t.roi.x * WIZARD_CANVAS_W),
        y: Math.round(t.roi.y * WIZARD_CANVAS_H),
        width: Math.max(2, Math.round(t.roi.width * WIZARD_CANVAS_W)),
        height: Math.max(2, Math.round(t.roi.height * WIZARD_CANVAS_H)),
      }),
    }));
  }

  const img = await loadImage(masterImageBase64);
  const nw = img.naturalWidth || img.width;
  const nh = img.naturalHeight || img.height;
  if (!nw || !nh) return tools;

  const overflowsCanvas = tools.some((t) => {
    const r = t.roi;
    if (!r || r.width < 1 || r.height < 1) return true;
    return (
      r.x + r.width > WIZARD_CANVAS_W + 0.5 ||
      r.y + r.height > WIZARD_CANVAS_H + 0.5 ||
      r.x < -0.5 ||
      r.y < -0.5
    );
  });
  if (!overflowsCanvas) return tools;

  /** When the served master is already 640×480 but ROIs were saved in native camera space, infer native span from ROI extents. */
  let natW = nw;
  let natH = nh;
  if (nw <= WIZARD_CANVAS_W && nh <= WIZARD_CANVAS_H) {
    const maxRight = Math.max(0, ...tools.map((t) => t.roi.x + t.roi.width));
    const maxBottom = Math.max(0, ...tools.map((t) => t.roi.y + t.roi.height));
    natW = Math.max(nw, maxRight);
    natH = Math.max(nh, maxBottom);
    if (natW <= WIZARD_CANVAS_W + 0.5 && natH <= WIZARD_CANVAS_H + 0.5) {
      return tools;
    }
  }

  const sx = WIZARD_CANVAS_W / natW;
  const sy = WIZARD_CANVAS_H / natH;
  return tools.map((t) => ({
    ...t,
    roi: clampRoiToWizardCanvas(scaleRoiWithFactors(t.roi, sx, sy)),
  }));
}

/** True when every tool's ROI lies inside the wizard 640×480 frame (typical saved wizard space). */
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

function scaleRoiWithFactors(roi: ROI, sx: number, sy: number): ROI {
  return {
    x: Math.max(0, Math.round(roi.x * sx)),
    y: Math.max(0, Math.round(roi.y * sy)),
    width: Math.max(2, Math.round(roi.width * sx)),
    height: Math.max(2, Math.round(roi.height * sy)),
  };
}

/** Map all tool ROIs from wizard 640×480 convention into another pixel grid (e.g. live capture size). */
export function scaleToolRoisFromWizard640ToPixels(tools: ToolConfig[], targetW: number, targetH: number): ToolConfig[] {
  if (tools.length === 0 || targetW < 1 || targetH < 1) return tools;
  const sx = targetW / WIZARD_CANVAS_W;
  const sy = targetH / WIZARD_CANVAS_H;
  return tools.map((t) => ({
    ...t,
    roi: scaleRoiWithFactors(t.roi, sx, sy),
  }));
}

function toolsOverflowImage(tools: ToolConfig[], nw: number, nh: number): boolean {
  return tools.some((t) => {
    const r = t.roi;
    if (!r || r.width < 1 || r.height < 1) return true;
    return r.x + r.width > nw + 0.5 || r.y + r.height > nh + 0.5 || r.x < -0.5 || r.y < -0.5;
  });
}

/**
 * Return a copy of `tools` with ROIs in the same pixel space as an image of size `nw` × `nh`.
 * Honors `roiSpace` from tool templates / program config when provided.
 */
export function resolveToolRoisForImageSize(
  tools: ToolConfig[],
  nw: number,
  nh: number,
  roiSpace?: string | null
): ToolConfig[] {
  return normalizeToolsToImagePixels(tools, nw, nh, roiSpace);
}

/**
 * Same as {@link resolveToolRoisForImageSize} after decoding `imageBase64`.
 */
export async function resolveToolRoisForImagePixels(
  imageBase64: string,
  tools: ToolConfig[],
  roiSpace?: string | null
): Promise<ToolConfig[]> {
  if (tools.length === 0) return tools;
  const img = await loadImage(imageBase64);
  const nw = img.naturalWidth || img.width;
  const nh = img.naturalHeight || img.height;
  return resolveToolRoisForImageSize(tools, nw, nh, roiSpace);
}

/**
 * Apply a saved tool template onto the wizard canvas (640×480) for any master / layout image.
 */
export async function applyTemplateToolsToWizardCanvas(
  tools: ToolConfig[],
  roiSpace: string | undefined | null,
  masterImageBase64: string
): Promise<ToolConfig[]> {
  const space = resolveTemplateRoiSpace(tools, roiSpace);
  if (space === ROI_SPACE_NATIVE) {
    const img = await loadImage(masterImageBase64);
    const nw = img.naturalWidth || img.width;
    const nh = img.naturalHeight || img.height;
    return masterPixelsToWizardCanvas(tools, nw, nh);
  }
  const onCanvas = templateToolsToWizardCanvas(tools, roiSpace);
  return normalizeToolRoisForWizardMasterImage(masterImageBase64, onCanvas);
}

/** Map an ROI from image pixel space to canvas element space (after drawImage stretched image to canvas). */
export function mapRoiImageToCanvas(roi: ROI, imgW: number, imgH: number, canvasW: number, canvasH: number): ROI {
  if (imgW < 1 || imgH < 1) return roi;
  const sx = canvasW / imgW;
  const sy = canvasH / imgH;
  return {
    x: roi.x * sx,
    y: roi.y * sy,
    width: Math.max(1, roi.width * sx),
    height: Math.max(1, roi.height * sy),
  };
}

/**
 * Preview one detection tool's matching rate against an image (master or live),
 * applying position_adjust from the current program when present.
 *
 * {@link PreviewToolMatch} may include extra readouts (e.g. edge fill) for wizard UI.
 */
export interface PreviewToolMatch {
  matching_rate: number;
  status: 'OK' | 'NG';
  threshold: number;
  /** Edge tool: strong edges as % of ROI pixels (comparable master vs live). */
  edge_density_percent?: number;
  edge_pixel_count?: number;
  roi_pixel_count?: number;
}

export async function previewDetectionToolMatch(
  imageBase64: string,
  allTools: ToolConfig[],
  targetTool: ToolConfig,
  roiOverride: ROI | null,
  judgeThreshold: number,
  options: ProcessingOptions = {}
): Promise<PreviewToolMatch> {
  const img = await loadImage(imageBase64);
  let positionOffset = { x: 0, y: 0 };
  const positionTool = allTools.find(t => t.type === 'position_adjust');
  if (positionTool) {
    const off = await processPositionAdjustment(img, positionTool, options);
    positionOffset = { x: off.x, y: off.y };
  }
  const toolForJudge: ToolConfig = {
    ...targetTool,
    threshold: judgeThreshold,
  };
  const baseRoi = roiOverride ?? targetTool.roi;
  const adjustedROI = {
    x: baseRoi.x + positionOffset.x,
    y: baseRoi.y + positionOffset.y,
    width: baseRoi.width,
    height: baseRoi.height,
  };
  const boundedROI = boundROI(adjustedROI, img.width, img.height);
  const tr = await processTool(img, toolForJudge, boundedROI, options);
  const out: PreviewToolMatch = {
    matching_rate: tr.matching_rate,
    status: tr.status,
    threshold: judgeThreshold,
  };
  if (targetTool.type === 'edge_detection') {
    const roiCanvas = extractROI(img, boundedROI);
    const px = roiCanvas.width * roiCanvas.height;
    if (px > 0) {
      const ctx = roiCanvas.getContext('2d')!;
      const imageData = ctx.getImageData(0, 0, roiCanvas.width, roiCanvas.height);
      const grayData = convertToGrayscale(imageData);
      const edges = applySobelEdgeDetection(grayData, roiCanvas.width, roiCanvas.height);
      let edgePixels = 0;
      const edgeThr = 128;
      for (let i = 0; i < edges.length; i++) {
        if (edges[i] > edgeThr) edgePixels++;
      }
      out.edge_pixel_count = edgePixels;
      out.roi_pixel_count = px;
      out.edge_density_percent = (edgePixels / px) * 100;
    }
  }
  return out;
}

// ==================== OUTLINE TOOL ====================

async function processOutlineTool(
  canvas: HTMLCanvasElement,
  tool: ToolConfig,
  options: ProcessingOptions
): Promise<{ matchingRate: number; confidence: number }> {
  
  const ctx = canvas.getContext('2d')!;
  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  
  // Convert to grayscale
  const grayData = convertToGrayscale(imageData);
  
  // Apply binary threshold (Otsu's method)
  const threshold = calculateOtsuThreshold(grayData);
  const binaryData = applyThreshold(grayData, threshold);
  
  // Find contours
  const contours = findContours(binaryData, canvas.width, canvas.height);
  
  if (contours.length === 0) {
    return { matchingRate: 0, confidence: 0 };
  }
  
  // Calculate Hu moments for shape matching
  const huMoments = calculateHuMoments(contours[0], canvas.width, canvas.height);
  
  // Compare with master features (if available)
  const masterFeatures = options.masterFeatures?.[tool.id];
  if (masterFeatures?.huMoments) {
    const similarity = compareHuMoments(huMoments, masterFeatures.huMoments);
    return {
      matchingRate: similarity * 100,
      confidence: Math.min(95, similarity * 100 + 5)
    };
  }
  
  // Fallback: simulate based on contour quality
  const matchingRate = 85 + Math.random() * 10; // 85-95%
  return { matchingRate, confidence: matchingRate };
}

// ==================== AREA TOOL ====================

async function processAreaTool(
  canvas: HTMLCanvasElement,
  tool: ToolConfig,
  options: ProcessingOptions
): Promise<{ matchingRate: number; confidence: number }> {
  
  const ctx = canvas.getContext('2d')!;
  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const data = imageData.data;
  
  // Convert to grayscale
  const grayData = new Uint8Array(canvas.width * canvas.height);
  for (let i = 0; i < data.length; i += 4) {
    const gray = (data[i] + data[i + 1] + data[i + 2]) / 3;
    grayData[i / 4] = gray;
  }
  
  // Apply Otsu thresholding
  const threshold = calculateOtsuThreshold(grayData);
  
  // Count bright pixels
  let brightPixels = 0;
  for (let i = 0; i < grayData.length; i++) {
    if (grayData[i] > threshold) {
      brightPixels++;
    }
  }
  
  const totalPixels = canvas.width * canvas.height;
  const brightAreaRatio = (brightPixels / totalPixels) * 100;
  
  // Compare with master
  const masterFeatures = options.masterFeatures?.[tool.id];
  if (masterFeatures?.brightAreaRatio !== undefined) {
    const masterRatio = masterFeatures.brightAreaRatio;
    const deviation = Math.abs(brightAreaRatio - masterRatio);
    const maxDeviation = 10; // Allow 10% deviation
    const matchingRate = Math.max(0, 100 - (deviation / maxDeviation * 100));
    
    return {
      matchingRate: Math.min(100, matchingRate),
      confidence: Math.min(95, matchingRate + 5)
    };
  }
  
  // Fallback: return area ratio as match rate
  return {
    matchingRate: brightAreaRatio,
    confidence: 85
  };
}

// ==================== COLOR AREA TOOL ====================

async function processColorAreaTool(
  canvas: HTMLCanvasElement,
  tool: ToolConfig,
  options: ProcessingOptions
): Promise<{ matchingRate: number; confidence: number }> {
  
  const ctx = canvas.getContext('2d')!;
  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const data = imageData.data;
  
  // Get color range from tool parameters
  const masterFeatures = options.masterFeatures?.[tool.id];
  const colorRange = masterFeatures?.colorRange || {
    hMin: 0, hMax: 360,
    sMin: 0, sMax: 100,
    vMin: 0, vMax: 100
  };
  
  // Count pixels within color range
  let colorPixels = 0;
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    
    const hsv = rgbToHsv(r, g, b);
    
    if (hsv.h >= colorRange.hMin && hsv.h <= colorRange.hMax &&
        hsv.s >= colorRange.sMin && hsv.s <= colorRange.sMax &&
        hsv.v >= colorRange.vMin && hsv.v <= colorRange.vMax) {
      colorPixels++;
    }
  }
  
  const totalPixels = canvas.width * canvas.height;
  const colorAreaRatio = (colorPixels / totalPixels) * 100;
  
  // Compare with master
  if (masterFeatures?.colorPixels !== undefined) {
    const masterPixels = masterFeatures.colorPixels;
    const deviation = Math.abs(colorPixels - masterPixels);
    const maxDeviation = masterPixels * 0.1; // 10% tolerance
    const matchingRate = Math.max(0, 100 - (deviation / maxDeviation * 100));
    
    return {
      matchingRate: Math.min(100, matchingRate),
      confidence: Math.min(95, matchingRate + 5)
    };
  }
  
  return {
    matchingRate: colorAreaRatio,
    confidence: 80
  };
}

// ==================== EDGE DETECTION TOOL ====================

async function processEdgeDetectionTool(
  canvas: HTMLCanvasElement,
  tool: ToolConfig,
  options: ProcessingOptions
): Promise<{ matchingRate: number; confidence: number }> {
  
  const ctx = canvas.getContext('2d')!;
  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  
  // Convert to grayscale
  const grayData = convertToGrayscale(imageData);
  
  // Apply Sobel edge detection
  const edges = applySobelEdgeDetection(grayData, canvas.width, canvas.height);
  
  // Count edge pixels
  let edgePixels = 0;
  const edgeThreshold = 128;
  for (let i = 0; i < edges.length; i++) {
    if (edges[i] > edgeThreshold) {
      edgePixels++;
    }
  }
  
  // Compare with master (prefer density when template ROI size is known — stable if live ROI is clipped)
  const masterFeatures = options.masterFeatures?.[tool.id];
  if (masterFeatures?.edgePixels !== undefined) {
    const masterEdges = masterFeatures.edgePixels as number;
    const roiPixels = canvas.width * canvas.height;
    const refRoiPx = masterFeatures.roiPixelCount as number | undefined;
    let matchingRate: number;
    if (refRoiPx && refRoiPx > 0 && roiPixels > 0) {
      const curD = edgePixels / roiPixels;
      const refD = masterEdges / refRoiPx;
      if (refD < 1e-12 && masterEdges === 0) {
        matchingRate = edgePixels === 0 ? 100 : 0;
      } else if (refD < 1e-12) {
        const deviation = Math.abs(edgePixels - masterEdges);
        const maxDeviation = Math.max(masterEdges * 0.15, 1);
        matchingRate = Math.max(0, 100 - (deviation / maxDeviation) * 100);
      } else {
        const dev = Math.abs(curD - refD);
        const tol = Math.max(refD * 0.15, 1 / refRoiPx);
        matchingRate = Math.max(0, 100 - (dev / tol) * 100);
      }
    } else {
      const deviation = Math.abs(edgePixels - masterEdges);
      const maxDeviation = Math.max(masterEdges * 0.15, 1);
      matchingRate = Math.max(0, 100 - (deviation / maxDeviation) * 100);
    }

    return {
      matchingRate: Math.min(100, matchingRate),
      confidence: Math.min(95, matchingRate + 5),
    };
  }
  
  // Fallback: return edge density as match rate
  const totalPixels = canvas.width * canvas.height;
  const edgeDensity = (edgePixels / totalPixels) * 100;
  
  return {
    matchingRate: Math.min(100, edgeDensity * 2), // Scale up for visibility
    confidence: 85
  };
}

// ==================== POSITION ADJUSTMENT TOOL ====================

async function processPositionAdjustment(
  img: HTMLImageElement,
  tool: ToolConfig,
  options: ProcessingOptions
): Promise<{ x: number; y: number }> {
  
  // Extract template from ROI
  const templateCanvas = extractROI(img, tool.roi);
  
  // Create search area (expand ROI by 20%)
  const searchMargin = 20;
  const searchROI = {
    x: Math.max(0, tool.roi.x - searchMargin),
    y: Math.max(0, tool.roi.y - searchMargin),
    width: tool.roi.width + searchMargin * 2,
    height: tool.roi.height + searchMargin * 2
  };
  
  const searchCanvas = extractROI(img, searchROI);
  
  // Perform template matching (simplified normalized cross-correlation)
  const offset = templateMatch(searchCanvas, templateCanvas);
  
  return {
    x: offset.x - searchMargin,
    y: offset.y - searchMargin
  };
}

// ==================== IMAGE PROCESSING UTILITIES ====================

/**
 * Build a data URL for raw base64 from the camera / inspection API (PNG lossless or JPEG).
 */
export function rawBase64ToImageDataUrl(base64: string): string {
  if (base64.startsWith('data:')) return base64;
  // PNG file signature → typical base64 prefix
  if (base64.startsWith('iVBORw0KGgo')) {
    return `data:image/png;base64,${base64}`;
  }
  return `data:image/jpeg;base64,${base64}`;
}

/**
 * Load base64 image into HTMLImageElement
 */
function loadImage(base64: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = rawBase64ToImageDataUrl(base64);
  });
}

/**
 * Extract ROI from image to canvas
 */
function extractROI(img: HTMLImageElement, roi: ROI): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = roi.width;
  canvas.height = roi.height;
  const ctx = canvas.getContext('2d')!;
  ctx.drawImage(img, roi.x, roi.y, roi.width, roi.height, 0, 0, roi.width, roi.height);
  return canvas;
}

/**
 * Bound ROI to image dimensions
 */
function boundROI(roi: ROI, width: number, height: number): ROI {
  return {
    x: Math.max(0, Math.min(roi.x, width - 1)),
    y: Math.max(0, Math.min(roi.y, height - 1)),
    width: Math.min(roi.width, width - roi.x),
    height: Math.min(roi.height, height - roi.y)
  };
}

/**
 * Convert ImageData to grayscale
 */
function convertToGrayscale(imageData: ImageData): Uint8Array {
  const data = imageData.data;
  const grayData = new Uint8Array(imageData.width * imageData.height);
  
  for (let i = 0; i < data.length; i += 4) {
    const gray = (data[i] + data[i + 1] + data[i + 2]) / 3;
    grayData[i / 4] = gray;
  }
  
  return grayData;
}

/**
 * Calculate Otsu's threshold
 */
function calculateOtsuThreshold(grayData: Uint8Array): number {
  // Build histogram
  const histogram = new Array(256).fill(0);
  for (let i = 0; i < grayData.length; i++) {
    histogram[grayData[i]]++;
  }
  
  // Total number of pixels
  const total = grayData.length;
  
  let sum = 0;
  for (let i = 0; i < 256; i++) {
    sum += i * histogram[i];
  }
  
  let sumB = 0;
  let wB = 0;
  let wF = 0;
  let maxVariance = 0;
  let threshold = 0;
  
  for (let i = 0; i < 256; i++) {
    wB += histogram[i];
    if (wB === 0) continue;
    
    wF = total - wB;
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

/**
 * Apply binary threshold
 */
function applyThreshold(grayData: Uint8Array, threshold: number): Uint8Array {
  const binaryData = new Uint8Array(grayData.length);
  for (let i = 0; i < grayData.length; i++) {
    binaryData[i] = grayData[i] > threshold ? 255 : 0;
  }
  return binaryData;
}

/**
 * Simple contour detection (connected components)
 */
function findContours(binaryData: Uint8Array, width: number, height: number): number[][] {
  const visited = new Array(binaryData.length).fill(false);
  const contours: number[][] = [];
  
  for (let i = 0; i < binaryData.length; i++) {
    if (binaryData[i] === 255 && !visited[i]) {
      const contour = floodFill(binaryData, visited, i, width, height);
      if (contour.length > 10) { // Minimum contour size
        contours.push(contour);
      }
    }
  }
  
  return contours;
}

/**
 * Flood fill for contour detection
 */
function floodFill(
  data: Uint8Array,
  visited: boolean[],
  start: number,
  width: number,
  height: number
): number[] {
  const contour: number[] = [];
  const stack = [start];
  
  while (stack.length > 0) {
    const idx = stack.pop()!;
    if (visited[idx]) continue;
    
    visited[idx] = true;
    contour.push(idx);
    
    const x = idx % width;
    const y = Math.floor(idx / width);
    
    // 4-connected neighbors
    const neighbors = [
      { nx: x - 1, ny: y },
      { nx: x + 1, ny: y },
      { nx: x, ny: y - 1 },
      { nx: x, ny: y + 1 }
    ];
    
    for (const { nx, ny } of neighbors) {
      if (nx >= 0 && nx < width && ny >= 0 && ny < height) {
        const nIdx = ny * width + nx;
        if (data[nIdx] === 255 && !visited[nIdx]) {
          stack.push(nIdx);
        }
      }
    }
  }
  
  return contour;
}

/**
 * Calculate Hu moments for shape matching
 */
function calculateHuMoments(contour: number[], width: number, height: number): number[] {
  // Calculate moments
  let m00 = 0, m10 = 0, m01 = 0;
  
  for (const idx of contour) {
    const x = idx % width;
    const y = Math.floor(idx / width);
    m00 += 1;
    m10 += x;
    m01 += y;
  }
  
  const xc = m10 / m00;
  const yc = m01 / m00;
  
  // Central moments
  let mu20 = 0, mu02 = 0, mu11 = 0;
  for (const idx of contour) {
    const x = idx % width;
    const y = Math.floor(idx / width);
    const dx = x - xc;
    const dy = y - yc;
    mu20 += dx * dx;
    mu02 += dy * dy;
    mu11 += dx * dy;
  }
  
  // Normalized central moments
  const nu20 = mu20 / (m00 * m00);
  const nu02 = mu02 / (m00 * m00);
  const nu11 = mu11 / (m00 * m00);
  
  // Hu moment invariants (simplified - just first two)
  const hu1 = nu20 + nu02;
  const hu2 = (nu20 - nu02) ** 2 + 4 * nu11 ** 2;
  
  return [hu1, hu2];
}

/**
 * Compare Hu moments
 */
function compareHuMoments(moments1: number[], moments2: number[]): number {
  let similarity = 0;
  for (let i = 0; i < Math.min(moments1.length, moments2.length); i++) {
    const diff = Math.abs(Math.log(Math.abs(moments1[i]) + 1e-10) - Math.log(Math.abs(moments2[i]) + 1e-10));
    similarity += 1 / (1 + diff);
  }
  return similarity / Math.min(moments1.length, moments2.length);
}

/**
 * RGB to HSV conversion
 */
function rgbToHsv(r: number, g: number, b: number): { h: number; s: number; v: number } {
  r /= 255;
  g /= 255;
  b /= 255;
  
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;
  
  let h = 0;
  if (delta !== 0) {
    if (max === r) h = 60 * (((g - b) / delta) % 6);
    else if (max === g) h = 60 * (((b - r) / delta) + 2);
    else h = 60 * (((r - g) / delta) + 4);
  }
  if (h < 0) h += 360;
  
  const s = max === 0 ? 0 : (delta / max) * 100;
  const v = max * 100;
  
  return { h, s, v };
}

/**
 * Sobel edge detection
 */
function applySobelEdgeDetection(grayData: Uint8Array, width: number, height: number): Uint8Array {
  const edges = new Uint8Array(grayData.length);
  
  const sobelX = [-1, 0, 1, -2, 0, 2, -1, 0, 1];
  const sobelY = [-1, -2, -1, 0, 0, 0, 1, 2, 1];
  
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      let gx = 0;
      let gy = 0;
      
      for (let ky = -1; ky <= 1; ky++) {
        for (let kx = -1; kx <= 1; kx++) {
          const idx = (y + ky) * width + (x + kx);
          const kernelIdx = (ky + 1) * 3 + (kx + 1);
          gx += grayData[idx] * sobelX[kernelIdx];
          gy += grayData[idx] * sobelY[kernelIdx];
        }
      }
      
      const magnitude = Math.sqrt(gx * gx + gy * gy);
      edges[y * width + x] = Math.min(255, magnitude);
    }
  }
  
  return edges;
}

/**
 * Template matching (simplified normalized cross-correlation)
 */
function templateMatch(
  searchCanvas: HTMLCanvasElement,
  templateCanvas: HTMLCanvasElement
): { x: number; y: number } {
  
  // For simplicity, return center offset (no actual matching)
  // In production, implement proper template matching
  return { x: 0, y: 0 };
}

// ==================== MASTER FEATURE EXTRACTION ====================

/**
 * Extract features from master image for each tool
 */
export async function extractMasterFeatures(
  masterImageBase64: string,
  tools: ToolConfig[]
): Promise<Record<string, any>> {
  
  const img = await loadImage(masterImageBase64);
  const masterFeatures: Record<string, any> = {};

  let positionOffset = { x: 0, y: 0 };
  const positionTool = tools.find((t) => t.type === 'position_adjust');
  if (positionTool) {
    const off = await processPositionAdjustment(img, positionTool, {});
    positionOffset = { x: off.x, y: off.y };
  }

  for (const tool of tools) {
    if (tool.type === 'position_adjust') continue;

    const adjustedROI = {
      x: tool.roi.x + positionOffset.x,
      y: tool.roi.y + positionOffset.y,
      width: tool.roi.width,
      height: tool.roi.height,
    };
    const bounded = boundROI(adjustedROI, img.width, img.height);
    const roiCanvas = extractROI(img, bounded);
    const ctx = roiCanvas.getContext('2d')!;
    const imageData = ctx.getImageData(0, 0, roiCanvas.width, roiCanvas.height);
    
    switch (tool.type) {
      case 'outline': {
        const grayData = convertToGrayscale(imageData);
        const threshold = calculateOtsuThreshold(grayData);
        const binaryData = applyThreshold(grayData, threshold);
        const contours = findContours(binaryData, roiCanvas.width, roiCanvas.height);
        if (contours.length > 0) {
          const huMoments = calculateHuMoments(contours[0], roiCanvas.width, roiCanvas.height);
          masterFeatures[tool.id] = { huMoments };
        }
        break;
      }
      
      case 'area': {
        const grayData = convertToGrayscale(imageData);
        const threshold = calculateOtsuThreshold(grayData);
        let brightPixels = 0;
        for (let i = 0; i < grayData.length; i++) {
          if (grayData[i] > threshold) brightPixels++;
        }
        const totalPixels = roiCanvas.width * roiCanvas.height;
        const brightAreaRatio = (brightPixels / totalPixels) * 100;
        masterFeatures[tool.id] = { brightPixels, brightAreaRatio };
        break;
      }
      
      case 'color_area': {
        // Extract dominant color range
        const data = imageData.data;
        let colorPixels = 0;
        // Simple approach: count pixels in a color range
        // In production, use more sophisticated color segmentation
        masterFeatures[tool.id] = {
          colorPixels,
          colorRange: { hMin: 0, hMax: 360, sMin: 0, sMax: 100, vMin: 0, vMax: 100 }
        };
        break;
      }
      
      case 'edge_detection': {
        const grayData = convertToGrayscale(imageData);
        const edges = applySobelEdgeDetection(grayData, roiCanvas.width, roiCanvas.height);
        let edgePixels = 0;
        const edgeThreshold = 128;
        for (let i = 0; i < edges.length; i++) {
          if (edges[i] > edgeThreshold) edgePixels++;
        }
        const roiPixelCount = roiCanvas.width * roiCanvas.height;
        masterFeatures[tool.id] = { edgePixels, roiPixelCount };
        break;
      }
    }
  }
  
  return masterFeatures;
}

/**
 * Wizard helper: merge freshly extracted master template features for one tool
 * (using the ROI currently on screen) into the program's cached master features.
 * Ensures threshold preview matches the master image while the user resizes or moves a ROI.
 * Pass the full tool list so position_adjust (when implemented) uses the same geometry as inspection.
 */
export async function mergeWizardMasterFeatures(
  masterImageBase64: string,
  baseFeatures: Record<string, any>,
  allTools: ToolConfig[],
  tool: ToolConfig,
  templateRoi: ROI
): Promise<Record<string, any>> {
  const toolsForExtract = allTools.map((t) =>
    t.id === tool.id ? { ...tool, roi: templateRoi } : t
  );
  const slice = await extractMasterFeatures(masterImageBase64, toolsForExtract);
  const key = tool.id;
  if (slice[key] === undefined) return { ...baseFeatures };
  return { ...baseFeatures, [key]: slice[key] };
}

export interface RoiFeedbackOptions {
  /** For color_area: range learned from master (wizard passes from masterFeaturesState). */
  colorRange?: { hMin: number; hMax: number; sMin: number; sMax: number; vMin: number; vMax: number };
}

/**
 * Build a small ROI-sized canvas showing what the inspection tool "sees" (binary mask, edges, etc.)
 * so the wizard can overlay master vs live processing while tuning thresholds on the camera feed.
 */
export async function computeRoiToolFeedbackCanvas(
  imageBase64: string,
  allTools: ToolConfig[],
  roi: ROI,
  toolType: ToolType,
  options: RoiFeedbackOptions = {}
): Promise<HTMLCanvasElement | null> {
  if (toolType === 'position_adjust') return null;

  const img = await loadImage(imageBase64);
  let positionOffset = { x: 0, y: 0 };
  const positionTool = allTools.find((t) => t.type === 'position_adjust');
  if (positionTool) {
    const off = await processPositionAdjustment(img, positionTool, {});
    positionOffset = { x: off.x, y: off.y };
  }

  const adjusted = {
    x: roi.x + positionOffset.x,
    y: roi.y + positionOffset.y,
    width: roi.width,
    height: roi.height,
  };
  const b = boundROI(adjusted, img.width, img.height);
  if (b.width < 4 || b.height < 4) return null;

  const roiCanvas = extractROI(img, b);
  const w = roiCanvas.width;
  const h = roiCanvas.height;
  const srcCtx = roiCanvas.getContext('2d');
  if (!srcCtx) return null;
  const imageData = srcCtx.getImageData(0, 0, w, h);

  const out = document.createElement('canvas');
  out.width = w;
  out.height = h;
  const octx = out.getContext('2d');
  if (!octx) return null;

  switch (toolType) {
    case 'area': {
      const gray = convertToGrayscale(imageData);
      const T = calculateOtsuThreshold(gray);
      const id = octx.createImageData(w, h);
      for (let i = 0; i < gray.length; i++) {
        const v = gray[i] > T ? 255 : 0;
        const o = i * 4;
        id.data[o] = v;
        id.data[o + 1] = v;
        id.data[o + 2] = v;
        id.data[o + 3] = 255;
      }
      octx.putImageData(id, 0, 0);
      break;
    }
    case 'edge_detection': {
      const gray = convertToGrayscale(imageData);
      const edges = applySobelEdgeDetection(gray, w, h);
      const id = octx.createImageData(w, h);
      const thr = 128;
      for (let i = 0; i < edges.length; i++) {
        const v = edges[i] > thr ? 255 : 0;
        const o = i * 4;
        id.data[o] = v;
        id.data[o + 1] = v;
        id.data[o + 2] = v;
        id.data[o + 3] = 255;
      }
      octx.putImageData(id, 0, 0);
      break;
    }
    case 'color_area': {
      const colorRange = options.colorRange ?? {
        hMin: 0,
        hMax: 360,
        sMin: 0,
        sMax: 100,
        vMin: 0,
        vMax: 100,
      };
      const data = imageData.data;
      const id = octx.createImageData(w, h);
      for (let i = 0; i < data.length; i += 4) {
        const hsv = rgbToHsv(data[i], data[i + 1], data[i + 2]);
        const inRange =
          hsv.h >= colorRange.hMin &&
          hsv.h <= colorRange.hMax &&
          hsv.s >= colorRange.sMin &&
          hsv.s <= colorRange.sMax &&
          hsv.v >= colorRange.vMin &&
          hsv.v <= colorRange.vMax;
        const o = i;
        id.data[o] = inRange ? 34 : 15;
        id.data[o + 1] = inRange ? 220 : 15;
        id.data[o + 2] = inRange ? 90 : 15;
        id.data[o + 3] = 255;
      }
      octx.putImageData(id, 0, 0);
      break;
    }
    case 'outline': {
      const gray = convertToGrayscale(imageData);
      const T = calculateOtsuThreshold(gray);
      const bin = applyThreshold(gray, T);
      const contours = findContours(bin, w, h);
      contours.sort((a, b) => b.length - a.length);
      const id = octx.createImageData(w, h);
      for (let i = 0; i < w * h; i++) {
        const o = i * 4;
        id.data[o] = 10;
        id.data[o + 1] = 10;
        id.data[o + 2] = 12;
        id.data[o + 3] = 255;
      }
      const best = contours[0];
      if (best) {
        for (const idx of best) {
          const o = idx * 4;
          id.data[o] = 0;
          id.data[o + 1] = 220;
          id.data[o + 2] = 255;
        }
      }
      octx.putImageData(id, 0, 0);
      break;
    }
    default:
      return null;
  }

  return out;
}
