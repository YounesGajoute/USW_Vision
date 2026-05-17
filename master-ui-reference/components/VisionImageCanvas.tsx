import { useEffect, useRef } from 'react'
import { imageDataUrl } from '../services/visionService'
import type { CaptureMeta } from '../lib/visionWizard'
import { resolutionLabel } from '../lib/visionWizard'
import type { LiveFeedStats } from '../hooks/useVisionLiveFeed'

const CANVAS_W = 960
const CANVAS_H = 540

interface VisionImageCanvasProps {
  imageB64: string | null
  emptyLabel: string
  live?: boolean
  liveStats?: LiveFeedStats
  captureMeta?: CaptureMeta | null
  formatHint?: string
}

export function VisionImageCanvas({
  imageB64,
  emptyLabel,
  live = false,
  liveStats,
  captureMeta,
  formatHint,
}: VisionImageCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !imageB64) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const img = new Image()
    img.onload = () => {
      if (!canvasRef.current) return
      const c = canvasRef.current
      const cx = c.getContext('2d')
      if (!cx) return
      cx.fillStyle = '#000'
      cx.fillRect(0, 0, c.width, c.height)
      const scale = Math.min(c.width / img.width, c.height / img.height)
      const dw = img.width * scale
      const dh = img.height * scale
      const dx = (c.width - dw) / 2
      const dy = (c.height - dh) / 2
      cx.drawImage(img, dx, dy, dw, dh)

      if (live && liveStats) {
        cx.fillStyle = 'rgba(0,0,0,0.55)'
        cx.fillRect(8, 8, 280, 48)
        cx.fillStyle = '#f8fafc'
        cx.font = 'bold 12px monospace'
        cx.fillText(`Live  ${liveStats.resolution || '—'}`, 14, 28)
        cx.fillStyle = '#cbd5e1'
        cx.font = '11px monospace'
        const lat = liveStats.latencyMs > 0 ? `${liveStats.latencyMs} ms` : ''
        cx.fillText(`FPS ${liveStats.fps || '–'}  ${lat}`, 14, 46)
      }
    }
    img.src = imageDataUrl(imageB64, formatHint) ?? ''
  }, [imageB64, live, liveStats, formatHint])

  const footerRes = live
    ? liveStats?.resolution || '—'
    : resolutionLabel(captureMeta ?? null)

  return (
    <div>
      <div style={{ position: 'relative', maxWidth: CANVAS_W, margin: '0 auto' }}>
        <canvas
          ref={canvasRef}
          width={CANVAS_W}
          height={CANVAS_H}
          style={{ display: 'block', width: '100%', height: 'auto', background: '#000', borderRadius: 8 }}
        />
        {!imageB64 && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#94a3b8',
              fontSize: 15,
            }}
          >
            {emptyLabel}
          </div>
        )}
      </div>
      {imageB64 && (
        <p style={{ margin: '8px 0 0', fontSize: 13, color: '#64748b' }}>
          <span
            style={{
              display: 'inline-block',
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: live ? '#22c55e' : '#94a3b8',
              marginRight: 6,
              verticalAlign: 'middle',
            }}
          />
          {live
            ? `~${liveStats?.fps ?? '–'} fps · ${liveStats?.latencyMs ?? '–'} ms · ${footerRes} (full-res PNG)`
            : `${footerRes} (still)`}
        </p>
      )}
    </div>
  )
}
