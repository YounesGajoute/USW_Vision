'use client';

import { useState, useRef, useEffect, useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';
import { Badge } from '@/components/ui/badge';
import { Trash2, Check, X, ChevronDown, ChevronUp, Grid3x3, Layers, Move, Image as ImageIcon, Camera, Pause, Play, SlidersHorizontal, LayoutTemplate, Save, Loader2 } from 'lucide-react';
import { useToast } from '@/hooks/use-toast';
import type { ToolConfig, ROI, ToolType, CaptureOptions, ToolTemplateSummary, ToolTemplate } from '@/types';
import { TOOL_TYPES } from '@/types';
import { api } from '@/lib/api';
import { ws } from '@/lib/websocket';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Input } from '@/components/ui/input';
import {
  extractMasterFeatures,
  mergeWizardMasterFeatures,
  previewDetectionToolMatch,
  imageBase64ToWizardFrame640,
  computeRoiToolFeedbackCanvas,
  applyTemplateToolsToWizardCanvas,
  normalizeToolRoisForWizardMasterImage,
  resolveToolRoisForImagePixels,
  clampRoiToWizardCanvas,
  ROI_SPACE_WIZARD,
  ROI_SPACE_NATIVE,
  type PreviewToolMatch,
  type RoiFeedbackOptions,
} from '@/lib/inspection-engine';

interface Step3Props {
  configuredTools: ToolConfig[];
  setConfiguredTools: (tools: ToolConfig[]) => void;
  masterImageData: string | null;
  /** Camera options for live preview (same as Step 1 / program defaults). */
  captureOptions?: CaptureOptions;
  /**
   * After saving a tool template (tools-only, no stored image), clear temporary layout
   * in the wizard. Omit when editing an existing program so master/tools stay loaded.
   */
  onToolTemplateSaved?: () => void;
  /** When editing a program, use server scaling for template apply (master-native ROIs). */
  programId?: number | null;
  /** Program display name — used for owned template title (e.g. test121). */
  programName?: string | null;
}

type BackdropMode = 'master' | 'live';

type EditMode = 'none' | 'drawing' | 'editing';
type ResizeHandle = 'tl' | 'tr' | 'bl' | 'br' | 't' | 'b' | 'l' | 'r' | 'move' | null;

function pointInRoi(x: number, y: number, roi: ROI): boolean {
  return x >= roi.x && x <= roi.x + roi.width && y >= roi.y && y <= roi.y + roi.height;
}

/** Label bar drawn above each ROI (must match drawROIs — width is capped by measureText). */
const ROI_LABEL_W = 168;
const ROI_LABEL_H = 26;
/** Hit target for label bar (max width matches drawRoiLabelBar cap; height fits two-line labels). */
const ROI_LABEL_HIT_W = 300;
const ROI_LABEL_HIT_H = 46;

function pointInRoiLabel(x: number, y: number, roi: ROI): boolean {
  return x >= roi.x && x <= roi.x + ROI_LABEL_HIT_W && y >= roi.y - ROI_LABEL_HIT_H && y <= roi.y;
}

function parseToolHexColor(hex: string): { r: number; g: number; b: number } {
  const h = (hex || '').trim();
  const short = /^#?([0-9a-f]{3})$/i.exec(h);
  if (short) {
    const s = short[1];
    return {
      r: parseInt(s[0] + s[0], 16),
      g: parseInt(s[1] + s[1], 16),
      b: parseInt(s[2] + s[2], 16),
    };
  }
  const m = /^#?([0-9a-f]{6})$/i.exec(h);
  if (m) {
    const s = m[1];
    return {
      r: parseInt(s.slice(0, 2), 16),
      g: parseInt(s.slice(2, 4), 16),
      b: parseInt(s.slice(4, 6), 16),
    };
  }
  return { r: 59, g: 130, b: 246 };
}

type RoiDrawKind = 'saved' | 'tuning' | 'insight' | 'draft' | 'editing';

/** Split so fills can sit under processing feedback while strokes stay readable on top. */
type RoiDecorationLayer = 'fill' | 'stroke' | 'all';

/**
 * High-contrast ROI decoration for video or still master: fill + double stroke + corner ticks.
 * Reads clearly on bright metal, dark shadows, and compressed live JPEG.
 */
function drawInspectionRoiDecoration(
  ctx: CanvasRenderingContext2D,
  roi: ROI,
  color: string,
  kind: RoiDrawKind,
  layer: RoiDecorationLayer = 'all'
) {
  const { x, y, width: w, height: h } = roi;
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(w) || !Number.isFinite(h)) return;
  /** Draft/editing must show hairline drag rects; saved ROIs stay at least 2px so strokes are valid. */
  const minDim = kind === 'draft' || kind === 'editing' ? 1 : 2;
  if (w < minDim || h < minDim) return;

  const drawFill = layer === 'fill' || layer === 'all';
  const drawStroke = layer === 'stroke' || layer === 'all';

  const { r, g, b } = parseToolHexColor(color);
  const tick = Math.max(10, Math.min(22, Math.floor(Math.min(Math.max(w, 2), Math.max(h, 2)) * 0.18)));

  ctx.save();

  if (kind === 'tuning') {
    if (drawFill) {
      ctx.fillStyle = 'rgba(250, 204, 21, 0.14)';
      ctx.fillRect(x, y, w, h);
    }
    if (drawStroke) {
      ctx.setLineDash([10, 7]);
      ctx.strokeStyle = 'rgba(0, 0, 0, 0.55)';
      ctx.lineWidth = 5;
      ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
      ctx.strokeStyle = 'rgba(250, 204, 21, 0.98)';
      ctx.lineWidth = 3;
      ctx.strokeRect(x + 2.5, y + 2.5, w - 5, h - 5);
      ctx.setLineDash([]);
    }
  } else if (kind === 'insight') {
    if (drawFill) {
      ctx.fillStyle = `rgba(${r},${g},${b},0.1)`;
      ctx.fillRect(x, y, w, h);
    }
    if (drawStroke) {
      ctx.strokeStyle = 'rgba(0, 0, 0, 0.55)';
      ctx.lineWidth = 5;
      ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.5;
      ctx.strokeRect(x + 2.5, y + 2.5, w - 5, h - 5);
      ctx.setLineDash([6, 5]);
      ctx.strokeStyle = 'rgba(34, 211, 238, 0.95)';
      ctx.lineWidth = 2;
      ctx.strokeRect(x - 2.5, y - 2.5, w + 5, h + 5);
      ctx.setLineDash([]);
    }
  } else {
    const fillA =
      kind === 'draft' ? 0.2 : kind === 'editing' ? 0.26 : kind === 'saved' ? 0.1 : 0.09;
    if (drawFill) {
      ctx.fillStyle = `rgba(${r},${g},${b},${fillA})`;
      ctx.fillRect(x, y, w, h);
    }
    if (drawStroke) {
      const editingLike = kind === 'draft' || kind === 'editing';
      ctx.strokeStyle = 'rgba(0, 0, 0, 0.65)';
      ctx.lineWidth = editingLike ? 6 : 4;
      ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
      ctx.strokeStyle = color;
      ctx.lineWidth = editingLike ? 4 : 3;
      if (kind === 'draft') {
        ctx.setLineDash([7, 5]);
      } else {
        ctx.setLineDash([]);
      }
      const inset = editingLike ? 3 : 2;
      ctx.strokeRect(x + inset, y + inset, w - inset * 2, h - inset * 2);
      ctx.setLineDash([]);
      if (editingLike && w >= 4 && h >= 4) {
        ctx.strokeStyle = `rgba(${r},${g},${b},0.95)`;
        ctx.lineWidth = 2;
        ctx.strokeRect(x + inset + 2, y + inset + 2, w - (inset + 2) * 2, h - (inset + 2) * 2);
      }
    }
  }

  if (drawStroke) {
    const drawTicks = (lineW: number, stroke: string, inset: number) => {
      ctx.strokeStyle = stroke;
      ctx.lineWidth = lineW;
      ctx.lineCap = 'square';
      const xi = x + inset;
      const yi = y + inset;
      const xo = x + w - inset;
      const yo = y + h - inset;
      const t = Math.max(4, Math.min(tick - inset * 2, Math.floor(Math.min(w, h) * 0.32) - inset * 3));
      ctx.beginPath();
      ctx.moveTo(xi, yi + t);
      ctx.lineTo(xi, yi);
      ctx.lineTo(xi + t, yi);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(xo - t, yi);
      ctx.lineTo(xo, yi);
      ctx.lineTo(xo, yi + t);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(xi, yo - t);
      ctx.lineTo(xi, yo);
      ctx.lineTo(xi + t, yo);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(xo - t, yo);
      ctx.lineTo(xo, yo);
      ctx.lineTo(xo, yo - t);
      ctx.stroke();
    };
    drawTicks(3, 'rgba(0,0,0,0.55)', 0);
    drawTicks(1.5, 'rgba(255,255,255,0.92)', 1);
  }

  ctx.restore();
}

function drawRoiLabelBar(
  ctx: CanvasRenderingContext2D,
  roi: ROI,
  text: string,
  barColor: string,
  sublabel?: string
) {
  const padX = 8;
  ctx.save();
  ctx.font = 'bold 12px system-ui, sans-serif';
  const tw = ctx.measureText(text).width;
  let subW = 0;
  if (sublabel) {
    ctx.font = '10px system-ui, sans-serif';
    subW = ctx.measureText(sublabel).width;
  }
  const barW = Math.min(300, Math.max(ROI_LABEL_W, Math.ceil(Math.max(tw, subW) + padX * 2 + 10)));
  const barH = sublabel ? ROI_LABEL_H + 12 : ROI_LABEL_H;
  const bx = roi.x;
  const by = roi.y - barH;

  ctx.fillStyle = barColor;
  if (typeof ctx.roundRect === 'function') {
    ctx.beginPath();
    ctx.roundRect(bx, by, barW, barH, 4);
    ctx.fill();
  } else {
    ctx.fillRect(bx, by, barW, barH);
  }

  ctx.strokeStyle = 'rgba(255,255,255,0.35)';
  ctx.lineWidth = 1;
  ctx.strokeRect(bx + 0.5, by + 0.5, barW - 1, barH - 1);

  ctx.fillStyle = 'rgba(255,255,255,0.98)';
  ctx.font = 'bold 12px system-ui, sans-serif';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, bx + padX, by + barH / 2 - (sublabel ? 6 : 0));

  if (sublabel) {
    ctx.font = '10px system-ui, sans-serif';
    ctx.fillStyle = 'rgba(255,255,255,0.82)';
    ctx.fillText(sublabel, bx + padX, by + barH / 2 + 8);
  }
  ctx.restore();
}

function findToolAtCoordinates(
  x: number,
  y: number,
  tools: ToolConfig[],
  opts?: { editingId?: string | null; liveRoi?: ROI | null }
): ToolConfig | null {
  for (let i = tools.length - 1; i >= 0; i--) {
    const t = tools[i];
    const roi =
      opts?.editingId === t.id && opts.liveRoi ? opts.liveRoi : t.roi;
    if (pointInRoi(x, y, roi) || pointInRoiLabel(x, y, roi)) {
      return t;
    }
  }
  return null;
}

function drawFeedbackThumb(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  maxW: number,
  maxH: number,
  source: HTMLCanvasElement
) {
  const scale = Math.min(maxW / source.width, maxH / source.height, 2);
  const dw = Math.max(1, Math.floor(source.width * scale));
  const dh = Math.max(1, Math.floor(source.height * scale));
  ctx.imageSmoothingEnabled = false;
  ctx.fillStyle = '#020617';
  ctx.fillRect(x, y, maxW, maxH);
  const ox = x + (maxW - dw) / 2;
  const oy = y + (maxH - dh) / 2;
  ctx.drawImage(source, ox, oy, dw, dh);
  ctx.strokeStyle = 'rgba(255,255,255,0.45)';
  ctx.lineWidth = 1;
  ctx.strokeRect(ox + 0.5, oy + 0.5, dw - 1, dh - 1);
}

