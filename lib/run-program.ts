import type {
  CaptureOptions,
  OutputAssignment,
  ProgramConfig,
  ToolConfig,
  ToolResult,
} from '@/types';
import { DEFAULT_CAMERA_CAPTURE } from '@/lib/camera-defaults';
import type { Program } from '@/lib/storage';

const DEFAULT_OUTPUTS: OutputAssignment = {
  OUT1: 'Always ON',
  OUT2: 'OK',
  OUT3: 'NG',
  OUT4: 'Not Used',
  OUT5: 'Not Used',
  OUT6: 'Not Used',
  OUT7: 'Not Used',
  OUT8: 'Not Used',
};

function isBrightnessMode(v: unknown): v is ProgramConfig['brightnessMode'] {
  return v === 'normal' || v === 'hdr' || v === 'highgain';
}

function isOutputCondition(v: unknown): v is OutputAssignment[keyof OutputAssignment] {
  return (
    v === 'OK' ||
    v === 'NG' ||
    v === 'Always ON' ||
    v === 'Always OFF' ||
    v === 'Not Used'
  );
}

function normalizeOutputs(raw: unknown): OutputAssignment {
  const o = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  const pick = (key: keyof OutputAssignment): OutputAssignment[keyof OutputAssignment] =>
    isOutputCondition(o[key]) ? o[key] : DEFAULT_OUTPUTS[key];

  return {
    OUT1: pick('OUT1'),
    OUT2: pick('OUT2'),
    OUT3: pick('OUT3'),
    OUT4: pick('OUT4'),
    OUT5: pick('OUT5'),
    OUT6: pick('OUT6'),
    OUT7: pick('OUT7'),
    OUT8: pick('OUT8'),
  };
}

/** Coerce API / localStorage program config into {@link ProgramConfig}. */
export function normalizeProgramConfig(raw: unknown): ProgramConfig {
  const c = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};

  return {
    triggerType: c.triggerType === 'external' ? 'external' : 'internal',
    triggerInterval: Number(c.triggerInterval ?? 1000),
    triggerDelay: Number(c.triggerDelay ?? 0),
    brightnessMode: isBrightnessMode(c.brightnessMode)
      ? c.brightnessMode
      : DEFAULT_CAMERA_CAPTURE.brightnessMode,
    focusValue: Number(c.focusValue ?? DEFAULT_CAMERA_CAPTURE.focusValue),
    exposureTimeUs: Number(c.exposureTimeUs ?? DEFAULT_CAMERA_CAPTURE.exposureTimeUs),
    analogGain: Number(c.analogGain ?? DEFAULT_CAMERA_CAPTURE.analogGain),
    digitalGain: Number(c.digitalGain ?? DEFAULT_CAMERA_CAPTURE.digitalGain),
    masterImage: typeof c.masterImage === 'string' ? c.masterImage : null,
    tools: Array.isArray(c.tools) ? (c.tools as ToolConfig[]) : [],
    outputs: normalizeOutputs(c.outputs),
  };
}

/** Normalize a program row from the API or localStorage for the run page. */
export function normalizeRunProgram(raw: unknown): Program | null {
  if (!raw || typeof raw !== 'object') return null;
  const p = raw as Record<string, unknown>;
  if (p.id == null || !p.name) return null;

  return {
    id: String(p.id),
    name: String(p.name),
    created: String(p.created ?? p.created_at ?? ''),
    lastRun: (p.lastRun ?? p.last_run ?? null) as string | null,
    totalInspections: Number(p.totalInspections ?? p.total_inspections ?? 0),
    okCount: Number(p.okCount ?? p.ok_count ?? 0),
    ngCount: Number(p.ngCount ?? p.ng_count ?? 0),
    config: normalizeProgramConfig(p.config),
  };
}

export type RunMode = 'program' | 'template';

/** Tools used for overlays and browser fallback on the run page. */
export function getToolsForRun(
  runMode: RunMode,
  program: Program,
  templateTools: ToolConfig[] | null | undefined
): ToolConfig[] {
  if (runMode === 'template' && templateTools && templateTools.length > 0) {
    return templateTools;
  }
  return (program.config.tools as ToolConfig[]) ?? [];
}

/** Match a tool result row to a configured tool (name first, then type). */
export function findToolForResult(
  toolResults: ToolResult[],
  tool: ToolConfig
): ToolResult | undefined {
  return (
    toolResults.find((tr) => tr.name === tool.name) ??
    toolResults.find((tr) => tr.tool_type === tool.type)
  );
}

export function captureOptionsFromConfig(cfg: ProgramConfig): CaptureOptions {
  return {
    brightnessMode: cfg.brightnessMode,
    focusValue: cfg.focusValue,
    exposureTime: cfg.exposureTimeUs,
    analogGain: cfg.analogGain,
    digitalGain: cfg.digitalGain,
  };
}
