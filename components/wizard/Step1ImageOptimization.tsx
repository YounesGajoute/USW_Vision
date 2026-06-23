"use client"

import { useState, useEffect, useRef } from "react"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Camera,
  Settings,
  Image as ImageIcon,
  CheckCircle2,
  AlertCircle,
  Info,
  Play,
  Pause,
} from "lucide-react"
import { api } from "@/lib/api"
import { ws } from "@/lib/websocket"
import { useToast } from "@/components/ui/use-toast"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import type { CameraInfo, ImageQuality } from "@/types"
import { enableHighQualityCanvasScaling, rawBase64ToImageDataUrl } from "@/lib/inspection-engine"
import { DEFAULT_CAMERA_CAPTURE } from "@/lib/camera-defaults"

interface Step1Props {
  /** Called after a successful test capture (production gate for Step 2). */
  onStep1Validated?: () => void
  step1Validated?: boolean
}

export default function Step1ImageOptimization({
  onStep1Validated,
  step1Validated,
}: Step1Props) {
  const { toast } = useToast()
  const d = DEFAULT_CAMERA_CAPTURE

  const [isPreviewActive, setIsPreviewActive] = useState(false)
  const [currentFps, setCurrentFps] = useState(0)
  const [processingTime, setProcessingTime] = useState(0)

  const [imageQuality, setImageQuality] = useState<ImageQuality | null>(null)
  const [isCapturing, setIsCapturing] = useState(false)
  const [cameraStatus, setCameraStatus] = useState<"connected" | "disconnected" | "error">(
    "disconnected"
  )
  const [cameraInfo, setCameraInfo] = useState<CameraInfo | null>(null)

  const [liveQuality, setLiveQuality] = useState<Partial<ImageQuality> | null>(null)
  const [liveResolution, setLiveResolution] = useState<string>("")
  const frameCountRef = useRef(0)
  const lastFrameTsRef = useRef<number>(0)

  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    checkCameraConnection()
    api.getCameraInfo().then(setCameraInfo).catch(() => {})
  }, [])

  const checkCameraConnection = async () => {
    const cameraOk = (data: unknown): boolean => {
      const c = (data as { components?: { camera?: unknown }; camera_ok?: boolean })?.components
        ?.camera
      if (c === "ok") return true
      if (typeof c === "object" && c !== null && "status" in (c as object)) {
        const s = (c as { status?: string }).status
        return s === "healthy" || s === "degraded"
      }
      if ((data as { camera_ok?: boolean })?.camera_ok === true) return true
      return false
    }

    const urls = [
      "/api/health",
      "/api/v1/health",
      "/api/health/full",
      "/api/v1/health/full",
    ]
    let data: unknown = null
    for (const u of urls) {
      try {
        const res = await fetch(u)
        if (res.ok) {
          data = await res.json()
          break
        }
      } catch {
        /* try next */
      }
    }
    if (!data) {
      setCameraStatus("error")
      return
    }
    setCameraStatus(cameraOk(data) ? "connected" : "error")
  }

  const captureRealImage = async () => {
    if (cameraStatus !== "connected") {
      toast({
        title: "Camera Not Available",
        description: "Camera not detected. Check the CSI cable and libcamera.",
        variant: "destructive",
      })
      return
    }

    setIsCapturing(true)
    try {
      const result = await api.captureImage({
        brightnessMode: d.brightnessMode,
        focusValue: d.focusValue,
        exposureTime: d.exposureTimeUs,
        analogGain: d.analogGain,
        digitalGain: d.digitalGain,
      })

      setImageQuality(result.quality ?? null)
      if (result.cameraInfo) setCameraInfo(result.cameraInfo)

      if (canvasRef.current && result.image) {
        const img = new Image()
        img.onload = () => {
          const ctx = canvasRef.current?.getContext("2d")
          if (ctx && canvasRef.current) {
            enableHighQualityCanvasScaling(ctx)
            ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height)
            ctx.drawImage(img, 0, 0, canvasRef.current.width, canvasRef.current.height)
          }
        }
        img.src = rawBase64ToImageDataUrl(result.image)
      }

      toast({
        title: "Test capture OK",
        description: `Quality: ${result.quality?.score?.toFixed(1) ?? "—"}/100`,
      })
      onStep1Validated?.()
    } catch {
      toast({
        title: "Capture Failed",
        description: "Could not capture from the camera.",
        variant: "destructive",
      })
    } finally {
      setIsCapturing(false)
    }
  }

  useEffect(() => {
    if (!isPreviewActive) return

    let mounted = true

    const handleFrame = (data: {
      image?: string
      timestamp?: number
      latencyMs?: number
      quality?: Partial<ImageQuality>
      resolution?: string
      fps?: number
    }) => {
      if (!mounted || !canvasRef.current || !data.image) return

      const img = new Image()
      img.onload = () => {
        const canvas = canvasRef.current
        if (!canvas) return
        const ctx = canvas.getContext("2d")
        if (!ctx) return
        enableHighQualityCanvasScaling(ctx)
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height)

        ctx.fillStyle = "rgba(0,0,0,0.55)"
        ctx.fillRect(8, 8, 240, 54)
        ctx.fillStyle = "#f8fafc"
        ctx.font = "bold 13px monospace"
        ctx.fillText(`IMX296  ${data.resolution || "—"}`, 14, 28)
        ctx.fillStyle = "#cbd5e1"
        ctx.font = "12px monospace"
        ctx.fillText(
          `FPS: ${data.fps ?? "–"}  Lat: ${data.latencyMs ?? "–"}ms  (capture exposure + lighting)`,
          14,
          50
        )
      }
      img.src = rawBase64ToImageDataUrl(data.image ?? "")

      const now = data.timestamp ?? Date.now() / 1000
      if (lastFrameTsRef.current > 0) {
        const elapsed = now - lastFrameTsRef.current
        if (elapsed > 0) setCurrentFps(Math.round(1 / elapsed))
      }
      lastFrameTsRef.current = now
      frameCountRef.current += 1

      if (data.latencyMs !== undefined) setProcessingTime(Math.round(data.latencyMs))
      if (data.quality) setLiveQuality(data.quality)
      if (data.resolution) setLiveResolution(data.resolution)
    }

    const handleStarted = () => {
      toast({ title: "Live preview", description: "Streaming camera frames." })
    }

    ws.on("live_frame", handleFrame)
    ws.on("live_feed_started", handleStarted)

    const cancelPendingSubscribe = ws.subscribeLiveFeedWhenReady(4, true, {
      brightnessMode: d.brightnessMode,
      focusValue: d.focusValue,
      exposureTime: d.exposureTimeUs,
      analogGain: d.analogGain,
      digitalGain: d.digitalGain,
    })

    return () => {
      mounted = false
      cancelPendingSubscribe()
      ws.off("live_frame", handleFrame)
      ws.off("live_feed_started", handleStarted)
      ws.unsubscribeLiveFeed()
    }
  }, [isPreviewActive, d.brightnessMode, d.focusValue, d.exposureTimeUs, d.analogGain, d.digitalGain])

  return (
    <div className="space-y-6">
      {step1Validated ? (
        <Alert className="border-primary/30 bg-primary/5 [&>svg]:text-primary">
          <CheckCircle2 />
          <AlertTitle>Step 1 verified</AlertTitle>
          <AlertDescription>
            Test capture succeeded. You can continue to the master image step.
          </AlertDescription>
        </Alert>
      ) : (
        <Alert>
          <Info />
          <AlertTitle>Camera check</AlertTitle>
          <AlertDescription>
            Run one successful test capture to confirm the camera before continuing.
          </AlertDescription>
        </Alert>
      )}

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-3xl font-bold flex flex-wrap items-center gap-3">
            Step 1: Camera check
            <Badge
              variant={cameraStatus === "connected" ? "default" : "destructive"}
              className={cameraStatus === "connected" ? "bg-primary" : ""}
            >
              <Camera className="mr-1 h-3 w-3" />
              {cameraStatus === "connected"
                ? `Connected${cameraInfo ? ` (${cameraInfo.native_resolution})` : ""}`
                : "Disconnected"}
            </Badge>
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            Inspection uses fixed system defaults for exposure and gain (no tuning in this wizard).
          </p>
        </div>
        <Button
          variant="default"
          onClick={captureRealImage}
          disabled={cameraStatus !== "connected" || isCapturing}
        >
          <Camera className="mr-2 h-4 w-4" />
          {isCapturing ? "Capturing…" : "Capture test image"}
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_340px]">
        <Card className="p-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-lg font-semibold flex items-center gap-2">
                <ImageIcon className="h-5 w-5" />
                Live preview
              </h3>
              <p className="text-xs text-muted-foreground mt-0.5">
                WebSocket stream {liveResolution ? `· ${liveResolution}` : ""}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {cameraStatus !== "connected" && (
                <Badge variant="destructive">
                  <AlertCircle className="mr-1 h-3 w-3" />
                  Offline
                </Badge>
              )}
              <Button
                variant={isPreviewActive ? "destructive" : "default"}
                size="sm"
                disabled={cameraStatus !== "connected"}
                onClick={() => setIsPreviewActive((p) => !p)}
              >
                {isPreviewActive ? (
                  <>
                    <Pause className="mr-2 h-4 w-4" />
                    Stop
                  </>
                ) : (
                  <>
                    <Play className="mr-2 h-4 w-4" />
                    Start preview
                  </>
                )}
              </Button>
            </div>
          </div>

          <div className="relative">
            <canvas
              ref={canvasRef}
              width={960}
              height={540}
              className="w-full rounded-lg border-2 border-border bg-black"
            />
            {!isPreviewActive && (
              <div className="absolute inset-0 flex flex-col items-center justify-center rounded-lg bg-black/80">
                <Camera className="h-12 w-12 text-muted-foreground mb-3" />
                <p className="text-muted-foreground font-medium">Preview stopped</p>
                <p className="text-xs text-muted-foreground mt-1">Start preview to stream frames</p>
              </div>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-2 flex items-center gap-2">
            <span
              className={`inline-block h-2 w-2 rounded-full ${isPreviewActive ? "animate-pulse bg-primary" : "bg-gray-500"}`}
            />
            {isPreviewActive ? `~${currentFps} fps · ${processingTime} ms latency` : "Preview idle"}
          </p>
        </Card>

        <div className="space-y-4">
          <Card className="p-6">
            <h3 className="text-sm font-semibold text-muted-foreground uppercase mb-3">Camera</h3>
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between gap-2">
                <dt className="text-muted-foreground">Model</dt>
                <dd className="font-medium font-mono">{cameraInfo?.model?.toUpperCase() ?? "—"}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-muted-foreground">Resolution</dt>
                <dd>{cameraInfo?.native_resolution ?? "—"}</dd>
              </div>
            </dl>
          </Card>

          <Card className="p-6">
            <h3 className="text-sm font-semibold text-muted-foreground uppercase mb-3">Feed</h3>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">FPS</span>
                <span className="font-mono">{isPreviewActive ? currentFps : "—"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Latency</span>
                <span className="font-mono">{isPreviewActive ? `${processingTime} ms` : "—"}</span>
              </div>
            </div>
          </Card>

          {imageQuality && (
            <Card className="p-6 border-primary/30 bg-primary/5">
              <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-primary" />
                Last capture
              </h3>
              <p className="text-2xl font-mono font-bold">{imageQuality.score.toFixed(1)}/100</p>
              <p className="text-xs text-muted-foreground mt-1">Composite quality score</p>
            </Card>
          )}

          {isPreviewActive && liveQuality?.score != null && (
            <Card className="p-6 border-border">
              <h3 className="text-sm font-semibold text-muted-foreground uppercase mb-2">Live preview</h3>
              <p className="text-2xl font-mono font-bold">{liveQuality.score.toFixed(1)}/100</p>
              <dl className="mt-3 grid grid-cols-2 gap-x-2 gap-y-1 text-xs text-muted-foreground">
                <dt>Exposure</dt>
                <dd className="font-mono text-foreground text-right">
                  {(liveQuality.exposure ?? 0).toFixed(0)}
                </dd>
                <dt>Detail</dt>
                <dd className="font-mono text-foreground text-right">
                  {(liveQuality.sharpness_index ?? 0).toFixed(0)}
                </dd>
                <dt>Range</dt>
                <dd className="font-mono text-foreground text-right">
                  {(liveQuality.contrast ?? 0).toFixed(0)}
                </dd>
              </dl>
            </Card>
          )}

          <Button variant="outline" className="w-full" onClick={() => checkCameraConnection()}>
            <Settings className="mr-2 h-4 w-4" />
            Refresh camera status
          </Button>
        </div>
      </div>
    </div>
  )
}
