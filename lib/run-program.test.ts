import { describe, expect, it } from 'vitest';
import { findToolForResult, getToolsForRun } from '@/lib/run-program';
import type { Program } from '@/lib/storage';
import type { ToolConfig, ToolResult } from '@/types';

const program: Program = {
  id: '1',
  name: 'P1',
  created: '',
  lastRun: null,
  totalInspections: 0,
  okCount: 0,
  ngCount: 0,
  config: {
    triggerType: 'internal',
    triggerInterval: 1000,
    triggerDelay: 0,
    brightnessMode: 'normal',
    focusValue: 50,
    exposureTimeUs: 5000,
    analogGain: 1,
    digitalGain: 1,
    masterImage: null,
    tools: [
      {
        id: 'a',
        type: 'pattern_match',
        name: 'ProgTool',
        color: '#f00',
        roi: { x: 0, y: 0, width: 10, height: 10 },
        threshold: 80,
        upperLimit: 100,
        parameters: {},
      },
    ],
    outputs: {
      OUT1: 'Always ON',
      OUT2: 'OK',
      OUT3: 'NG',
      OUT4: 'Not Used',
      OUT5: 'Not Used',
      OUT6: 'Not Used',
      OUT7: 'Not Used',
      OUT8: 'Not Used',
    },
  },
};

const templateTools: ToolConfig[] = [
  {
    id: 'b',
    type: 'pattern_match',
    name: 'TplTool',
    color: '#0f0',
    roi: { x: 1, y: 1, width: 20, height: 20 },
    threshold: 70,
    upperLimit: 100,
    parameters: {},
  },
];

describe('getToolsForRun', () => {
  it('returns program tools in program mode', () => {
    expect(getToolsForRun('program', program, templateTools)).toEqual(program.config.tools);
  });

  it('returns template tools in template mode when set', () => {
    expect(getToolsForRun('template', program, templateTools)).toEqual(templateTools);
  });

  it('falls back to program tools when template list empty', () => {
    expect(getToolsForRun('template', program, [])).toEqual(program.config.tools);
  });
});

describe('findToolForResult', () => {
  const results: ToolResult[] = [
    {
      name: 'TplTool',
      tool_type: 'pattern_match',
      status: 'OK',
      matching_rate: 90,
      threshold: 70,
    },
  ];

  it('matches by name', () => {
    expect(findToolForResult(results, templateTools[0])?.name).toBe('TplTool');
  });

  it('matches by type when name differs', () => {
    const tool = { ...templateTools[0], name: 'Other' };
    expect(findToolForResult(results, tool)?.tool_type).toBe('pattern_match');
  });
});