function drawRoiProcessingInset(
  ctx: CanvasRenderingContext2D,
  roi: ROI,
  masterFb: HTMLCanvasElement | null,
  liveFb: HTMLCanvasElement | null
) {
  if (!masterFb && !liveFb) return;
  const innerPad = 4;
  const labelH = 11;
  const rowH = Math.min(68, Math.max(36, Math.floor(roi.height * 0.34)));
  if (roi.height < 52 || roi.width < 72) return;

  const plateH = rowH + labelH + innerPad * 2;
  const plateY = roi.y + roi.height - plateH - innerPad;
  const plateX = roi.x + innerPad;
  const plateW = roi.width - innerPad * 2;
  if (plateY < roi.y + 6) return;

  ctx.save();
  ctx.fillStyle = 'rgba(0,0,0,0.82)';
  ctx.strokeStyle = 'rgba(34, 197, 94, 0.9)';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  if (typeof ctx.roundRect === 'function') {
    ctx.roundRect(plateX - 2, plateY - 2, plateW + 4, plateH + 4, 4);
  } else {
    ctx.rect(plateX - 2, plateY - 2, plateW + 4, plateH + 4);
  }
  ctx.fill();
  ctx.stroke();

  ctx.fillStyle = '#e2e8f0';
  ctx.font = 'bold 9px system-ui, sans-serif';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';

  const colW = liveFb && masterFb ? (plateW - innerPad) / 2 : plateW;
  let colX = plateX + innerPad;
  const thumbY = plateY + labelH + 1;

  if (masterFb) {
    ctx.fillText('Master template', colX, plateY + innerPad);
    drawFeedbackThumb(ctx, colX, thumbY, colW - 4, rowH, masterFb);
    colX += colW + innerPad;
  }
  if (liveFb) {
    ctx.fillText('Live camera', colX, plateY + innerPad);
    drawFeedbackThumb(ctx, colX, thumbY, colW - 4, rowH, liveFb);
  }

  ctx.restore();
}

function ThresholdMeter({
  rate,
  threshold: thr,
  upper,
}: {
  rate: number;
  threshold: number;
  upper?: number;
}) {
  return (
    <div className="relative mt-2 h-3 w-full overflow-hidden rounded-full bg-black/15 dark:bg-white/10">
      <div
        className="absolute h-full rounded-l-full bg-primary/80 transition-[width] duration-75 ease-out"
        style={{ width: `${Math.min(100, Math.max(0, rate))}%` }}
      />
      <div
        className="absolute top-0 h-full w-0.5 bg-foreground shadow-sm"
        style={{ left: `calc(${thr}% - 1px)` }}
        title="Threshold"
      />
      {upper != null && (
        <div
          className="absolute top-0 h-full w-0.5 bg-orange-500"
          style={{ left: `calc(${upper}% - 1px)` }}
          title="Upper limit"
        />
      )}
    </div>
  );
}

