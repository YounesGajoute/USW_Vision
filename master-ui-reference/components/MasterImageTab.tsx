/**
 * Settings → Vision → Master image (US Machine / master RPI).
 * Copy into your master repo; wire @/ imports to ThemeContext and paths.
 */
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { useTheme } from '@/contexts/ThemeContext'
import { useVisionLiveFeed } from '@/hooks/useVisionLiveFeed'
import {
  applyCaptureMeta,
  detectMimeFromB64,
  type CaptureMeta,
} from '@/lib/visionWizard'
import { extractImageB64 } from '@/lib/visionWizard'
import {
  captureVisionFrame,
  fetchMasterImage,
  registerMasterImage,
} from '@/services/visionService'
import { VisionImageCanvas } from './VisionImageCanvas'

const ACCEPTED_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/jpg']
/** Matches vision slave validate_file_upload(max_size_mb=10) */
const MAX_FILE_BYTES = 10 * 1024 * 1024

const TOUCH_BTN: CSSProperties = {
  minWidth: 48,
  minHeight: 48,
  padding: '12px 22px',
  borderRadius: 10,
  fontWeight: 700,
  fontSize: 15,
  cursor: 'pointer',
  touchAction: 'manipulation',
  userSelect: 'none',
}

interface MasterImageTabProps {
  programId: number | null
  busy: boolean
  setBusy: (v: boolean) => void
  onMessage: (msg: string) => void
  onError: (msg: string) => void
  onMasterImageChange?: (b64: string | null) => void
}

