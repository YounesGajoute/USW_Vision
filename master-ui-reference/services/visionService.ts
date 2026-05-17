/**
 * Master-side client for vision slave operations (via master backend proxy).
 * Default prefix: /api/vision — map to your US Machine proxy routes.
 */

import {
  b64ToFile,
  detectMimeFromB64,
  extensionForMime,
  extractImageB64,
  stripDataUri,
} from '../lib/visionWizard'

const VISION_API_PREFIX =
  (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_VISION_API_PREFIX) || '/api/vision'

async function visionFetch(path: string, init?: RequestInit): Promise<Response> {
  const url = `${VISION_API_PREFIX}${path.startsWith('/') ? path : `/${path}`}`
  const res = await fetch(url, init)
  return res
}

async function parseJsonOrThrow(res: Response): Promise<Record<string, unknown>> {
  const body = (await res.json().catch(() => ({}))) as Record<string, unknown>
  if (!res.ok) {
    const err = body.error ?? body.message ?? res.statusText
    throw new Error(typeof err === 'string' ? err : `HTTP ${res.status}`)
  }
  return body
}

export async function captureVisionFrame(
  opts: {
    brightnessMode?: 'normal' | 'hdr' | 'highgain'
    exposureTime?: number
    analogGain?: number
    digitalGain?: number
  } = {},
): Promise<Record<string, unknown>> {
  const res = await visionFetch('/camera/capture', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      brightnessMode: opts.brightnessMode ?? 'normal',
      ...(opts.exposureTime != null ? { exposureTime: opts.exposureTime } : {}),
      ...(opts.analogGain != null ? { analogGain: opts.analogGain } : {}),
      ...(opts.digitalGain != null ? { digitalGain: opts.digitalGain } : {}),
    }),
  })
  return parseJsonOrThrow(res)
}

export async function fetchMasterImage(programId: number): Promise<Record<string, unknown>> {
  const res = await visionFetch(`/master-image/${programId}`)
  if (res.status === 404) {
    throw new Error('404 Master image not found')
  }
  return parseJsonOrThrow(res)
}

/** POST multipart to vision slave (proxied). Re-encodes to lossless PNG on disk. */
export async function registerMasterImage(
  programId: number,
  b64: string,
  formatHint?: string,
): Promise<{ path?: string }> {
  const mime = detectMimeFromB64(b64, formatHint)
  const ext = extensionForMime(mime)
  const file = b64ToFile(b64, `program-${programId}-master.${ext}`, mime)
  const form = new FormData()
  form.append('file', file)
  form.append('programId', String(programId))

  const res = await visionFetch('/master-image', {
    method: 'POST',
    body: form,
  })
  const data = await parseJsonOrThrow(res)
  return { path: typeof data.path === 'string' ? data.path : undefined }
}

export function imageDataUrl(b64: string | null, formatHint?: string): string | null {
  if (!b64) return null
  const raw = stripDataUri(b64)
  const mime = detectMimeFromB64(raw, formatHint)
  return `data:${mime};base64,${raw}`
}

export { extractImageB64 }