function cloneToolsWithNewIds(tools: ToolConfig[]): ToolConfig[] {
  return tools.map((tool) => ({
    ...tool,
    id: `${tool.type}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
  }));
}

/** Plain payload for templates: every tool's ROI layout and threshold (and optional limits). */
function snapshotToolsForTemplate(tools: ToolConfig[]): ToolConfig[] {
  return tools.map((t) => ({
    id: t.id,
    type: t.type,
    name: t.name,
    color: t.color,
    roi: {
      x: Math.round(t.roi.x),
      y: Math.round(t.roi.y),
      width: Math.round(t.roi.width),
      height: Math.round(t.roi.height),
    },
    threshold: Math.round(t.threshold),
    ...(t.upperLimit != null ? { upperLimit: Math.round(t.upperLimit) } : {}),
    ...(t.parameters != null && Object.keys(t.parameters).length > 0
      ? { parameters: { ...t.parameters } }
      : {}),
  }));
}

export default function Step3ToolConfiguration({
  configuredTools,
  setConfiguredTools,
  masterImageData,
  onToolTemplateSaved,
  programId = null,
  programName = null,
}: Step3Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const offscreenCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const masterImageRef = useRef<HTMLImageElement | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  
  const [selectedToolType, setSelectedToolType] = useState<ToolType | null>(null);
  const [threshold, setThreshold] = useState([65]);
  const [editMode, setEditMode] = useState<EditMode>('none');
  const [isDrawing, setIsDrawing] = useState(false);
  const [startPoint, setStartPoint] = useState<{ x: number; y: number } | null>(null);
  const [currentRect, setCurrentRect] = useState<ROI | null>(null);
  const [activeHandle, setActiveHandle] = useState<ResizeHandle>(null);
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null);
  const [hoverHandle, setHoverHandle] = useState<ResizeHandle>(null);
  const [needsRedraw, setNeedsRedraw] = useState(false);
  const [showLegend, setShowLegend] = useState(true);
  const [showGrid, setShowGrid] = useState(false);
  const [toolListExpanded, setToolListExpanded] = useState(false);
  const [mousePos, setMousePos] = useState<{ x: number; y: number } | null>(null);
  /** When set, editing mode updates this tool instead of adding a new one. */
  const [editingExistingToolId, setEditingExistingToolId] = useState<string | null>(null);

  const [backdrop, setBackdrop] = useState<BackdropMode>('master');
  const [wizardFrameMaster, setWizardFrameMaster] = useState<string | null>(null);
  const [wizardFrameLive, setWizardFrameLive] = useState<string | null>(null);
  /** Full-resolution live capture (1456×1088); wizardFrameLive is 640×480 display only. */
  const [fullResLiveB64, setFullResLiveB64] = useState<string | null>(null);
  const [livePaused, setLivePaused] = useState(false);
  const [liveCaptureError, setLiveCaptureError] = useState<string | null>(null);
  const [masterFeaturesState, setMasterFeaturesState] = useState<Record<string, unknown>>({});
  /** Match rate vs master template — always from the master image (reference judgment). */
  const [previewMatchMaster, setPreviewMatchMaster] = useState<PreviewToolMatch | null>(null);
  /** Same ROI/threshold evaluated on the latest live frame (runtime sanity check). */
  const [previewMatchLive, setPreviewMatchLive] = useState<PreviewToolMatch | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  /** Latest match % from image processing; updated async. Pass/fail vs slider uses this + current threshold for instant feedback. */
  const [rateSnapshot, setRateSnapshot] = useState<{ master: number; live: number | null } | null>(null);
  /** Tune threshold only (saved ROI) without entering ROI edit mode. */
  const [tuningToolId, setTuningToolId] = useState<string | null>(null);
  /** When set, show master/live threshold feedback for this saved tool (idle wizard — no ROI edit required). */
  const [thresholdInsightToolId, setThresholdInsightToolId] = useState<string | null>(null);

  const [templateSummaries, setTemplateSummaries] = useState<ToolTemplateSummary[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [saveTemplateOpen, setSaveTemplateOpen] = useState(false);
  const [applyTemplateOpen, setApplyTemplateOpen] = useState(false);
  const [templateName, setTemplateName] = useState('');
  const [templateDescription, setTemplateDescription] = useState('');
  const [templateSaving, setTemplateSaving] = useState(false);
  const [templateApplyingId, setTemplateApplyingId] = useState<number | null>(null);
  const [templateDeletingId, setTemplateDeletingId] = useState<number | null>(null);
  const [pendingApplyTemplateId, setPendingApplyTemplateId] = useState<number | null>(null);
  const [templatePreviewImages, setTemplatePreviewImages] = useState<Record<number, string>>({});
  const [applyPreviewTemplate, setApplyPreviewTemplate] = useState<ToolTemplate | null>(null);
  const [applyPreviewLoading, setApplyPreviewLoading] = useState(false);

  const liveImageRef = useRef<HTMLImageElement | null>(null);
  const previewSeq = useRef(0);
  const feedbackSeq = useRef(0);
  const roiFeedbackMasterRef = useRef<HTMLCanvasElement | null>(null);
  const roiFeedbackLiveRef = useRef<HTMLCanvasElement | null>(null);
  const didAutoSwitchLive = useRef(false);
  const livePausedRef = useRef(livePaused);
  const liveFrameBusyRef = useRef(false);

  const { toast } = useToast();

  useEffect(() => {
    livePausedRef.current = livePaused;
  }, [livePaused]);

  const beginRepositionTool = (tool: ToolConfig) => {
    if (editMode === 'drawing' || isDrawing) return;
    setTuningToolId(null);
    setThresholdInsightToolId(null);
    setRateSnapshot(null);
    setEditingExistingToolId(tool.id);
    setCurrentRect({ ...tool.roi });
    setSelectedToolType(tool.type);
    setThreshold([tool.threshold]);
    setEditMode('editing');
    setActiveHandle(null);
    setDragStart(null);
    setIsDrawing(false);
    setStartPoint(null);
  };

  const clearEditingState = () => {
    setEditMode('none');
    setCurrentRect(null);
    setEditingExistingToolId(null);
    setActiveHandle(null);
    setDragStart(null);
    setIsDrawing(false);
    setStartPoint(null);
  };

  // Load master image scaled to wizard canvas (640×480) for ROI alignment, preview, and live toggle baseline
  useEffect(() => {
    if (!masterImageData) {
      masterImageRef.current = null;
      setWizardFrameMaster(null);
      setNeedsRedraw(true);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const norm = await imageBase64ToWizardFrame640(masterImageData);
        if (cancelled) return;
        setWizardFrameMaster(norm);
        const img = new Image();
        img.onload = () => {
          if (cancelled) return;
          masterImageRef.current = img;
          if (!offscreenCanvasRef.current) {
            offscreenCanvasRef.current = document.createElement('canvas');
            offscreenCanvasRef.current.width = 640;
            offscreenCanvasRef.current.height = 480;
          }
          setNeedsRedraw(true);
        };
        img.src = norm;
      } catch {
        if (!cancelled) {
          masterImageRef.current = null;
          setWizardFrameMaster(null);
          setNeedsRedraw(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [masterImageData]);

  const toolRoiSignature = useMemo(
    () =>
      configuredTools
        .map((t) => `${t.id}:${t.roi.x},${t.roi.y},${t.roi.width},${t.roi.height}`)
        .join('|'),
    [configuredTools]
  );

  /**
   * Master on disk may be full resolution while the wizard canvas is always 640×480.
   * If stored ROIs use native pixels, map them once so overlays match the stretched image.
   */
  useEffect(() => {
    if (!masterImageData || configuredTools.length === 0) return;
    let cancelled = false;
    (async () => {
      try {
        const next = await normalizeToolRoisForWizardMasterImage(masterImageData, configuredTools);
        if (cancelled) return;
        const same =
          next.length === configuredTools.length &&
          next.every((t, i) => {
            const a = configuredTools[i].roi;
            const b = t.roi;
            return a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;
          });
        if (!same) setConfiguredTools(next);
      } catch {
        /* ignore decode errors */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [masterImageData, toolRoiSignature, setConfiguredTools]);

  useEffect(() => {
    if (!wizardFrameLive) {
      liveImageRef.current = null;
      setNeedsRedraw(true);
      return;
    }
    const img = new Image();
    img.onload = () => {
      liveImageRef.current = img;
      setNeedsRedraw(true);
    };
    img.src = wizardFrameLive;
  }, [wizardFrameLive]);

  /** Prefer live video once frames arrive so ROI work matches production lighting. */
  useEffect(() => {
    if (wizardFrameLive && !didAutoSwitchLive.current) {
      didAutoSwitchLive.current = true;
      setBackdrop('live');
    }
  }, [wizardFrameLive]);

  useEffect(() => {
    if (tuningToolId && !configuredTools.some((t) => t.id === tuningToolId)) {
      setTuningToolId(null);
      setRateSnapshot(null);
    }
  }, [configuredTools, tuningToolId]);

  /** Default threshold feedback to first detection tool whenever you are idle (not editing ROI / not in sliders-only tuning). */
  useEffect(() => {
    if (editMode !== 'none' || tuningToolId) return;
    const detections = configuredTools.filter((t) => t.type !== 'position_adjust');
    if (detections.length === 0) {
      setThresholdInsightToolId(null);
      return;
    }
    setThresholdInsightToolId((prev) => {
      if (prev && detections.some((t) => t.id === prev)) return prev;
      return detections[0].id;
    });
  }, [configuredTools, editMode, tuningToolId]);

  /** Keep slider aligned with the saved tool when switching insight target. */
  useEffect(() => {
    if (editMode !== 'none' || tuningToolId || !thresholdInsightToolId) return;
    const t = configuredTools.find((x) => x.id === thresholdInsightToolId);
    if (!t || t.type === 'position_adjust') return;
    setThreshold([t.threshold]);
  }, [thresholdInsightToolId, editMode, tuningToolId]);

  useEffect(() => {
    if (!masterImageData || configuredTools.length === 0) {
      setMasterFeaturesState({});
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const toolsNative = await resolveToolRoisForImagePixels(masterImageData, configuredTools);
        const f = await extractMasterFeatures(masterImageData, toolsNative);
        if (!cancelled) setMasterFeaturesState(f);
      } catch {
        if (!cancelled) setMasterFeaturesState({});
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [masterImageData, configuredTools]);

  /** Live wizard preview via WebSocket (not REST /camera/capture — avoids ~1 full sensor grab per second). */
  useEffect(() => {
    if (!wizardFrameMaster || livePaused) return;

    let mounted = true;

    const handleFrame = async (data: { image?: string }) => {
      if (!mounted || livePausedRef.current || !data.image || liveFrameBusyRef.current) return;
      liveFrameBusyRef.current = true;
      try {
        const norm = await imageBase64ToWizardFrame640(data.image);
        if (!mounted || livePausedRef.current) return;
        setFullResLiveB64(data.image);
        setWizardFrameLive(norm);
        setLiveCaptureError(null);
      } catch (e) {
        if (mounted && !livePausedRef.current) {
          setLiveCaptureError(
            e instanceof Error ? e.message : 'Live stream decode failed'
          );
        }
      } finally {
        liveFrameBusyRef.current = false;
      }
    };

    const handleSocketError = (data: { code?: string; message?: string }) => {
      if (!mounted) return;
      if (data.code === 'NO_CAMERA') {
        setLiveCaptureError(data.message ?? 'CSI camera not available.');
      }
    };

    ws.on('live_frame', handleFrame);
    ws.on('error', handleSocketError);
    const cancelPendingSubscribe = ws.subscribeLiveFeedWhenReady(4, true);

    return () => {
      mounted = false;
      cancelPendingSubscribe();
      ws.off('live_frame', handleFrame);
      ws.off('error', handleSocketError);
      ws.unsubscribeLiveFeed();
    };
  }, [wizardFrameMaster, livePaused]);

  // Match preview: ROI edit, tuning mode, or idle insight (saved tools — always see master + live vs threshold).
  useEffect(() => {
    const tuningTool = tuningToolId ? configuredTools.find((t) => t.id === tuningToolId) : undefined;
    const insightTool =
      thresholdInsightToolId && editMode === 'none' && !tuningToolId
        ? configuredTools.find((t) => t.id === thresholdInsightToolId)
        : undefined;

    const previewToolType: ToolType | null =
      editMode !== 'none' || tuningToolId ? selectedToolType : insightTool?.type ?? selectedToolType;

    const allowPreview =
      !!previewToolType &&
      previewToolType !== 'position_adjust' &&
      (editMode !== 'none' || !!tuningTool || !!insightTool);

    if (!allowPreview) {
      setPreviewMatchMaster(null);
      setPreviewMatchLive(null);
      setRateSnapshot(null);
      setPreviewBusy(false);
      return;
    }

    if (!wizardFrameMaster || !masterImageData) {
      setPreviewMatchMaster(null);
      setPreviewMatchLive(null);
      setRateSnapshot(null);
      setPreviewBusy(false);
      return;
    }

    if (tuningToolId && !tuningTool) {
      return;
    }

    if (thresholdInsightToolId && !insightTool && editMode === 'none' && !tuningToolId) {
      setPreviewMatchMaster(null);
      setPreviewMatchLive(null);
      setRateSnapshot(null);
      setPreviewBusy(false);
      return;
    }

    const seq = ++previewSeq.current;
    const timer = window.setTimeout(async () => {
      if (!wizardFrameMaster) {
        if (seq === previewSeq.current) {
          setPreviewMatchMaster(null);
          setPreviewMatchLive(null);
          setRateSnapshot(null);
          setPreviewBusy(false);
        }
        return;
      }

      const roiOk =
        currentRect && currentRect.width > 6 && currentRect.height > 6 ? currentRect : null;

      const existingFromEdit = editingExistingToolId
        ? configuredTools.find((x) => x.id === editingExistingToolId)
        : null;
      const existing = existingFromEdit ?? tuningTool ?? insightTool ?? null;

      if (!existing && !roiOk) {
        if (seq === previewSeq.current) {
          setPreviewMatchMaster(null);
          setPreviewMatchLive(null);
          setRateSnapshot(null);
          setPreviewBusy(false);
        }
        return;
      }

      const tmpl = TOOL_TYPES.find((x) => x.id === previewToolType);
      if (!tmpl) {
        if (seq === previewSeq.current) setPreviewBusy(false);
        return;
      }

      const targetTool: ToolConfig = existing
        ? {
            ...existing,
            threshold: threshold[0],
            roi: roiOk ? { ...roiOk } : { ...existing.roi },
          }
        : {
            id: '__wizard_preview__',
            type: previewToolType,
            name: tmpl.name,
            color: tmpl.color,
            roi: roiOk!,
            threshold: threshold[0],
            upperLimit: undefined,
          };

      const roiOverride: ROI | null = roiOk;
      const templateRoi = roiOk ?? existing!.roi;

      if (seq === previewSeq.current) setPreviewBusy(true);
      try {
        const toolsNative = await resolveToolRoisForImagePixels(masterImageData, configuredTools);
        const [targetNativeScaled] = await resolveToolRoisForImagePixels(masterImageData, [targetTool]);
        const targetNative = targetNativeScaled ?? targetTool;
        const roiOverrideNative = roiOverride
          ? (await resolveToolRoisForImagePixels(masterImageData, [{ ...targetTool, roi: roiOverride }]))[0]?.roi ??
            null
          : null;

        const merged = await mergeWizardMasterFeatures(
          masterImageData,
          masterFeaturesState as Record<string, any>,
          toolsNative,
          targetNative,
          targetNative.roi
        );

        const opts = { masterFeatures: merged };

        const masterResult = await previewDetectionToolMatch(
          masterImageData,
          toolsNative,
          targetNative,
          roiOverrideNative,
          threshold[0],
          opts
        );

        let liveResult: PreviewToolMatch | null = null;
        if (fullResLiveB64) {
          const toolsLive = await resolveToolRoisForImagePixels(fullResLiveB64, configuredTools);
          const [targetLive] = await resolveToolRoisForImagePixels(fullResLiveB64, [targetTool]);
          const liveTarget = targetLive ?? targetTool;
          const roiLive = roiOverride
            ? (await resolveToolRoisForImagePixels(fullResLiveB64, [{ ...targetTool, roi: roiOverride }]))[0]?.roi ??
              null
            : null;
          liveResult = await previewDetectionToolMatch(
            fullResLiveB64,
            toolsLive,
            liveTarget,
            roiLive,
            threshold[0],
            opts
          );
        }

        if (seq !== previewSeq.current) return;

        setPreviewMatchMaster(masterResult);
        setPreviewMatchLive(liveResult);
        setRateSnapshot({
          master: masterResult.matching_rate,
          live: liveResult?.matching_rate ?? null,
        });
      } catch {
        if (seq === previewSeq.current) {
          setPreviewMatchMaster(null);
          setPreviewMatchLive(null);
          setRateSnapshot(null);
        }
      } finally {
        if (seq === previewSeq.current) setPreviewBusy(false);
      }
    }, 32);

    return () => {
      window.clearTimeout(timer);
    };
  }, [
    selectedToolType,
    editMode,
    threshold,
    currentRect,
    editingExistingToolId,
    tuningToolId,
    thresholdInsightToolId,
    configuredTools,
    wizardFrameMaster,
    wizardFrameLive,
    masterImageData,
    fullResLiveB64,
    masterFeaturesState,
  ]);

  // ROI-sized processing previews — include idle insight (saved ROI) so mask matches the tool you are judging.
  useEffect(() => {
    const tuningTool = tuningToolId ? configuredTools.find((t) => t.id === tuningToolId) : undefined;
    const insightTf =
      thresholdInsightToolId && editMode === 'none' && !tuningToolId
        ? configuredTools.find((t) => t.id === thresholdInsightToolId)
        : undefined;
    const roiFromTune = tuningTool?.roi;
    const roiFromInsight = insightTf?.roi;
    const roiFromEdit =
      editMode !== 'none' && currentRect && currentRect.width >= 8 && currentRect.height >= 8
        ? currentRect
        : null;
    const roi = roiFromEdit ?? roiFromTune ?? roiFromInsight ?? null;
    const feedbackToolType = insightTf?.type ?? selectedToolType;

    const active =
      !!roi &&
      !!feedbackToolType &&
      feedbackToolType !== 'position_adjust' &&
      (editMode !== 'none' || !!tuningTool || !!insightTf);

    if (!active || !wizardFrameMaster || !feedbackToolType) {
      roiFeedbackMasterRef.current = null;
      roiFeedbackLiveRef.current = null;
      setNeedsRedraw(true);
      return;
    }

    const seq = ++feedbackSeq.current;
    const colorOpts: RoiFeedbackOptions = {};
    const colorFeatureId = editingExistingToolId ?? tuningToolId ?? thresholdInsightToolId;
    if (feedbackToolType === 'color_area' && colorFeatureId) {
      const raw = (masterFeaturesState as Record<string, any>)[colorFeatureId];
      if (raw?.colorRange) colorOpts.colorRange = raw.colorRange;
    }

    const t = window.setTimeout(async () => {
      try {
        const masterCanvas = await computeRoiToolFeedbackCanvas(
          wizardFrameMaster,
          configuredTools,
          roi,
          feedbackToolType,
          colorOpts
        );
        let liveCanvas: HTMLCanvasElement | null = null;
        if (wizardFrameLive) {
          liveCanvas = await computeRoiToolFeedbackCanvas(
            wizardFrameLive,
            configuredTools,
            roi,
            feedbackToolType,
            colorOpts
          );
        }
        if (seq !== feedbackSeq.current) return;
        roiFeedbackMasterRef.current = masterCanvas;
        roiFeedbackLiveRef.current = liveCanvas;
        setNeedsRedraw(true);
      } catch {
        if (seq === feedbackSeq.current) {
          roiFeedbackMasterRef.current = null;
          roiFeedbackLiveRef.current = null;
          setNeedsRedraw(true);
        }
      }
    }, 100);

    return () => window.clearTimeout(t);
  }, [
    wizardFrameMaster,
    wizardFrameLive,
    configuredTools,
    currentRect,
    tuningToolId,
    thresholdInsightToolId,
    editMode,
    selectedToolType,
    editingExistingToolId,
    masterFeaturesState,
  ]);

  // Optimized rendering loop — include canvas-driving state so in-flight rAF is not stale vs currentRect / backdrop.
  useEffect(() => {
    if (!needsRedraw) return;
    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
    }
    animationFrameRef.current = requestAnimationFrame(() => {
      drawCanvas();
      setNeedsRedraw(false);
    });
    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [
    needsRedraw,
    currentRect,
    editMode,
    hoverHandle,
    configuredTools,
    backdrop,
    showGrid,
    showLegend,
    editingExistingToolId,
    wizardFrameMaster,
    wizardFrameLive,
    selectedToolType,
    tuningToolId,
    thresholdInsightToolId,
  ]);

  // Trigger redraw on state changes
  useEffect(() => {
    setNeedsRedraw(true);
  }, [
    configuredTools,
    currentRect,
    editMode,
    hoverHandle,
    showGrid,
    showLegend,
    editingExistingToolId,
    backdrop,
    wizardFrameMaster,
    wizardFrameLive,
    selectedToolType,
    tuningToolId,
    thresholdInsightToolId,
    mousePos,
  ]);

  const drawCanvas = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) return;

    const offscreen = offscreenCanvasRef.current;
    const offscreenCtx = offscreen?.getContext('2d', { alpha: false });
    
    const drawingContext = offscreenCtx || ctx;
    const targetCanvas = offscreen || canvas;

    // Clear canvas
    drawingContext.fillStyle = '#1e293b';
    drawingContext.fillRect(0, 0, 640, 480);

    // Draw backdrop: live camera (normalized) or master image
    if (backdrop === 'live' && liveImageRef.current) {
      drawingContext.drawImage(liveImageRef.current, 0, 0, 640, 480);
    } else if (masterImageRef.current) {
      drawingContext.drawImage(masterImageRef.current, 0, 0, 640, 480);
    } else {
      drawingContext.fillStyle = '#64748b';
      drawingContext.font = '20px sans-serif';
      drawingContext.textAlign = 'center';
      drawingContext.fillText(
        backdrop === 'live' ? 'Live view — waiting for camera…' : 'No master image',
        320,
        240
      );
    }

    // Draw grid overlay
    if (showGrid) {
      drawGrid(drawingContext);
    }

    // ROI fills under processing feedback; strokes/labels drawn again after feedback so borders stay visible.
    drawROIs(drawingContext, 'fill');

    // Processing mask inside active ROI (same algorithms as inspection) — tune threshold vs master on live video
    const tuningToolDraw = tuningToolId ? configuredTools.find((t) => t.id === tuningToolId) : undefined;
    const insightDraw =
      thresholdInsightToolId && editMode === 'none' && !tuningToolId
        ? configuredTools.find((t) => t.id === thresholdInsightToolId)
        : undefined;
    const roiTuneDraw = tuningToolDraw?.roi;
    const roiInsightDraw = insightDraw?.roi;
    const roiEditDraw =
      editMode !== 'none' &&
      currentRect &&
      currentRect.width >= 8 &&
      currentRect.height >= 8
        ? currentRect
        : null;
    const drawFeedbackType = insightDraw?.type ?? selectedToolType;
    const activeFeedbackRoi =
      drawFeedbackType && drawFeedbackType !== 'position_adjust'
        ? roiEditDraw ?? roiTuneDraw ?? roiInsightDraw ?? null
        : null;

    if (activeFeedbackRoi) {
      const mFb = roiFeedbackMasterRef.current;
      const lFb = roiFeedbackLiveRef.current;
      const fbSource =
        backdrop === 'live' && lFb ? lFb : backdrop === 'master' && mFb ? mFb : lFb || mFb;
      if (fbSource) {
        drawingContext.save();
        drawingContext.beginPath();
        drawingContext.rect(
          activeFeedbackRoi.x,
          activeFeedbackRoi.y,
          activeFeedbackRoi.width,
          activeFeedbackRoi.height
        );
        drawingContext.clip();
        drawingContext.globalAlpha = backdrop === 'live' ? 0.4 : 0.34;
        drawingContext.globalCompositeOperation = 'screen';
        drawingContext.drawImage(
          fbSource,
          activeFeedbackRoi.x,
          activeFeedbackRoi.y,
          activeFeedbackRoi.width,
          activeFeedbackRoi.height
        );
        drawingContext.restore();
      }

      if (mFb || lFb) {
        drawRoiProcessingInset(drawingContext, activeFeedbackRoi, mFb, lFb);
      }
    }

    drawROIs(drawingContext, 'stroke');

    // Draw legend overlay
    if (showLegend && configuredTools.length > 0) {
      drawLegend(drawingContext);
    }

    // Draw mouse coordinates overlay
    if (mousePos && selectedToolType) {
      drawMouseCoordinates(drawingContext);
    }

    // Copy to main canvas
    if (offscreen && offscreenCtx) {
      ctx.drawImage(offscreen, 0, 0);
    }
  };

  const drawGrid = (ctx: CanvasRenderingContext2D) => {
    ctx.save();
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.15)';
    ctx.lineWidth = 1;
    
    // Vertical lines
    for (let x = 0; x <= 640; x += 40) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, 480);
      ctx.stroke();
    }
    
    // Horizontal lines
    for (let y = 0; y <= 480; y += 40) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(640, y);
      ctx.stroke();
    }
    
    ctx.restore();
  };

  const drawLegend = (ctx: CanvasRenderingContext2D) => {
    ctx.save();
    
    // Background
    ctx.fillStyle = 'rgba(0, 0, 0, 0.75)';
    ctx.fillRect(10, 10, 200, 30 + (configuredTools.length * 25));
    
    // Border
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
    ctx.lineWidth = 1;
    ctx.strokeRect(10, 10, 200, 30 + (configuredTools.length * 25));
    
    // Title
    ctx.fillStyle = 'white';
    ctx.font = 'bold 12px sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('Legend', 20, 28);
    
    // Tools
    ctx.font = '11px sans-serif';
    configuredTools.forEach((tool, index) => {
      const y = 48 + (index * 25);
      
      // Color indicator
      ctx.fillStyle = tool.color;
      ctx.fillRect(20, y - 8, 12, 12);
      ctx.strokeStyle = 'white';
      ctx.lineWidth = 1;
      ctx.strokeRect(20, y - 8, 12, 12);
      
      // Tool name
      ctx.fillStyle = 'white';
      ctx.fillText(`${index + 1}. ${tool.name}`, 38, y);
    });
    
    ctx.restore();
  };

  const drawMouseCoordinates = (ctx: CanvasRenderingContext2D) => {
    if (!mousePos) return;
    
    ctx.save();
    
    const text = `X:${Math.round(mousePos.x)} Y:${Math.round(mousePos.y)}`;
    ctx.font = '12px monospace';
    const metrics = ctx.measureText(text);
    const padding = 8;
    const width = metrics.width + (padding * 2);
    const height = 24;
    
    // Position at top right
    const x = 640 - width - 10;
    const y = 10;
    
    // Background
    ctx.fillStyle = 'rgba(0, 0, 0, 0.75)';
    ctx.fillRect(x, y, width, height);
    
    // Border
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
    ctx.lineWidth = 1;
    ctx.strokeRect(x, y, width, height);
    
    // Text
    ctx.fillStyle = 'white';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x + padding, y + height / 2);
    
    ctx.restore();
  };

  const drawROIs = (ctx: CanvasRenderingContext2D, layer: RoiDecorationLayer = 'all') => {
    ctx.save();

    const drawLabels = layer === 'stroke' || layer === 'all';

    // Draw tuning target last so its emphasis sits above sibling ROIs.
    const orderedTools = [...configuredTools].sort((a, b) => {
      const aTune = tuningToolId === a.id && editMode === 'none';
      const bTune = tuningToolId === b.id && editMode === 'none';
      if (aTune && !bTune) return 1;
      if (!aTune && bTune) return -1;
      return 0;
    });

    orderedTools.forEach((tool) => {
      if (editingExistingToolId === tool.id && editMode === 'editing' && currentRect) {
        return;
      }
      const listIndex = configuredTools.indexOf(tool);
      const idx = listIndex >= 0 ? listIndex + 1 : 1;

      const isTuning = tuningToolId === tool.id && editMode === 'none';
      const isInsight =
        !isTuning &&
        thresholdInsightToolId === tool.id &&
        editMode === 'none' &&
        !tuningToolId;

      let kind: RoiDrawKind = 'saved';
      if (isTuning) kind = 'tuning';
      else if (isInsight) kind = 'insight';

      const roiDraw = clampRoiToWizardCanvas(tool.roi);
      drawInspectionRoiDecoration(ctx, roiDraw, tool.color, kind, layer);

      if (drawLabels) {
        const title = `${idx}. ${tool.name}`;
        let sub: string | undefined;
        if (isTuning) sub = 'Threshold tuning — use slider below';
        else if (isInsight)
          sub =
            backdrop === 'live'
              ? 'Match preview · judging on live frame'
              : 'Match preview · judging on master frame';

        drawRoiLabelBar(ctx, roiDraw, title, tool.color, sub);
      }
    });

    // New ROI or repositioning (same 640×480 space as master / live wizard frames)
    if (currentRect && selectedToolType) {
      const tool = TOOL_TYPES.find((t) => t.id === selectedToolType);
      if (tool && currentRect.width >= 1 && currentRect.height >= 1) {
        const kind: RoiDrawKind = editMode === 'drawing' ? 'draft' : 'editing';
        const rectDraw = clampRoiToWizardCanvas(currentRect);
        drawInspectionRoiDecoration(ctx, rectDraw, tool.color, kind, layer);

        if (drawLabels && editMode === 'editing') {
          drawResizeHandles(ctx, rectDraw, tool.color);
        }

        if (drawLabels) {
          const existing = editingExistingToolId
            ? configuredTools.find((t) => t.id === editingExistingToolId)
            : null;
          const idx =
            existing != null ? Math.max(1, configuredTools.findIndex((t) => t.id === existing.id) + 1) : null;
          const title = existing ? `${idx}. ${existing.name}` : `New · ${tool.name}`;
          const dims =
            currentRect.width >= 8 && currentRect.height >= 8
              ? `${Math.round(currentRect.width)}×${Math.round(currentRect.height)} px`
              : 'Drag to set size';
          drawRoiLabelBar(ctx, rectDraw, title, tool.color, dims);
        }
      }
    }

    ctx.restore();
  };

  const drawResizeHandles = (ctx: CanvasRenderingContext2D, roi: ROI, color: string) => {
    const handleSize = 12;
    const halfSize = handleSize / 2;
    
    const midX = roi.x + roi.width / 2;
    const midY = roi.y + roi.height / 2;
    const rightX = roi.x + roi.width;
    const bottomY = roi.y + roi.height;
    
    const handles = [
      { x: roi.x, y: roi.y },
      { x: rightX, y: roi.y },
      { x: roi.x, y: bottomY },
      { x: rightX, y: bottomY },
      { x: midX, y: roi.y },
      { x: midX, y: bottomY },
      { x: roi.x, y: midY },
      { x: rightX, y: midY },
    ];

    ctx.save();
    ctx.fillStyle = color;
    ctx.strokeStyle = 'rgba(0,0,0,0.75)';
    ctx.lineWidth = 2;
    ctx.shadowColor = 'rgba(0,0,0,0.55)';
    ctx.shadowBlur = 4;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 1;

    handles.forEach(handle => {
      const hx = handle.x - halfSize;
      const hy = handle.y - halfSize;
      ctx.fillRect(hx, hy, handleSize, handleSize);
      ctx.strokeRect(hx, hy, handleSize, handleSize);
    });

    ctx.shadowBlur = 0;
    ctx.shadowOffsetY = 0;
    ctx.restore();
  };

  const getHandleAtPosition = (x: number, y: number, roi: ROI): ResizeHandle => {
    const tolerance = 12;

    if (Math.abs(x - roi.x) <= tolerance && Math.abs(y - roi.y) <= tolerance) return 'tl';
    if (Math.abs(x - (roi.x + roi.width)) <= tolerance && Math.abs(y - roi.y) <= tolerance) return 'tr';
    if (Math.abs(x - roi.x) <= tolerance && Math.abs(y - (roi.y + roi.height)) <= tolerance) return 'bl';
    if (Math.abs(x - (roi.x + roi.width)) <= tolerance && Math.abs(y - (roi.y + roi.height)) <= tolerance) return 'br';

    if (Math.abs(x - (roi.x + roi.width / 2)) <= tolerance && Math.abs(y - roi.y) <= tolerance) return 't';
    if (Math.abs(x - (roi.x + roi.width / 2)) <= tolerance && Math.abs(y - (roi.y + roi.height)) <= tolerance) return 'b';
    if (Math.abs(x - roi.x) <= tolerance && Math.abs(y - (roi.y + roi.height / 2)) <= tolerance) return 'l';
    if (Math.abs(x - (roi.x + roi.width)) <= tolerance && Math.abs(y - (roi.y + roi.height / 2)) <= tolerance) return 'r';

    if (x >= roi.x && x <= roi.x + roi.width && y >= roi.y && y <= roi.y + roi.height) return 'move';

    return null;
  };

  const getCanvasCoordinates = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return null;

    const rect = canvas.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return null;
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    if (!Number.isFinite(scaleX) || !Number.isFinite(scaleY)) return null;
    const x = (e.clientX - rect.left) * scaleX;
    const y = (e.clientY - rect.top) * scaleY;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;

    return { x, y };
  };

  const handleCanvasMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const coords = getCanvasCoordinates(e);
    if (!coords) return;
    const { x, y } = coords;

    // 1) Resize / move active ROI
    if (editMode === 'editing' && currentRect) {
      const handle = getHandleAtPosition(x, y, currentRect);
      if (handle) {
        setActiveHandle(handle);
        setDragStart({ x, y });
        return;
      }
      // Click outside current ROI while repositioning an existing tool
      if (editingExistingToolId) {
        const hit = findToolAtCoordinates(x, y, configuredTools, {
          editingId: editingExistingToolId,
          liveRoi: currentRect,
        });
        if (hit && hit.id !== editingExistingToolId) {
          beginRepositionTool(hit);
          return;
        }
        clearEditingState();
        return;
      }
      // New-tool ROI: click outside — ignore (use Cancel)
      return;
    }

    // 2) Idle: click an existing ROI or its label to reposition
    if (editMode === 'none' && configuredTools.length > 0) {
      const hit = findToolAtCoordinates(x, y, configuredTools);
      if (hit) {
        beginRepositionTool(hit);
        return;
      }
    }

    if (!selectedToolType) {
      toast({
        title: "No Tool Selected",
        description: "Select a tool type to draw a new ROI, or click an existing region to move it",
        variant: "destructive",
      });
      return;
    }

    if (editMode !== 'none') {
      return;
    }

    // Check constraints
    if (selectedToolType === 'position_adjust') {
      const posCount = configuredTools.filter(t => t.type === 'position_adjust').length;
      if (posCount >= 1) {
        toast({
          title: "Limit Reached",
          description: "Maximum 1 position adjustment tool allowed",
          variant: "destructive",
        });
        return;
      }
    }

    if (configuredTools.length >= 16) {
      toast({
        title: "Limit Reached",
        description: "Maximum 16 tools allowed per program",
        variant: "destructive",
      });
      return;
    }

    setTuningToolId(null);
    setRateSnapshot(null);
    setEditMode('drawing');
    setIsDrawing(true);
    setStartPoint({ x, y });
    setCurrentRect({ x, y, width: 0, height: 0 });
  };

  const handleCanvasMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const coords = getCanvasCoordinates(e);
    if (!coords) return;
    const { x, y } = coords;

    // Update mouse position for coordinates display
    setMousePos({ x, y });

    if (editMode === 'editing' && currentRect && !activeHandle) {
      const handle = getHandleAtPosition(x, y, currentRect);
      if (handle !== hoverHandle) {
        setHoverHandle(handle);
      }
    }

    if (isDrawing && startPoint && editMode === 'drawing') {
      const width = x - startPoint.x;
      const height = y - startPoint.y;

      const newRect = {
        x: width < 0 ? x : startPoint.x,
        y: height < 0 ? y : startPoint.y,
        width: Math.abs(width),
        height: Math.abs(height),
      };
      
      setCurrentRect(newRect);
      setNeedsRedraw(true);
      return;
    }

    if (activeHandle && dragStart && currentRect && editMode === 'editing') {
      const dx = x - dragStart.x;
      const dy = y - dragStart.y;

      let newRect = { ...currentRect };

      switch (activeHandle) {
        case 'tl':
          newRect = {
            x: currentRect.x + dx,
            y: currentRect.y + dy,
            width: currentRect.width - dx,
            height: currentRect.height - dy,
          };
          break;
        case 'tr':
          newRect = {
            x: currentRect.x,
            y: currentRect.y + dy,
            width: currentRect.width + dx,
            height: currentRect.height - dy,
          };
          break;
        case 'bl':
          newRect = {
            x: currentRect.x + dx,
            y: currentRect.y,
            width: currentRect.width - dx,
            height: currentRect.height + dy,
          };
          break;
        case 'br':
          newRect = {
            x: currentRect.x,
            y: currentRect.y,
            width: currentRect.width + dx,
            height: currentRect.height + dy,
          };
          break;
        case 't':
          newRect = {
            x: currentRect.x,
            y: currentRect.y + dy,
            width: currentRect.width,
            height: currentRect.height - dy,
          };
          break;
        case 'b':
          newRect = {
            x: currentRect.x,
            y: currentRect.y,
            width: currentRect.width,
            height: currentRect.height + dy,
          };
          break;
        case 'l':
          newRect = {
            x: currentRect.x + dx,
            y: currentRect.y,
            width: currentRect.width - dx,
            height: currentRect.height,
          };
          break;
        case 'r':
          newRect = {
            x: currentRect.x,
            y: currentRect.y,
            width: currentRect.width + dx,
            height: currentRect.height,
          };
          break;
        case 'move':
          newRect = {
            x: currentRect.x + dx,
            y: currentRect.y + dy,
            width: currentRect.width,
            height: currentRect.height,
          };
          break;
      }

      // Ensure positive dimensions
      if (newRect.width < 0) {
        newRect.x += newRect.width;
        newRect.width = Math.abs(newRect.width);
      }
      if (newRect.height < 0) {
        newRect.y += newRect.height;
        newRect.height = Math.abs(newRect.height);
      }

      setCurrentRect(newRect);
      setDragStart({ x, y });
      setNeedsRedraw(true);
    }
  };

  const handleCanvasMouseUp = () => {
    if (isDrawing && currentRect && editMode === 'drawing') {
      setIsDrawing(false);
      setStartPoint(null);

      if (currentRect.width > 10 && currentRect.height > 10) {
        setEditMode('editing');
        toast({
          title: "ROI Created",
          description: "Adjust the ROI or click 'Save Tool' to confirm",
        });
      } else {
        setCurrentRect(null);
        setEditMode('none');
      }
      return;
    }

    if (activeHandle && editMode === 'editing') {
      setActiveHandle(null);
      setDragStart(null);
    }
  };

  const handleCanvasMouseLeave = () => {
    setMousePos(null);
    setHoverHandle(null);
    
    if (isDrawing || activeHandle) {
      handleCanvasMouseUp();
    }
  };

  const handleSaveTool = () => {
    if (!currentRect || !selectedToolType) return;

    if (editingExistingToolId) {
      setConfiguredTools(
        configuredTools.map(t =>
          t.id === editingExistingToolId
            ? { ...t, roi: { ...currentRect }, threshold: threshold[0] }
            : t
        )
      );
      toast({
        title: "Position saved",
        description: "Tool region and threshold updated",
      });
      clearEditingState();
      return;
    }

    const tool = TOOL_TYPES.find(t => t.id === selectedToolType);
    if (tool) {
      const newTool: ToolConfig = {
        id: `${selectedToolType}-${Date.now()}`,
        type: selectedToolType,
        name: tool.name,
        color: tool.color,
        roi: currentRect,
        threshold: threshold[0],
      };
      setConfiguredTools([...configuredTools, newTool]);

      toast({
        title: "Tool Added",
        description: `${tool.name} configured successfully`,
      });

      setEditMode('none');
      setCurrentRect(null);
      setTuningToolId(null);
      setRateSnapshot(null);
    }
  };

  const handleCancelTool = () => {
    const wasReposition = !!editingExistingToolId;
    clearEditingState();

    toast({
      title: "Cancelled",
      description: wasReposition ? "Reposition cancelled" : "Tool creation cancelled",
    });
  };

  const handleDeleteTool = (id: string) => {
    if (id === editingExistingToolId) {
      clearEditingState();
    }
    if (id === tuningToolId) {
      setTuningToolId(null);
      setRateSnapshot(null);
    }
    if (id === thresholdInsightToolId) {
      setThresholdInsightToolId(null);
      setRateSnapshot(null);
    }
    setConfiguredTools(configuredTools.filter(t => t.id !== id));
    toast({
      title: "Tool Removed",
      description: "Tool configuration deleted",
    });
  };

  const applyThresholdTuning = () => {
    const id = tuningToolId ?? thresholdInsightToolId;
    if (!id) return;
    setConfiguredTools(
      configuredTools.map((t) => (t.id === id ? { ...t, threshold: threshold[0] } : t))
    );
    toast({
      title: 'Threshold saved',
      description: 'Stored on this tool.',
    });
    if (tuningToolId) {
      setTuningToolId(null);
      setSelectedToolType(null);
    }
    setRateSnapshot(null);
  };

  const startThresholdTuning = (tool: ToolConfig) => {
    if (tool.type === 'position_adjust' || editMode !== 'none' || tuningToolId) return;
    setThresholdInsightToolId(null);
    setTuningToolId(tool.id);
    setSelectedToolType(tool.type);
    setThreshold([tool.threshold]);
  };

  const handleClearSelection = () => {
    setSelectedToolType(null);
    setTuningToolId(null);
    setThresholdInsightToolId(null);
    setRateSnapshot(null);
    clearEditingState();
    setThreshold([65]);
  };

  const getCursorStyle = () => {
    if (editMode === 'editing') {
      if (hoverHandle === 'tl' || hoverHandle === 'br') return 'cursor-nwse-resize';
      if (hoverHandle === 'tr' || hoverHandle === 'bl') return 'cursor-nesw-resize';
      if (hoverHandle === 't' || hoverHandle === 'b') return 'cursor-ns-resize';
      if (hoverHandle === 'l' || hoverHandle === 'r') return 'cursor-ew-resize';
      if (hoverHandle === 'move') return 'cursor-move';
      return 'cursor-default';
    }
    if (editMode === 'drawing') {
      return 'cursor-crosshair';
    }
    if (selectedToolType) return 'cursor-crosshair';
    if (configuredTools.length > 0) return 'cursor-pointer';
    return 'cursor-default';
  };

  // Count tools by type
  const getToolCount = (toolType: ToolType) => {
    return configuredTools.filter(t => t.type === toolType).length;
  };

  const isToolDisabled = (toolType: ToolType) => {
    if (editMode !== 'none') return true;
    if (tuningToolId) return true;
    if (toolType === 'position_adjust' && getToolCount(toolType) >= 1) return true;
    if (configuredTools.length >= 16) return true;
    return false;
  };

  const activeContextTool = useMemo(() => {
    if (editingExistingToolId) {
      return configuredTools.find((t) => t.id === editingExistingToolId) ?? null;
    }
    if (editMode !== 'none') {
      return null;
    }
    if (tuningToolId) {
      return configuredTools.find((t) => t.id === tuningToolId) ?? null;
    }
    if (thresholdInsightToolId) {
      return configuredTools.find((t) => t.id === thresholdInsightToolId) ?? null;
    }
    return null;
  }, [editingExistingToolId, tuningToolId, thresholdInsightToolId, configuredTools, editMode]);

  const panelToolType = useMemo(
    () => activeContextTool?.type ?? selectedToolType,
    [activeContextTool, selectedToolType]
  );

  const passMeta = useMemo(() => {
    const upper = activeContextTool?.upperLimit;
    const thr = threshold[0];
    const mRate = rateSnapshot?.master ?? previewMatchMaster?.matching_rate ?? null;
    const lRate = rateSnapshot?.live ?? previewMatchLive?.matching_rate ?? null;
    const mEdgeFill = previewMatchMaster?.edge_density_percent ?? null;
    const lEdgeFill = previewMatchLive?.edge_density_percent ?? null;
    const judge = (rate: number | null): boolean | null => {
      if (rate == null) return null;
      if (upper !== undefined) return rate >= thr && rate <= upper;
      return rate >= thr;
    };
    return {
      mRate,
      lRate,
      mEdgeFill,
      lEdgeFill,
      mPass: judge(mRate),
      lPass: judge(lRate),
      mMargin: mRate != null ? mRate - thr : null,
      lMargin: lRate != null ? lRate - thr : null,
      upper,
    };
  }, [
    activeContextTool,
    threshold,
    rateSnapshot,
    previewMatchMaster,
    previewMatchLive,
  ]);

  const combinedVerdict = useMemo(() => {
    const m = passMeta.mPass;
    const l = passMeta.lPass;
    if (m == null && l == null) {
      return { text: 'Set a master image (step 1) and wait for readings.', tone: 'muted' as const };
    }
    if (m != null && l == null) {
      if (m) return { text: 'Master image: IN RANGE. Live camera: no reading yet (resume camera or fix errors).', tone: 'warn' as const };
      return { text: 'Master image: OUT OF RANGE vs your threshold.', tone: 'bad' as const };
    }
    if (m && l) return { text: 'Both master reference and live camera are IN RANGE at this threshold.', tone: 'good' as const };
    if (m && !l) return { text: 'Master: IN RANGE — Live camera: OUT OF RANGE (scene differs from reference).', tone: 'warn' as const };
    if (!m && l) return { text: 'Master: OUT OF RANGE — Live: IN RANGE (check master / ROI / lighting).', tone: 'warn' as const };
    return { text: 'Both master and live are OUT OF RANGE at this threshold.', tone: 'bad' as const };
  }, [passMeta.mPass, passMeta.lPass]);

  const showThresholdPanel = useMemo(
    () =>
      (!!selectedToolType && editMode !== 'none') ||
      !!tuningToolId ||
      (!!thresholdInsightToolId && editMode === 'none' && !tuningToolId),
    [selectedToolType, editMode, tuningToolId, thresholdInsightToolId]
  );

  const resetThresholdToSaved = () => {
    const id = tuningToolId ?? thresholdInsightToolId;
    const t = id ? configuredTools.find((x) => x.id === id) : null;
    if (t) setThreshold([t.threshold]);
  };

  const refreshTemplateSummaries = async () => {
    setTemplatesLoading(true);
    try {
      const pid =
        programId != null && !Number.isNaN(Number(programId)) ? Number(programId) : undefined;
      const templates = await api.getToolTemplates(pid);
      setTemplateSummaries(templates);
    } catch (err) {
      toast({
        title: 'Could not load templates',
        description: err instanceof Error ? err.message : 'Unknown error',
        variant: 'destructive',
      });
    } finally {
      setTemplatesLoading(false);
    }
  };

  const openSaveTemplateDialog = () => {
    if (!masterImageData) {
      toast({
        title: 'Layout image required',
        description: 'Capture or load an image in step 1 to draw ROIs, then save a template (only tool settings are stored).',
        variant: 'destructive',
      });
      return;
    }
    if (configuredTools.length === 0) {
      toast({
        title: 'No tools configured',
        description: 'Add at least one tool before saving a template.',
        variant: 'destructive',
      });
      return;
    }
    const ownedName = programName?.trim() || '';
    setTemplateName(ownedName);
    setTemplateDescription(
      programId != null && ownedName
        ? `Tool configuration for program ${ownedName}`
        : ''
    );
    setSaveTemplateOpen(true);
  };

  const handleSaveTemplate = async () => {
    if (!masterImageData || !templateName.trim()) return;
    setTemplateSaving(true);
    try {
      const toolsPayload = snapshotToolsForTemplate(configuredTools);
      const programIdNum =
        programId != null && !Number.isNaN(Number(programId)) ? Number(programId) : null;

      if (programIdNum != null) {
        const ownedName = (programName?.trim() || templateName.trim());
        await api.upsertProgramToolTemplate(programIdNum, {
          tools: toolsPayload,
          description:
            templateDescription.trim() || `Tool configuration for program ${ownedName}`,
          roi_space: ROI_SPACE_WIZARD,
        });
        toast({
          title: 'Program template saved',
          description: `"${ownedName}" — this program's template only. Other programs are not changed.`,
        });
      } else {
        await api.createToolTemplate({
          name: templateName.trim(),
          description: templateDescription.trim() || undefined,
          roi_space: ROI_SPACE_WIZARD,
          tools: toolsPayload,
        });
        toast({
          title: 'Template saved',
          description: onToolTemplateSaved
            ? `"${templateName.trim()}" — only tool configuration was stored. Temporary layout image was cleared.`
            : `"${templateName.trim()}" — only tool configuration was stored.`,
        });
      }
      setSaveTemplateOpen(false);
      await refreshTemplateSummaries();
      onToolTemplateSaved?.();
    } catch (err) {
      toast({
        title: 'Save failed',
        description: err instanceof Error ? err.message : 'Unknown error',
        variant: 'destructive',
      });
    } finally {
      setTemplateSaving(false);
    }
  };

  const openApplyTemplateDialog = async () => {
    if (!masterImageData) {
      toast({
        title: 'Image required on canvas',
        description: 'Register a master or capture/load an image in step 1, then apply a template to that image.',
        variant: 'destructive',
      });
      return;
    }
    setApplyTemplateOpen(true);
    setTemplatePreviewImages({});
    setApplyPreviewTemplate(null);
    await refreshTemplateSummaries();
  };

  const loadTemplatePreview = async (templateId: number) => {
    if (templatePreviewImages[templateId]) return;
    try {
      const template = await api.getToolTemplate(templateId, true);
      if (template.reference_image) {
        setTemplatePreviewImages((prev) => ({
          ...prev,
          [templateId]: template.reference_image!,
        }));
      }
    } catch {
      // Preview is optional
    }
  };

  const loadApplyTemplatePreview = async (templateId: number) => {
    setApplyPreviewLoading(true);
    try {
      const template = await api.getToolTemplate(templateId, false);
      setApplyPreviewTemplate(template);
    } catch {
      setApplyPreviewTemplate(null);
    } finally {
      setApplyPreviewLoading(false);
    }
  };

  const applyTemplateToMaster = async (templateId: number) => {
    if (!masterImageData) return;
    setTemplateApplyingId(templateId);
    try {
      const template = await api.getToolTemplate(templateId, false);
      let toolsForWizard: ToolConfig[];

      const programIdNum =
        programId != null && !Number.isNaN(Number(programId)) ? Number(programId) : null;

      if (programIdNum != null) {
        const scaled = await api.getToolTemplateForProgram(templateId, programIdNum);
        const clonedNative = cloneToolsWithNewIds(scaled.tools);
        toolsForWizard = await applyTemplateToolsToWizardCanvas(
          clonedNative,
          ROI_SPACE_NATIVE,
          masterImageData
        );
      } else {
        const cloned = cloneToolsWithNewIds(template.tools);
        toolsForWizard = await applyTemplateToolsToWizardCanvas(
          cloned,
          template.roi_space ?? ROI_SPACE_WIZARD,
          masterImageData
        );
      }

      handleClearSelection();
      setConfiguredTools(toolsForWizard);
      toast({
        title: 'Template applied',
        description: `"${template.name}" — ${toolsForWizard.length} tool(s) with ROI and threshold on your image.`,
      });
      setApplyTemplateOpen(false);
      setPendingApplyTemplateId(null);
      setApplyPreviewTemplate(null);
    } catch (err) {
      toast({
        title: 'Apply failed',
        description: err instanceof Error ? err.message : 'Unknown error',
        variant: 'destructive',
      });
    } finally {
      setTemplateApplyingId(null);
    }
  };

  const requestApplyTemplate = (templateId: number) => {
    if (configuredTools.length > 0) {
      setPendingApplyTemplateId(templateId);
      return;
    }
    void applyTemplateToMaster(templateId);
  };

  const handleDeleteTemplate = async (templateId: number) => {
    setTemplateDeletingId(templateId);
    try {
      await api.deleteToolTemplate(templateId);
      setTemplateSummaries((prev) => prev.filter((t) => t.id !== templateId));
      setTemplatePreviewImages((prev) => {
        const next = { ...prev };
        delete next[templateId];
        return next;
      });
      toast({ title: 'Template deleted' });
    } catch (err) {
      toast({
        title: 'Delete failed',
        description: err instanceof Error ? err.message : 'Unknown error',
        variant: 'destructive',
      });
    } finally {
      setTemplateDeletingId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold">Step 2: Tool Configuration</h2>
        <p className="text-sm text-muted-foreground mt-2 max-w-4xl">
          In the configuration wizard, this step is where you define inspection regions and limits. Use live or master
          backdrop, watch the real-time judgment while adjusting threshold, and save thresholds from the tool list when you
          only need to change the limit. Save a tool template (tools only — no stored image) or apply one to any master or layout capture.
        </p>
      </div>

      {/* Section 1: Horizontal Tool Selection Bar */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>SELECT TOOL TYPE</CardTitle>
            {selectedToolType && (
              <Badge variant="outline" className="text-sm">
                Selected: {TOOL_TYPES.find(t => t.id === selectedToolType)?.name}
              </Badge>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex gap-3 overflow-x-auto pb-2">
            {TOOL_TYPES.map((tool) => {
              const count = getToolCount(tool.id);
              const disabled = isToolDisabled(tool.id);
              const isSelected = selectedToolType === tool.id;

              return (
                <button
                  key={tool.id}
                  onClick={() => {
                    if (!disabled) {
                      setTuningToolId(null);
                      setRateSnapshot(null);
                      setSelectedToolType(tool.id);
                    }
                  }}
                  disabled={disabled}
                  className={`flex-shrink-0 w-32 p-4 border-2 rounded-lg transition-all duration-200 ${
                    isSelected
                      ? 'border-primary bg-primary/10 ring-2 ring-primary/20 scale-105'
                      : disabled
                      ? 'opacity-50 cursor-not-allowed border-border'
                      : 'border-border hover:border-primary/50 hover:bg-accent/50'
                  }`}
                  style={{
                    borderColor: isSelected ? tool.color : undefined,
                  }}
                >
                  <div className="flex flex-col items-center gap-2 text-center">
                    <div
                      className="w-12 h-12 rounded-lg flex items-center justify-center text-white font-bold text-2xl"
                      style={{ backgroundColor: tool.color }}
                    >
                      {count}
                    </div>
                    <div className="text-xs font-semibold line-clamp-2">
                      {tool.name}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
          
          {selectedToolType ? (
            <div className="mt-4 p-3 bg-green-50 dark:bg-green-950/20 border border-green-200 dark:border-green-800 rounded-lg">
              <p className="text-sm text-green-900 dark:text-green-100">
                ✓ Tool selected. Draw on the canvas (choose Live view to work on the camera, or Master for the reference). The panel below shows master vs live match rates; templates always come from the master strip.
              </p>
            </div>
          ) : (
            <div className="mt-4 p-3 bg-blue-50 dark:bg-blue-950/20 border border-blue-200 dark:border-blue-800 rounded-lg">
              <p className="text-sm text-blue-900 dark:text-blue-100">
                Click any existing region on the canvas to reposition it, or select a tool type to draw a new ROI.
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Section 2: Full-Width Interactive Canvas */}
      <Card>
        <CardHeader>
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="space-y-2 min-w-0 flex-1">
              <CardTitle>
                {editMode === 'editing'
                  ? editingExistingToolId
                    ? 'REPOSITION TOOL — drag handles or body, then Save'
                    : 'EDIT ROI - Drag handles to resize, drag body to move'
                  : 'INTERACTIVE CANVAS'}
              </CardTitle>
              <CardDescription>
                {editMode === 'none' &&
                  (configuredTools.length > 0
                    ? 'Live view opens automatically when the camera is ready. Inside an ROI while you draw or edit, you see the same segmentation the tool uses (e.g. Otsu for Area) overlaid on the video plus Master vs Live thumbnails at the bottom of the box.'
                    : selectedToolType
                      ? 'Click and drag to draw a rectangular ROI (works on master or live backdrop).'
                      : 'Select a tool type above to draw your first ROI')}
                {editMode === 'editing' &&
                  'Inside the ROI: processing preview (mask / edges) is blended on the canvas; thumbnails compare Master template vs Live camera. Use the threshold slider with the match % readouts below.'}
              </CardDescription>
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <span className="text-xs font-medium text-muted-foreground">Main canvas backdrop</span>
                <Button
                  type="button"
                  size="sm"
                  variant={backdrop === 'master' ? 'default' : 'outline'}
                  onClick={() => setBackdrop('master')}
                >
                  <ImageIcon className="h-4 w-4 mr-1" />
                  Master image
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={backdrop === 'live' ? 'default' : 'outline'}
                  onClick={() => setBackdrop('live')}
                  title="Draw and move ROIs on top of the live camera stream (same 640×480 normalization as inspection)."
                >
                  <Camera className="h-4 w-4 mr-1" />
                  Live view
                </Button>
                {wizardFrameMaster && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => setLivePaused((p) => !p)}
                    title="Pause stops camera polling for this step (saves CPU / avoids errors if the camera is disconnected)."
                  >
                    {livePaused ? (
                      <>
                        <Play className="h-4 w-4 mr-1" />
                        Resume camera
                      </>
                    ) : (
                      <>
                        <Pause className="h-4 w-4 mr-1" />
                        Pause camera
                      </>
                    )}
                  </Button>
                )}
                {liveCaptureError && (
                  <span className="text-xs text-destructive max-w-[240px]">{liveCaptureError}</span>
                )}
                {!livePaused && wizardFrameMaster && (
                  <span className="text-xs text-muted-foreground">Live frames update in the background for instant switching and the live match readout.</span>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <Button
                size="sm"
                variant={showLegend ? "default" : "outline"}
                onClick={() => setShowLegend(!showLegend)}
                disabled={configuredTools.length === 0}
              >
                <Layers className="h-4 w-4 mr-1" />
                Legend
              </Button>
              <Button
                size="sm"
                variant={showGrid ? "default" : "outline"}
                onClick={() => setShowGrid(!showGrid)}
              >
                <Grid3x3 className="h-4 w-4 mr-1" />
                Grid
              </Button>
              <Badge variant="outline" className="text-sm">
                {configuredTools.length}/16
              </Badge>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="relative">
            <canvas
              ref={canvasRef}
              width={640}
              height={480}
              onMouseDown={handleCanvasMouseDown}
              onMouseMove={handleCanvasMouseMove}
              onMouseUp={handleCanvasMouseUp}
              onMouseLeave={handleCanvasMouseLeave}
              className={`border rounded w-full aspect-[4/3] ${getCursorStyle()}`}
              style={{ maxWidth: '100%', height: 'auto' }}
            />
          </div>
          
          {editMode === 'editing' && (
            <div className="flex gap-2 mt-4">
              <Button 
                onClick={handleSaveTool} 
                className="flex-1"
                variant="default"
              >
                <Check className="h-4 w-4 mr-2" />
                {editingExistingToolId ? 'Save position' : 'Save Tool'}
              </Button>
              <Button 
                onClick={handleCancelTool} 
                className="flex-1"
                variant="outline"
              >
                <X className="h-4 w-4 mr-2" />
                Cancel
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Section 3: Threshold + real-time match readouts */}
      {showThresholdPanel && panelToolType && (
        <Card className="border-2" style={{ borderColor: TOOL_TYPES.find(t => t.id === panelToolType)?.color }}>
          <CardContent className="pt-6">
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3 min-w-0">
                  <div
                    className="w-8 h-8 rounded flex-shrink-0"
                    style={{ backgroundColor: TOOL_TYPES.find(t => t.id === panelToolType)?.color }}
                  />
                  <div className="min-w-0">
                    <Label className="text-base font-semibold">
                      {activeContextTool?.name
                        ? `${activeContextTool.name} — ${TOOL_TYPES.find((t) => t.id === panelToolType)?.name ?? panelToolType}`
                        : TOOL_TYPES.find((t) => t.id === panelToolType)?.name}
                    </Label>
                    <p className="text-xs text-muted-foreground">
                      {tuningToolId && editMode === 'none'
                        ? 'Threshold-only mode — saved ROI; move the slider for instant pass/fail vs the master.'
                        : thresholdInsightToolId && editMode === 'none' && !tuningToolId
                          ? 'Saved ROI on the canvas — master and live match % update as the scene changes; the slider is temporary until you save to the tool.'
                          : 'Active tool — match % refreshes from the scene; pass/fail vs the slider updates immediately.'}
                    </p>
                  </div>
                </div>
                <div className="text-right flex-shrink-0">
                  <div className="text-2xl font-bold text-primary tabular-nums">{threshold[0]}%</div>
                  <p className="text-xs text-muted-foreground">Threshold limit</p>
                </div>
              </div>

              <div
                role="status"
                className={`rounded-md border px-3 py-2 text-sm ${
                  combinedVerdict.tone === 'good'
                    ? 'border-green-600/40 bg-green-50 dark:bg-green-950/30'
                    : combinedVerdict.tone === 'warn'
                      ? 'border-amber-600/40 bg-amber-50 dark:bg-amber-950/30'
                      : combinedVerdict.tone === 'bad'
                        ? 'border-red-600/40 bg-red-50 dark:bg-red-950/30'
                        : 'border-border bg-muted/40'
                }`}
              >
                {combinedVerdict.text}
              </div>

              {editMode === 'none' &&
                !tuningToolId &&
                configuredTools.filter((t) => t.type !== 'position_adjust').length > 0 && (
                  <div className="space-y-1">
                    <Label htmlFor="threshold-insight-tool" className="text-xs font-medium">
                      Tool for master / live threshold check
                    </Label>
                    <select
                      id="threshold-insight-tool"
                      className="flex h-9 w-full max-w-md rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm"
                      value={thresholdInsightToolId ?? ''}
                      onChange={(e) => {
                        const v = e.target.value;
                        setThresholdInsightToolId(v || null);
                      }}
                    >
                      {configuredTools
                        .filter((t) => t.type !== 'position_adjust')
                        .map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.name} (saved {t.threshold}%)
                          </option>
                        ))}
                    </select>
                  </div>
                )}

              {panelToolType !== 'position_adjust' && (
                <div className="rounded-lg border-2 border-foreground/10 bg-gradient-to-br from-slate-50 to-slate-100 p-4 dark:from-slate-900 dark:to-slate-950 space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm font-bold uppercase tracking-wide text-muted-foreground">
                      Real-time judgment
                    </span>
                    {previewBusy && passMeta.mRate == null && (
                      <span className="text-xs text-muted-foreground animate-pulse">Sampling…</span>
                    )}
                    {previewBusy && passMeta.mRate != null && (
                      <span className="text-xs text-muted-foreground">Updating scene…</span>
                    )}
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div
                      className={`rounded-xl border-2 p-4 transition-colors duration-75 ${
                        passMeta.mPass === true
                          ? 'border-green-500 bg-green-50 dark:bg-green-950/40'
                          : passMeta.mPass === false
                            ? 'border-red-500 bg-red-50 dark:bg-red-950/40'
                            : 'border-muted bg-muted/30'
                      }`}
                    >
                      <div className="text-xs font-semibold uppercase text-muted-foreground">Master (reference)</div>
                      {passMeta.mRate != null ? (
                        <>
                          {passMeta.mEdgeFill != null && (
                            <div className="mt-2 space-y-0.5">
                              <div className="text-4xl font-black tabular-nums leading-tight text-foreground">
                                {passMeta.mEdgeFill.toFixed(2)}
                                <span className="text-lg font-bold text-muted-foreground">%</span>
                              </div>
                              <p className="text-xs font-medium text-muted-foreground">
                                Edge fill (ROI){' '}
                                <span className="text-foreground/80">— same scale as live for lighting / focus</span>
                              </p>
                            </div>
                          )}
                          <div
                            className={
                              passMeta.mEdgeFill != null ? 'mt-3 pt-3 border-t border-foreground/10 space-y-1' : 'space-y-1'
                            }
                          >
                            <div
                              className={
                                passMeta.mEdgeFill != null
                                  ? 'text-2xl font-bold tabular-nums leading-tight'
                                  : 'text-4xl font-black tabular-nums leading-tight'
                              }
                            >
                              {passMeta.mRate.toFixed(1)}
                              <span
                                className={
                                  passMeta.mEdgeFill != null
                                    ? 'text-base font-bold text-muted-foreground'
                                    : 'text-lg font-bold text-muted-foreground'
                                }
                              >
                                %
                              </span>
                            </div>
                            <p className="text-xs text-muted-foreground">
                              Match vs saved template
                              {passMeta.mEdgeFill != null && (
                                <>
                                  {' '}
                                  <span className="italic">
                                    (reads ~100% on the master stillframe by definition; the slider uses this row)
                                  </span>
                                </>
                              )}
                            </p>
                          </div>
                          <div className="text-lg font-bold mt-1">
                            {passMeta.mPass ? 'PASS' : 'FAIL'}
                            <span className="text-sm font-normal text-muted-foreground"> · limit {threshold[0]}%</span>
                          </div>
                          <div className="mt-1">
                            {passMeta.mPass === true && (
                              <span className="text-xs font-bold uppercase tracking-wide text-green-700 dark:text-green-300">
                                In accepted range (master)
                              </span>
                            )}
                            {passMeta.mPass === false && (
                              <span className="text-xs font-bold uppercase tracking-wide text-red-700 dark:text-red-300">
                                Out of range (master)
                              </span>
                            )}
                          </div>
                          {passMeta.upper != null ? (
                            <p className="text-xs text-muted-foreground mt-1">
                              Allowed band: {threshold[0]}% – {passMeta.upper}% match
                            </p>
                          ) : (
                            <p className="text-xs text-muted-foreground mt-1">
                              Margin vs limit:{' '}
                              <span className="font-mono font-semibold text-foreground">
                                {passMeta.mMargin != null && passMeta.mMargin >= 0 ? '+' : ''}
                                {passMeta.mMargin?.toFixed(1) ?? '—'} pts
                              </span>{' '}
                              (positive = safer)
                            </p>
                          )}
                          <ThresholdMeter rate={passMeta.mRate} threshold={threshold[0]} upper={passMeta.upper} />
                        </>
                      ) : (
                        <p className="text-sm text-muted-foreground py-6">Waiting for first reading…</p>
                      )}
                    </div>
                    <div
                      className={`rounded-xl border-2 p-4 transition-colors duration-75 ${
                        passMeta.lPass === true
                          ? 'border-green-500 bg-green-50 dark:bg-green-950/40'
                          : passMeta.lPass === false
                            ? 'border-red-500 bg-red-50 dark:bg-red-950/40'
                            : 'border-muted bg-muted/30'
                      }`}
                    >
                      <div className="text-xs font-semibold uppercase text-muted-foreground">Live camera</div>
                      {passMeta.lRate != null ? (
                        <>
                          {passMeta.lEdgeFill != null && (
                            <div className="mt-2 space-y-0.5">
                              <div className="text-4xl font-black tabular-nums leading-tight text-foreground">
                                {passMeta.lEdgeFill.toFixed(2)}
                                <span className="text-lg font-bold text-muted-foreground">%</span>
                              </div>
                              <p className="text-xs font-medium text-muted-foreground">
                                Edge fill (ROI){' '}
                                <span className="text-foreground/80">— compare to master edge fill above</span>
                              </p>
                            </div>
                          )}
                          <div
                            className={
                              passMeta.lEdgeFill != null ? 'mt-3 pt-3 border-t border-foreground/10 space-y-1' : 'space-y-1'
                            }
                          >
                            <div
                              className={
                                passMeta.lEdgeFill != null
                                  ? 'text-2xl font-bold tabular-nums leading-tight'
                                  : 'text-3xl font-black tabular-nums leading-tight'
                              }
                            >
                              {passMeta.lRate.toFixed(1)}
                              <span
                                className={
                                  passMeta.lEdgeFill != null
                                    ? 'text-sm font-bold text-muted-foreground'
                                    : 'text-base font-bold text-muted-foreground'
                                }
                              >
                                %
                              </span>
                            </div>
                            <p className="text-xs text-muted-foreground">Match vs saved template (threshold uses this)</p>
                          </div>
                          <div className="text-lg font-bold mt-1">
                            {passMeta.lPass ? 'PASS' : 'FAIL'}
                            <span className="text-sm font-normal text-muted-foreground"> · same limit</span>
                          </div>
                          <div className="mt-1">
                            {passMeta.lPass === true && (
                              <span className="text-xs font-bold uppercase tracking-wide text-green-700 dark:text-green-300">
                                In accepted range (live)
                              </span>
                            )}
                            {passMeta.lPass === false && (
                              <span className="text-xs font-bold uppercase tracking-wide text-red-700 dark:text-red-300">
                                Out of range (live)
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground mt-1">
                            Margin:{' '}
                            <span className="font-mono font-semibold text-foreground">
                              {passMeta.lMargin != null && passMeta.lMargin >= 0 ? '+' : ''}
                              {passMeta.lMargin?.toFixed(1) ?? '—'} pts
                            </span>
                          </p>
                          <ThresholdMeter rate={passMeta.lRate} threshold={threshold[0]} upper={passMeta.upper} />
                        </>
                      ) : (
                        <p className="text-sm text-muted-foreground py-6">
                          {livePaused
                            ? 'Resume the camera for live readings.'
                            : 'Live % appears when the camera stream is available.'}
                        </p>
                      )}
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Pass/fail flips instantly with the slider because it only compares your limit to the latest{' '}
                    <span className="font-medium text-foreground">match vs template</span> (not edge fill). Edge fill is
                    shown for edge tools so master and live use the same physical scale. Percentages refresh quickly when
                    the scene or ROI changes (~32ms debounce).
                  </p>
                </div>
              )}

              <div className="space-y-2">
                <Slider
                  value={threshold}
                  onValueChange={setThreshold}
                  min={0}
                  max={100}
                  step={1}
                  className="w-full"
                />
                <div className="flex justify-between text-xs text-muted-foreground">
                  <span>Strict (0)</span>
                  <span>Balanced (50)</span>
                  <span>Lenient (100)</span>
                </div>
                {panelToolType !== 'position_adjust' && (
                  <p className="text-xs text-muted-foreground pt-1">
                    On the canvas, the ROI mask shows what the tool measures. Use the cards above while dragging this slider to
                    see immediately if you are above or below your limit.
                  </p>
                )}
              </div>

              {panelToolType !== 'position_adjust' && (
                <div className="space-y-3">
                  <div className="rounded-md border bg-muted/50 p-3 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium">Master detail</span>
                      {previewBusy && passMeta.mRate == null && (
                        <span className="text-xs text-muted-foreground">Computing…</span>
                      )}
                    </div>
                    {passMeta.mRate != null ? (
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="text-xl font-bold tabular-nums">
                          {passMeta.mRate.toFixed(1)}
                          <span className="text-sm font-semibold text-muted-foreground">%</span>
                        </div>
                        <Badge variant={passMeta.mPass ? 'default' : passMeta.mPass === false ? 'destructive' : 'secondary'}>
                          {passMeta.mPass === true ? 'OK' : passMeta.mPass === false ? 'NG' : '—'} · limit {threshold[0]}%
                        </Badge>
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">Draw or select an ROI to load master match.</p>
                    )}
                  </div>

                  <div className="rounded-md border bg-muted/30 p-3 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-muted-foreground">Live detail</span>
                      {!wizardFrameLive && (
                        <span className="text-xs text-muted-foreground">Waiting for frames…</span>
                      )}
                    </div>
                    {passMeta.lRate != null ? (
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="text-xl font-bold tabular-nums">
                          {passMeta.lRate.toFixed(1)}
                          <span className="text-sm font-semibold text-muted-foreground">%</span>
                        </div>
                        <Badge variant={passMeta.lPass ? 'default' : passMeta.lPass === false ? 'destructive' : 'secondary'}>
                          {passMeta.lPass === true ? 'OK' : passMeta.lPass === false ? 'NG' : '—'} (live)
                        </Badge>
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">
                        {livePaused
                          ? 'Resume the camera to stream live frames.'
                          : 'Live match fills when the camera is running.'}
                      </p>
                    )}
                  </div>
                </div>
              )}

              {panelToolType === 'position_adjust' && (
                <p className="text-xs text-muted-foreground">
                  Matching-rate preview is for detection tools. Use the master backdrop to align the template ROI.
                </p>
              )}

              <div className="flex flex-wrap gap-2">
                {(tuningToolId || (thresholdInsightToolId && editMode === 'none')) &&
                  panelToolType !== 'position_adjust' && (
                    <>
                      <Button size="sm" className="flex-1 min-w-[140px]" onClick={applyThresholdTuning}>
                        Save threshold to tool
                      </Button>
                      <Button size="sm" variant="secondary" className="flex-1 min-w-[120px]" onClick={resetThresholdToSaved}>
                        Reset to saved
                      </Button>
                    </>
                  )}
                <Button size="sm" variant="outline" onClick={handleClearSelection} className="flex-1 min-w-[120px]">
                  Clear selection
                </Button>
                {editMode !== 'none' && !tuningToolId && (
                  <Button size="sm" variant="outline" onClick={handleCancelTool} className="flex-1 min-w-[120px]">
                    Cancel ROI edit
                  </Button>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Section 4: Collapsible Configured Tools List */}
      <Card>
        <CardHeader className="cursor-pointer" onClick={() => setToolListExpanded(!toolListExpanded)}>
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <CardTitle>
              CONFIGURED TOOLS ({configuredTools.length}/16)
            </CardTitle>
            <div
              className="flex items-center gap-2"
              onClick={(e) => e.stopPropagation()}
            >
              <Button
                size="sm"
                variant="outline"
                onClick={openApplyTemplateDialog}
                disabled={editMode !== 'none' || !!tuningToolId}
              >
                <LayoutTemplate className="h-4 w-4 mr-1" />
                Apply template
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={openSaveTemplateDialog}
                disabled={editMode !== 'none' || !!tuningToolId || configuredTools.length === 0}
              >
                <Save className="h-4 w-4 mr-1" />
                Save as template
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setToolListExpanded(!toolListExpanded)}>
                {toolListExpanded ? (
                  <>
                    <ChevronUp className="h-4 w-4 mr-1" />
                    Collapse
                  </>
                ) : (
                  <>
                    <ChevronDown className="h-4 w-4 mr-1" />
                    Expand
                  </>
                )}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {configuredTools.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-4">
              No tools configured yet. Select a tool type and draw on the canvas.
            </p>
          ) : toolListExpanded ? (
            // Expanded View: Grid Layout
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {configuredTools.map((tool, index) => (
                <div
                  key={tool.id}
                  className="p-4 border-2 rounded-lg hover:border-primary/50 hover:shadow-lg transition-all"
                  style={{ borderColor: tool.color }}
                >
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <div
                        className="w-6 h-6 rounded flex-shrink-0"
                        style={{ backgroundColor: tool.color }}
                      />
                      <span className="font-semibold text-sm">
                        {index + 1}. {tool.name}
                      </span>
                    </div>
                    <div className="flex gap-1">
                      {tool.type !== 'position_adjust' && (
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => startThresholdTuning(tool)}
                          className="h-6 px-2"
                          disabled={editMode !== 'none' || !!tuningToolId}
                          title="Tune threshold with instant pass/fail (saved ROI)"
                        >
                          <SlidersHorizontal className="h-3 w-3" />
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => beginRepositionTool(tool)}
                        className="h-6 px-2"
                        disabled={editMode !== 'none'}
                        title="Move or resize on canvas"
                      >
                        <Move className="h-3 w-3" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleDeleteTool(tool.id)}
                        className="h-6 w-6 p-0 hover:bg-destructive hover:text-destructive-foreground"
                        disabled={editMode !== 'none'}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  </div>
                  <div className="space-y-1 text-xs text-muted-foreground">
                    <div className="flex justify-between">
                      <span>Position:</span>
                      <span className="font-mono">X:{tool.roi.x} Y:{tool.roi.y}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Size:</span>
                      <span className="font-mono">{tool.roi.width}×{tool.roi.height}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Threshold:</span>
                      <Badge variant="secondary" className="text-xs">{tool.threshold}%</Badge>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            // Collapsed View: Horizontal Chips
            <div className="flex flex-wrap gap-2">
              {configuredTools.map((tool, index) => (
                <div
                  key={tool.id}
                  className="flex items-center gap-2 px-3 py-2 bg-accent/50 border rounded-full hover:bg-accent hover:shadow-md transition-all"
                >
                  <div
                    className="w-3 h-3 rounded-full flex-shrink-0"
                    style={{ backgroundColor: tool.color }}
                  />
                  <span className="text-sm font-medium">
                    {index + 1}. {tool.name}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {tool.roi.width}×{tool.roi.height}
                  </span>
                  {tool.type !== 'position_adjust' && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => startThresholdTuning(tool)}
                      className="h-5 w-5 p-0 rounded-full ml-0.5"
                      disabled={editMode !== 'none' || !!tuningToolId}
                      title="Tune threshold"
                    >
                      <SlidersHorizontal className="h-3 w-3" />
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => beginRepositionTool(tool)}
                    className="h-5 w-5 p-0 rounded-full ml-0.5"
                    disabled={editMode !== 'none'}
                    title="Reposition"
                  >
                    <Move className="h-3 w-3" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => handleDeleteTool(tool.id)}
                    className="h-5 w-5 p-0 rounded-full ml-1"
                    disabled={editMode !== 'none'}
                  >
                    <X className="h-3 w-3" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={saveTemplateOpen} onOpenChange={setSaveTemplateOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {programId != null ? 'Save program tool template' : 'Save tool configuration template'}
            </DialogTitle>
            <DialogDescription>
              {programId != null ? (
                <>
                  Saves to <strong>{programName?.trim() || 'this program'}&apos;s</strong> private template only
                  (same name as the program). Configuring another program (e.g. 1222) will not change this template.
                </>
              ) : (
                <>
                  Each configured tool is stored with its <strong>ROI</strong> and <strong>threshold</strong>. No
                  camera image is kept. Shared templates can be applied to any master.
                </>
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="template-name">Template name</Label>
              <Input
                id="template-name"
                value={templateName}
                onChange={(e) => setTemplateName(e.target.value)}
                placeholder="e.g. PCB top — 4 tools"
                maxLength={80}
                readOnly={programId != null}
                className={programId != null ? 'bg-muted' : undefined}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="template-description">Description (optional)</Label>
              <Input
                id="template-description"
                value={templateDescription}
                onChange={(e) => setTemplateDescription(e.target.value)}
                placeholder="Notes about lighting, part variant, etc."
                maxLength={200}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Saving {configuredTools.length} tool(s): ROI rectangles and threshold for each, in list order.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSaveTemplateOpen(false)} disabled={templateSaving}>
              Cancel
            </Button>
            <Button
              onClick={() => void handleSaveTemplate()}
              disabled={templateSaving || !templateName.trim()}
            >
              {templateSaving ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Saving…
                </>
              ) : (
                'Save template'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={applyTemplateOpen} onOpenChange={setApplyTemplateOpen}>
        <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Apply tool configuration template</DialogTitle>
            <DialogDescription>
              Load saved ROI rectangles and thresholds onto your current image template. Each tool keeps
              its threshold (and optional upper limit). ROIs are mapped to this master on the wizard canvas;
              at run time they scale to full master resolution for inspection.
            </DialogDescription>
          </DialogHeader>
          {templatesLoading ? (
            <div className="flex items-center justify-center py-10 text-muted-foreground gap-2">
              <Loader2 className="h-5 w-5 animate-spin" />
              Loading templates…
            </div>
          ) : templateSummaries.length === 0 ? (
            <p className="text-sm text-muted-foreground py-8 text-center">
              No saved templates yet. Configure tools on a master image and use &quot;Save as template&quot;.
            </p>
          ) : (
            <div className="space-y-3 py-2">
              {templateSummaries.map((template) => (
                <div
                  key={template.id}
                  className="flex flex-col sm:flex-row gap-3 p-3 border rounded-lg hover:border-primary/40 transition-colors"
                  onMouseEnter={() => {
                    void loadTemplatePreview(template.id);
                    void loadApplyTemplatePreview(template.id);
                  }}
                >
                  <div className="w-full sm:w-28 h-20 rounded-md border bg-muted/30 overflow-hidden flex-shrink-0 flex items-center justify-center">
                    {templatePreviewImages[template.id] ? (
                      <img
                        src={`data:image/png;base64,${templatePreviewImages[template.id]}`}
                        alt={`${template.name} reference`}
                        className="w-full h-full object-cover"
                      />
                    ) : (
                      <ImageIcon className="h-6 w-6 text-muted-foreground" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="font-semibold truncate">
                          {template.name}
                          {template.owned_by_program ? (
                            <Badge variant="secondary" className="ml-2 text-[10px] py-0">
                              Program
                            </Badge>
                          ) : null}
                        </p>
                        {template.description ? (
                          <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                            {template.description}
                          </p>
                        ) : null}
                        <p className="text-xs text-muted-foreground mt-1">
                          {template.tool_count} tool(s) · updated{' '}
                          {new Date(template.updated_at).toLocaleDateString()}
                        </p>
                        {applyPreviewTemplate?.id === template.id && applyPreviewTemplate.tools.length > 0 ? (
                          <ul className="mt-2 space-y-0.5 text-xs font-mono text-muted-foreground max-h-24 overflow-y-auto">
                            {applyPreviewTemplate.tools.map((t) => (
                              <li key={t.id} className="truncate">
                                {t.name}: ROI ({Math.round(t.roi.x)},{Math.round(t.roi.y)}{' '}
                                {Math.round(t.roi.width)}×{Math.round(t.roi.height)}) · thr{' '}
                                {Math.round(t.threshold)}
                                {t.upperLimit != null ? ` · cap ${Math.round(t.upperLimit)}` : ''}
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </div>
                      <div className="flex gap-1 flex-shrink-0">
                        <Button
                          size="sm"
                          onClick={() => requestApplyTemplate(template.id)}
                          disabled={templateApplyingId !== null || templateDeletingId !== null}
                        >
                          {templateApplyingId === template.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            'Apply'
                          )}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => void handleDeleteTemplate(template.id)}
                          disabled={templateDeletingId === template.id || templateApplyingId !== null}
                          className="text-destructive hover:text-destructive"
                        >
                          {templateDeletingId === template.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Trash2 className="h-4 w-4" />
                          )}
                        </Button>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={pendingApplyTemplateId !== null}
        onOpenChange={(open) => {
          if (!open) setPendingApplyTemplateId(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Replace current tools?</AlertDialogTitle>
            <AlertDialogDescription>
              Applying this template will replace your {configuredTools.length} configured tool(s) with
              the template layout. This cannot be undone without re-applying or redrawing.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (pendingApplyTemplateId !== null) {
                  void applyTemplateToMaster(pendingApplyTemplateId);
                }
              }}
            >
              Replace and apply
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
