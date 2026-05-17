import { rawBase64ToImageDataUrl } from '@/lib/inspection-engine';
import type { InspectionResultEvent, ToolResult } from '@/types';

export type InspectionTransport = 'rest' | 'ws' | 'rest-with-ws-fallback';

export function getInspectionTransport(): InspectionTransport {
  const v = process.env.NEXT_PUBLIC_INSPECTION_TRANSPORT?.trim().toLowerCase();
  if (v === 'ws') return 'ws';
  if (v === 'rest-with-ws-fallback') return 'rest-with-ws-fallback';
  return 'rest';
}

export interface RunPageInspectionResult {
  id: string;
  timestamp: Date;
  programId: string | number;
  status: 'OK' | 'NG';
  overallConfidence: number;
  processingTime: number;
  toolResults: ToolResult[];
  image: string;
  positionOffset?: { x: number; y: number };
  resultId?: number | null;
}

export function overallConfidenceFromToolResults(toolResults: ToolResult[]): number {
  const detectRates = toolResults
    .filter((t) => t.tool_type !== 'position_adjust')
    .map((t) => t.matching_rate);
  if (detectRates.length > 0) {
    return detectRates.reduce((a, b) => a + b, 0) / detectRates.length;
  }
  return (
    toolResults.reduce((a, t) => a + (t.matching_rate || 0), 0) /
    Math.max(1, toolResults.length)
  );
}

/** Map Socket.IO inspection_result payload to run-page result shape. */
export function mapWsInspectionToResult(
  data: InspectionResultEvent,
  programId: string
): RunPageInspectionResult {
  const toolResults = (data.toolResults ?? []) as ToolResult[];
  const pos = toolResults.find((t) => t.tool_type === 'position_adjust');
  const processingTime =
    data.processingTime ??
    (data as { processingTimeMs?: number }).processingTimeMs ??
    0;

  let image = '';
  if (data.image) {
    image = data.image.startsWith('data:')
      ? data.image
      : rawBase64ToImageDataUrl(data.image);
  }

  return {
    id: String((data as { resultId?: number }).resultId ?? Date.now()),
    timestamp: new Date((data.timestamp ?? Date.now() / 1000) * 1000),
    programId,
    status: data.status,
    overallConfidence: overallConfidenceFromToolResults(toolResults),
    processingTime,
    toolResults,
    image,
    positionOffset: pos?.offset
      ? { x: pos.offset.dx, y: pos.offset.dy }
      : undefined,
    resultId: (data as { resultId?: number }).resultId ?? undefined,
  };
}

/** True when REST failed in a way that should trigger WS fallback. */
export function isRestFallbackEligible(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return (
    msg.includes('503') ||
    msg.includes('502') ||
    msg.includes('504') ||
    msg.toLowerCase().includes('timeout') ||
    msg.toLowerCase().includes('unavailable')
  );
}
