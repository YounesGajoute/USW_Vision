/** Shared helpers for master Settings → Vision (wizard ROI space unchanged). */

export interface CaptureMeta {
  w?: number
  h?: number
  format?: string
}

const PNG_MAGIC = 'iVBORw0KGgo'

export function stripDataUri(b64: string): string {
  const trimmed = b64.trim()
  if (!trimmed.includes(',')) return trimmed
  return trimmed.split(',', 1)[1] ?? trimmed
}

export function detectMimeFromB64(b64: string, formatHint?: string): string {
  const fmt = (formatHint ?? '').toLowerCase()
  if (fmt === 'png') return 'image/png'
  if (fmt === 'jpg' || fmt === 'jpeg') return 'image/jpeg'
  const raw = stripDataUri(b64)
  if (raw.startsWith(PNG_MAGIC)) return 'image/png'
  if (raw.startsWith('/9j/')) return 'image/jpeg'
  return 'image/png'
}

export function extensionForMime(mime: string): 'png' | 'jpg' {
  return mime === 'image/png' ? 'png' : 'jpg'
}

/** Pull base64 from vision GET capture/master or proxied JSON bodies. */
export function extractImageB64(data: Record<string, unknown> | null | undefined): string | null {
  if (!data) return null
  const direct = data.image ?? data.Image
  if (typeof direct === 'string' && direct.length > 32) {
    return stripDataUri(direct)
  }
  const nested = data.data
  if (nested && typeof nested === 'object') {
    const inner = (nested as Record<string, unknown>).image
    if (typeof inner === 'string' && inner.length > 32) {
      return stripDataUri(inner)
    }
  }
  return null
}

export function applyCaptureMeta(data: Record<string, unknown>): CaptureMeta {
  const w = data.width ?? data.nativeWidth
  const h = data.height ?? data.nativeHeight
  const format = typeof data.format === 'string' ? data.format : undefined
  return {
    w: typeof w === 'number' ? w : undefined,
    h: typeof h === 'number' ? h : undefined,
    format,
  }
}

export function b64ToFile(b64: string, filename: string, mime: string): File {
  const raw = stripDataUri(b64)
  const byteString = atob(raw)
  const ab = new ArrayBuffer(byteString.length)
  const ia = new Uint8Array(ab)
  for (let i = 0; i < byteString.length; i++) {
    ia[i] = byteString.charCodeAt(i)
  }
  return new File([ab], filename, { type: mime })
}

export function resolutionLabel(meta: CaptureMeta | null): string {
  if (meta?.w && meta?.h) return `${meta.w}×${meta.h}`
  return '—'
}