export function MasterImageTab({
  programId,
  busy,
  setBusy,
  onMessage,
  onError,
  onMasterImageChange,
}: MasterImageTabProps) {
  const { colors } = useTheme()
  const [stillB64, setStillB64] = useState<string | null>(null)
  const [stillFormat, setStillFormat] = useState<string>('png')
  const [captureMeta, setCaptureMeta] = useState<CaptureMeta | null>(null)
  const [isRegistered, setIsRegistered] = useState(false)
  const [liveOn, setLiveOn] = useState(true)

  const registeredB64Ref = useRef<string | null>(null)
  const loadGenRef = useRef(0)
  const localDraftRef = useRef(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const onErrorRef = useRef(onError)
  const onMessageRef = useRef(onMessage)
  const onMasterImageChangeRef = useRef(onMasterImageChange)
  onErrorRef.current = onError
  onMessageRef.current = onMessage
  onMasterImageChangeRef.current = onMasterImageChange

  const liveEnabled = programId != null && liveOn && stillB64 == null
  const { frame: liveFrame, stats: liveStats } = useVisionLiveFeed(programId, liveEnabled)

  const displayB64 = stillB64 ?? liveFrame
  const showingLive = stillB64 == null && liveOn && programId != null
  const registered = isRegistered && stillB64 != null

  const applyStill = useCallback(
    (
      b64: string,
      meta?: CaptureMeta,
      opts?: { fromServer?: boolean; format?: string },
    ) => {
      const fmt = opts?.format ?? meta?.format ?? detectMimeFromB64(b64)
      const mimeFmt = fmt.includes('/') ? fmt.split('/')[1] : fmt
      setStillB64(b64)
      setStillFormat(mimeFmt === 'jpeg' ? 'jpg' : mimeFmt)
      setCaptureMeta(meta ?? null)
      setLiveOn(false)
      if (opts?.fromServer) {
        registeredB64Ref.current = b64
        localDraftRef.current = false
        setIsRegistered(true)
      } else {
        localDraftRef.current = true
        setIsRegistered(false)
      }
      onMasterImageChangeRef.current?.(b64)
    },
    [],
  )

  const resumeLive = useCallback(() => {
    loadGenRef.current += 1
    localDraftRef.current = false
    setStillB64(null)
    setCaptureMeta(null)
    setLiveOn(true)
    onErrorRef.current('')
    if (isRegistered && registeredB64Ref.current) {
      onMasterImageChangeRef.current?.(registeredB64Ref.current)
    }
  }, [isRegistered])

  const loadRegistered = useCallback(
    async (pid: number) => {
      const gen = ++loadGenRef.current
      try {
        const data = await fetchMasterImage(pid)
        if (gen !== loadGenRef.current) return
        if (localDraftRef.current) return
        const b64 = extractImageB64(data)
        if (b64) {
          const meta = applyCaptureMeta(data)
          const fmt = typeof data.format === 'string' ? data.format : 'png'
          applyStill(b64, meta, { fromServer: true, format: fmt })
        } else {
          registeredB64Ref.current = null
          setIsRegistered(false)
          onMasterImageChangeRef.current?.(null)
        }
      } catch (e) {
        if (gen !== loadGenRef.current) return
        if (localDraftRef.current) return
        registeredB64Ref.current = null
        setIsRegistered(false)
        onMasterImageChangeRef.current?.(null)
        const msg = e instanceof Error ? e.message : ''
        if (msg && !/404|not found/i.test(msg)) {
          onErrorRef.current(msg)
        }
      }
    },
    [applyStill],
  )

  useEffect(() => {
    loadGenRef.current += 1
    localDraftRef.current = false
    setStillB64(null)
    setCaptureMeta(null)
    setLiveOn(true)
    setIsRegistered(false)
    registeredB64Ref.current = null
    onErrorRef.current('')
    if (programId != null) void loadRegistered(programId)
    else onMasterImageChangeRef.current?.(null)
  }, [programId, loadRegistered])

  const handleCapture = async () => {
    if (programId == null) {
      onErrorRef.current('Select a program first')
      return
    }
    loadGenRef.current += 1
    setBusy(true)
    onErrorRef.current('')
    try {
      const data = await captureVisionFrame()
      const b64 = extractImageB64(data)
      if (!b64) throw new Error('No image returned from camera')
      applyStill(b64, applyCaptureMeta(data), {
        format: typeof data.format === 'string' ? data.format : 'png',
      })
      onMessageRef.current('Frame captured — click Register to save on vision Pi')
    } catch (e) {
      onErrorRef.current(e instanceof Error ? e.message : 'Capture failed')
    } finally {
      setBusy(false)
    }
  }

  const handleLoadFile = (file: File) => {
    if (!ACCEPTED_IMAGE_TYPES.includes(file.type)) {
      onErrorRef.current('Use a PNG or JPEG image')
      return
    }
    if (file.size > MAX_FILE_BYTES) {
      onErrorRef.current('Image file is too large (max 10 MB)')
      return
    }
    loadGenRef.current += 1
    onErrorRef.current('')
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result
      if (typeof result !== 'string') {
        onErrorRef.current('Could not read file')
        return
      }
      const b64 = result.includes(',') ? result.split(',')[1] : result
      if (!b64) {
        onErrorRef.current('Could not read file')
        return
      }
      const fmt = file.type === 'image/png' ? 'png' : 'jpg'
      applyStill(b64, undefined, { format: fmt })
      onMessageRef.current(`Loaded ${file.name}`)
    }
    reader.onerror = () => onErrorRef.current('Could not read file')
    reader.readAsDataURL(file)
  }

  const handleRegister = async () => {
    if (programId == null || !stillB64) return
    if (registered) return
    setBusy(true)
    onErrorRef.current('')
    try {
      await registerMasterImage(programId, stillB64, stillFormat)
      await loadRegistered(programId)
      onMessageRef.current(`Master image registered for program #${programId}`)
    } catch (e) {
      onErrorRef.current(e instanceof Error ? e.message : 'Register failed')
    } finally {
      setBusy(false)
    }
  }

  const controlsDisabled = busy || programId == null
  const registerDisabled = controlsDisabled || !stillB64 || registered

  const btnBase: CSSProperties = {
    ...TOUCH_BTN,
    cursor: controlsDisabled ? 'not-allowed' : 'pointer',
    opacity: controlsDisabled ? 0.55 : 1,
  }

  return (
    <div>
      {programId == null && (
        <p style={{ margin: '0 0 12px', color: colors.textSecondary, fontSize: 15 }}>
          Select a vision program above to capture or register a master image.
        </p>
      )}

      <VisionImageCanvas
        imageB64={displayB64}
        emptyLabel={
          programId == null
            ? 'Select a program to start'
            : liveOn
              ? 'Waiting for live feed…'
              : 'No image — capture or load a file'
        }
        live={showingLive}
        liveStats={liveStats}
        captureMeta={stillB64 ? captureMeta : null}
        formatHint={stillFormat}
      />

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 16 }}>
        <button
          type="button"
          disabled={controlsDisabled}
          onClick={() => void handleCapture()}
          style={{ ...btnBase, backgroundColor: '#111', color: '#fff', border: 'none' }}
        >
          Capture
        </button>
        <button
          type="button"
          disabled={controlsDisabled}
          onClick={() => fileRef.current?.click()}
          style={{
            ...btnBase,
            backgroundColor: colors.white,
            color: colors.text,
            border: `2px solid ${colors.border}`,
          }}
        >
          Load File
        </button>
        <input
          ref={fileRef}
          type="file"
          accept={ACCEPTED_IMAGE_TYPES.join(',')}
          style={{ display: 'none' }}
          onChange={e => {
            const f = e.target.files?.[0]
            if (f) handleLoadFile(f)
            e.target.value = ''
          }}
        />
        <button
          type="button"
          disabled={registerDisabled}
          onClick={() => void handleRegister()}
          style={{
            ...btnBase,
            backgroundColor: registered ? colors.success : colors.grey,
            color: registered ? '#fff' : colors.text,
            border: registered ? 'none' : `1px solid ${colors.border}`,
            opacity: registerDisabled ? 0.55 : 1,
            cursor: registerDisabled ? 'not-allowed' : 'pointer',
          }}
        >
          {registered ? 'Registered' : 'Register'}
        </button>
        {stillB64 && (
          <button
            type="button"
            disabled={busy}
            onClick={resumeLive}
            style={{
              ...btnBase,
              backgroundColor: colors.white,
              color: colors.primary,
              border: `1px solid ${colors.primary}`,
              opacity: busy ? 0.55 : 1,
            }}
          >
            Resume live
          </button>
        )}
      </div>
    </div>
  )
}
