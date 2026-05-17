import { useEffect, useRef, useState } from 'react'
import { io, type Socket } from 'socket.io-client'
import { extractImageB64 } from '../lib/visionWizard'

export interface LiveFeedStats {
  fps: number
  latencyMs: number
  resolution: string
}

function resolveLiveSocketUrl(): string {
  if (typeof window === 'undefined') return ''
  const env = process.env.NEXT_PUBLIC_VISION_WS_URL ?? process.env.NEXT_PUBLIC_WS_URL
  if (env) return env
  return window.location.origin
}

/**
 * Subscribes to vision Pi live_frame via master Socket.IO proxy (or direct WS URL).
 * `programId` is reserved for future per-program feeds; live stream is global on the slave today.
 */
export function useVisionLiveFeed(programId: number | null, enabled: boolean) {
  const [frame, setFrame] = useState<string | null>(null)
  const [stats, setStats] = useState<LiveFeedStats>({ fps: 0, latencyMs: 0, resolution: '' })
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const lastTsRef = useRef(0)
  const socketRef = useRef<Socket | null>(null)

  useEffect(() => {
    if (!enabled || programId == null || typeof window === 'undefined') {
      setFrame(null)
      setConnected(false)
      return
    }

    let mounted = true
    lastTsRef.current = 0

    const url = resolveLiveSocketUrl()
    const socketKey = process.env.NEXT_PUBLIC_VISION_SOCKETIO_KEY
    const socket = io(url, {
      path: '/socket.io',
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionAttempts: Infinity,
      ...(socketKey ? { auth: { remoteKey: socketKey } } : {}),
    })
    socketRef.current = socket

    const onConnect = () => {
      if (!mounted) return
      setConnected(true)
      setError(null)
      socket.emit('subscribe_live_feed', { fps: 4, fullResolution: true })
    }

    const onDisconnect = () => {
      if (!mounted) return
      setConnected(false)
    }

    const onLiveFrame = (data: Record<string, unknown>) => {
      if (!mounted) return
      const b64 = extractImageB64(data)
      if (b64) setFrame(b64)

      const now =
        typeof data.timestamp === 'number' ? data.timestamp : Date.now() / 1000
      if (lastTsRef.current > 0) {
        const elapsed = now - lastTsRef.current
        if (elapsed > 0) {
          setStats(s => ({ ...s, fps: Math.round(1 / elapsed) }))
        }
      }
      lastTsRef.current = now

      if (typeof data.latencyMs === 'number') {
        setStats(s => ({ ...s, latencyMs: Math.round(data.latencyMs as number) }))
      }
      if (typeof data.resolution === 'string') {
        setStats(s => ({ ...s, resolution: data.resolution as string }))
      }
    }

    const onConnectError = (err: Error) => {
      if (!mounted) return
      setError(err.message || 'Live feed connection failed')
    }

    socket.on('connect', onConnect)
    socket.on('disconnect', onDisconnect)
    socket.on('live_frame', onLiveFrame)
    socket.io.on('error', onConnectError)

    return () => {
      mounted = false
      socket.emit('unsubscribe_live_feed')
      socket.off('connect', onConnect)
      socket.off('disconnect', onDisconnect)
      socket.off('live_frame', onLiveFrame)
      socket.io.off('error', onConnectError)
      socket.disconnect()
      socketRef.current = null
    }
  }, [enabled, programId])

  return { frame, stats, connected, error }
}
