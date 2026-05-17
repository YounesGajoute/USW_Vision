import { describe, expect, it } from 'vitest';
import {
  isRestFallbackEligible,
  mapWsInspectionToResult,
  overallConfidenceFromToolResults,
} from '@/lib/inspection-transport';
import type { InspectionResultEvent, ToolResult } from '@/types';

describe('mapWsInspectionToResult', () => {
  it('maps WS fields to run page shape', () => {
    const data: InspectionResultEvent = {
      programId: 7,
      status: 'OK',
      toolResults: [
        {
          name: 'T1',
          tool_type: 'pattern_match',
          status: 'OK',
          matching_rate: 80,
          threshold: 70,
        },
      ] as ToolResult[],
      processingTime: 42,
      image: 'abc123',
      format: 'png',
      timestamp: 1000,
    };
    const r = mapWsInspectionToResult(data, '7');
    expect(r.programId).toBe('7');
    expect(r.status).toBe('OK');
    expect(r.processingTime).toBe(42);
    expect(r.image).toContain('data:image');
    expect(r.overallConfidence).toBe(80);
  });
});

describe('overallConfidenceFromToolResults', () => {
  it('excludes position_adjust from average', () => {
    const tools: ToolResult[] = [
      {
        name: 'pos',
        tool_type: 'position_adjust',
        status: 'OK',
        matching_rate: 0,
        threshold: 0,
      },
      {
        name: 'd',
        tool_type: 'pattern_match',
        status: 'OK',
        matching_rate: 60,
        threshold: 50,
      },
      {
        name: 'd2',
        tool_type: 'pattern_match',
        status: 'OK',
        matching_rate: 80,
        threshold: 50,
      },
    ];
    expect(overallConfidenceFromToolResults(tools)).toBe(70);
  });
});

describe('isRestFallbackEligible', () => {
  it('detects 503 errors', () => {
    expect(isRestFallbackEligible(new Error('HTTP 503: Service Unavailable'))).toBe(true);
  });

  it('rejects generic errors', () => {
    expect(isRestFallbackEligible(new Error('not found'))).toBe(false);
  });
});
