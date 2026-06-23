'use client';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  displayScore,
  judgmentMargin,
  judgmentPass,
  judgmentTone,
  suggestThreshold,
  type JudgmentChannel,
  type JudgmentTone,
  type ToolJudgmentSnapshot,
} from '@/lib/toolJudgment';

function JudgmentMeter({
  score,
  threshold,
  upper,
}: {
  score: number;
  threshold: number;
  upper?: number;
}) {
  return (
    <div className="relative mt-2 h-2.5 w-full overflow-hidden rounded-full bg-black/15 dark:bg-white/10">
      <div
        className="absolute h-full rounded-l-full bg-primary/85 transition-[width] duration-75 ease-out"
        style={{ width: `${Math.min(100, Math.max(0, score))}%` }}
      />
      <div
        className="absolute top-0 h-full w-0.5 bg-foreground shadow-sm"
        style={{ left: `calc(${threshold}% - 1px)` }}
        title={`Threshold ${threshold}%`}
      />
      {upper != null && (
        <div
          className="absolute top-0 h-full w-0.5 bg-orange-500"
          style={{ left: `calc(${upper}% - 1px)` }}
          title={`Upper ${upper}%`}
        />
      )}
    </div>
  );
}

function channelBorder(tone: JudgmentTone): string {
  switch (tone) {
    case 'pass':
      return 'border-green-500/80 bg-green-50/80 dark:bg-green-950/35';
    case 'fail':
      return 'border-red-500/80 bg-red-50/80 dark:bg-red-950/35';
    case 'warn':
      return 'border-amber-500/70 bg-amber-50/70 dark:bg-amber-950/30';
    default:
      return 'border-muted bg-muted/25';
  }
}

function ChannelCard({
  title,
  channel,
  threshold,
  upper,
  waitingText,
}: {
  title: string;
  channel: JudgmentChannel | null;
  threshold: number;
  upper?: number;
  waitingText?: string;
}) {
  const score = displayScore(channel);
  const pass = judgmentPass(score, threshold, upper);
  const tone = judgmentTone(pass);
  const margin = judgmentMargin(score, threshold);
  const usesPipeline =
    channel?.pipelineScore != null && Number.isFinite(channel.pipelineScore);

  return (
    <div className={`rounded-lg border-2 p-3 transition-colors duration-75 ${channelBorder(tone)}`}>
      <div className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">{title}</div>
      {channel && score != null ? (
        <div className="mt-1.5 space-y-1">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-sm font-semibold tabular-nums">
              {channel.metricLabel}
              <span className="text-muted-foreground font-normal"> · </span>
              <span className="text-lg font-black text-foreground">{score}</span>
            </p>
            <Badge
              variant={pass === true ? 'default' : pass === false ? 'destructive' : 'secondary'}
              className="text-xs font-bold"
            >
              {pass === true ? 'PASS' : pass === false ? 'FAIL' : '—'}
            </Badge>
          </div>
          {channel.detail && (
            <p className="text-[11px] text-muted-foreground leading-snug">{channel.detail}</p>
          )}
          {usesPipeline && (
            <p className="text-[10px] text-muted-foreground">
              Pipeline match {channel.pipelineScore!.toFixed(1)}% · fast {channel.fastScore}
            </p>
          )}
          <JudgmentMeter score={score} threshold={threshold} upper={upper} />
          <p className="text-[10px] text-muted-foreground tabular-nums">
            Margin{' '}
            <span className="font-mono font-semibold text-foreground">
              {margin != null && margin >= 0 ? '+' : ''}
              {margin?.toFixed(1) ?? '—'}
            </span>{' '}
            vs limit {threshold}%
            {upper != null ? ` – ${upper}%` : ''}
          </p>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground mt-2 py-2">{waitingText ?? 'No reading'}</p>
      )}
    </div>
  );
}

export interface RealTimeJudgmentStripProps {
  snapshot: ToolJudgmentSnapshot | null;
  threshold: number;
  upperLimit?: number;
  busy?: boolean;
  hasMasterImage: boolean;
  hasJudgmentTarget: boolean;
  livePaused?: boolean;
  hasLiveFrame?: boolean;
  onApplySuggestedThreshold?: (value: number) => void;
}

export function RealTimeJudgmentStrip({
  snapshot,
  threshold,
  upperLimit,
  busy,
  hasMasterImage,
  hasJudgmentTarget,
  livePaused,
  hasLiveFrame,
  onApplySuggestedThreshold,
}: RealTimeJudgmentStripProps) {
  const masterScore = displayScore(snapshot?.master);
  const masterPass = judgmentPass(masterScore, threshold, upperLimit);
  const overallTone = judgmentTone(masterPass);
  const suggested = suggestThreshold(snapshot?.master);

  const shellClass =
    overallTone === 'pass'
      ? 'border-green-500/60 bg-green-50/50 dark:bg-green-950/25'
      : overallTone === 'fail'
        ? 'border-red-500/60 bg-red-50/50 dark:bg-red-950/25'
        : 'border-border bg-muted/20';

  return (
    <div role="status" className={`rounded-xl border-2 px-4 py-3 space-y-3 transition-colors duration-75 ${shellClass}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
          Real-time judgment
        </span>
        {busy && (
          <span className="text-xs text-muted-foreground animate-pulse">Analyzing ROI…</span>
        )}
      </div>

      {!hasMasterImage ? (
        <p className="text-sm text-muted-foreground">
          Load or register a master image (Step 1) to enable judgment on the Master Pi / wizard.
        </p>
      ) : !hasJudgmentTarget ? (
        <p className="text-sm text-muted-foreground">
          Draw a ROI on the master canvas, or select a configured tool to see pass/fail while tuning the threshold.
        </p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <ChannelCard
              title="Master (reference)"
              channel={snapshot?.master ?? null}
              threshold={threshold}
              upper={upperLimit}
              waitingText={busy ? 'Computing…' : 'Waiting for master ROI…'}
            />
            <ChannelCard
              title="Live camera"
              channel={snapshot?.live ?? null}
              threshold={threshold}
              upper={upperLimit}
              waitingText={
                livePaused
                  ? 'Resume the camera for live judgment.'
                  : hasLiveFrame
                    ? 'Computing live ROI…'
                    : 'Live frame not available yet.'
              }
            />
          </div>

          {suggested != null && onApplySuggestedThreshold && (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <span className="text-xs text-muted-foreground">
                Suggested limit from master signal:{' '}
                <span className="font-mono font-semibold text-foreground">{suggested}%</span>
              </span>
              <Button
                type="button"
                size="sm"
                variant="secondary"
                className="h-7 text-xs"
                onClick={() => onApplySuggestedThreshold(suggested)}
              >
                Use {suggested}%
              </Button>
            </div>
          )}

          <p className="text-[11px] text-muted-foreground leading-relaxed">
            Fast metrics refresh when the ROI or scene changes (~80ms). Pass/fail on the slider is instant. When the
            pipeline finishes, template match % replaces the fast score. Save &amp; run once for production results.
          </p>
        </>
      )}
    </div>
  );
}
