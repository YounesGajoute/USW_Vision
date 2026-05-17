'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Camera, CheckCircle2, AlertTriangle, Loader2, Upload, Video } from 'lucide-react';
import { api } from '@/lib/api';
import { ws } from '@/lib/websocket';
import { useToast } from '@/hooks/use-toast';
import type { CapturedImage, ImageQuality } from '@/types';
import { rawBase64ToImageDataUrl } from '@/lib/inspection-engine';

const CANVAS_W = 960;
const CANVAS_H = 540;
/** IMX296 full-resolution target for master capture and registration. */
const NATIVE_W = 1456;
const NATIVE_H = 1088;

interface Step2Props {
  programId?: number | null;
  masterImageRegistered: boolean;
  setMasterImageRegistered: (registered: boolean) => void;
  masterImagePath: string | null;
  setMasterImagePath: (path: string | null) => void;
  masterImageData: string | null;
  setMasterImageData: (data: string | null) => void;
  brightnessMode: 'normal' | 'hdr' | 'highgain';
  focusValue: number;
  exposureTimeUs: number;
  analogGain: number;
  digitalGain: number;
}

export default function Step2MasterImage({
  programId = null,
  masterImageRegistered,
  setMasterImageRegistered,
  masterImagePath,
  setMasterImagePath,
  masterImageData,
  setMasterImageData,
  brightnessMode,
  focusValue,
  exposureTimeUs,
  analogGain,
  digitalGain,
}: Step2Props) {
  const [capturedImage, setCapturedImage] = useState<string | null>(masterImageData);
  /** MIME for base64 payload (uploads may be PNG/WebP). */
  const [capturedMime, setCapturedMime] = useState<string>('image/jpeg');
  /** When true, canvas shows WebSocket live frames; when false, shows captured/uploaded still. */
  const [viewLive, setViewLive] = useState(() => !masterImageData);
  const [liveResolution, setLiveResolution] = useState('');
  const [currentFps, setCurrentFps] = useState(0);
  const [processingTime, setProcessingTime] = useState(0);
  const [liveFrameReceived, setLiveFrameReceived] = useState(false);
  const [liveStreamHint, setLiveStreamHint] = useState<string | null>(null);

  const [isCapturing, setIsCapturing] = useState(false);
  const [isRegistering, setIsRegistering] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [imageQuality, setImageQuality] = useState<ImageQuality | null>(null);
  const [captureResolution, setCaptureResolution] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewLiveRef = useRef(viewLive);
  const lastFrameTsRef = useRef(0);
  const { toast } = useToast();

  useEffect(() => {
    viewLiveRef.current = viewLive;
    if (viewLive) {
      setLiveFrameReceived(false);
      setLiveStreamHint(null);
    }
  }, [viewLive]);

  const dataUrlForCanvas = useCallback((b64: string, mime: string) => {
    if (b64.startsWith('data:')) return b64;
    return `data:${mime};base64,${b64}`;
  }, []);

  const drawStillOnCanvas = useCallback(
    (b64: string, mime: string) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      const img = new Image();
      img.onload = () => {
        if (!canvasRef.current) return;
        const c = canvasRef.current;
        const cx = c.getContext('2d');
        if (!cx) return;
        cx.drawImage(img, 0, 0, c.width, c.height);
      };
      img.src = dataUrlForCanvas(b64, mime);
    },
    [dataUrlForCanvas]
  );

  // Update captured image when masterImageData changes
  useEffect(() => {
    if (masterImageData) {
      setCapturedImage(masterImageData);
      setCapturedMime(
        masterImageData.trimStart().startsWith('iVBORw0KGgo') ? 'image/png' : 'image/jpeg'
      );
      setViewLive(false);
    }
  }, [masterImageData]);

  // Draw still when not in live mode and we have image data
  useEffect(() => {
    if (viewLive || !capturedImage) return;
    drawStillOnCanvas(capturedImage, capturedMime);
  }, [capturedImage, capturedMime, viewLive, drawStillOnCanvas]);

  // WebSocket live feed → same canvas
  useEffect(() => {
    if (!viewLive) return;

    let mounted = true;
    lastFrameTsRef.current = 0;

    const handleFrame = (data: {
      image?: string;
      timestamp?: number;
      latencyMs?: number;
      resolution?: string;
      fps?: number;
    }) => {
      if (!mounted || !viewLiveRef.current || !canvasRef.current || !data.image) return;

      const img = new Image();
      img.onload = () => {
        const canvas = canvasRef.current;
        if (!canvas || !viewLiveRef.current) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        setLiveFrameReceived(true);

        ctx.fillStyle = 'rgba(0,0,0,0.5)';
        ctx.fillRect(8, 8, 300, 48);
        ctx.fillStyle = '#f8fafc';
        ctx.font = 'bold 12px monospace';
        ctx.fillText(`Live  ${data.resolution ?? '—'}`, 14, 28);
        ctx.fillStyle = '#cbd5e1';
        ctx.font = '11px monospace';
        const fpsLabel = data.fps != null ? String(data.fps) : '–';
        const latLabel = data.latencyMs != null ? `${Math.round(data.latencyMs)} ms` : '';
        ctx.fillText(`FPS ${fpsLabel}  ${latLabel}`, 14, 46);
      };
      img.src = rawBase64ToImageDataUrl(data.image ?? '');

      const now = data.timestamp ?? Date.now() / 1000;
      if (lastFrameTsRef.current > 0) {
        const elapsed = now - lastFrameTsRef.current;
        if (elapsed > 0) setCurrentFps(Math.round(1 / elapsed));
      }
      lastFrameTsRef.current = now;
      if (data.latencyMs !== undefined) setProcessingTime(Math.round(data.latencyMs));
      if (data.resolution) setLiveResolution(data.resolution);
    };

    const handleConnected = () => {
      if (mounted) setLiveStreamHint(null);
    };
    const handleConnectError = (data: { message?: string }) => {
      if (!mounted) return;
      setLiveStreamHint(
        'Cannot reach the vision API on port 5000. Check that inspection-vision is running and Socket.IO auth is configured.'
      );
    };
    const handleSocketError = (data: { code?: string; message?: string }) => {
      if (!mounted) return;
      if (data.code === 'NO_CAMERA') {
        setLiveStreamHint(data.message ?? 'CSI camera not available.');
      }
    };
    const handleWarning = (data: { message?: string }) => {
      if (!mounted || !data.message) return;
      setLiveStreamHint(data.message);
    };

    ws.on('live_frame', handleFrame);
    ws.on('connected', handleConnected);
    ws.on('connect_error', handleConnectError);
    ws.on('error', handleSocketError);
    ws.on('warning', handleWarning);
    const cancelPendingSubscribe = ws.subscribeLiveFeedWhenReady(4, true);

    return () => {
      mounted = false;
      cancelPendingSubscribe();
      ws.off('live_frame', handleFrame);
      ws.off('connected', handleConnected);
      ws.off('connect_error', handleConnectError);
      ws.off('error', handleSocketError);
      ws.off('warning', handleWarning);
      ws.unsubscribeLiveFeed();
    };
  }, [viewLive]);

  const handleCapture = async () => {
    setIsCapturing(true);
    
    try {
      const result: CapturedImage = await api.captureImage({
        brightnessMode,
        focusValue,
        exposureTime: exposureTimeUs,
        analogGain,
        digitalGain,
      });
      
      setCapturedImage(result.image);
      setCapturedMime(
        result.format === 'png' || result.image.trimStart().startsWith('iVBORw0KGgo')
          ? 'image/png'
          : 'image/jpeg'
      );
      setMasterImageData(result.image);
      setMasterImageRegistered(false);
      setImageQuality(result.quality);
      const w = result.width ?? result.cameraInfo?.output_resolution?.split('×')?.[0];
      const h = result.height ?? result.cameraInfo?.output_resolution?.split('×')?.[1];
      const resLabel =
        result.width && result.height
          ? `${result.width}×${result.height}`
          : result.cameraInfo?.output_resolution ?? 'unknown';
      setCaptureResolution(resLabel);
      setViewLive(false);

      if (result.isNativeResolution === false) {
        toast({
          title: 'Resolution warning',
          description: `Capture is ${resLabel}, not native ${NATIVE_W}×${NATIVE_H}. Check camera config (CAMERA_RESOLUTION_*).`,
          variant: 'destructive',
        });
      } else if (result.quality.score < 70) {
        toast({
          title: "Image Quality Warning",
          description: "Image quality is below recommended threshold. Consider adjusting camera settings.",
          variant: "destructive",
        });
      } else {
        toast({
          title: "Image Captured",
          description: `${resLabel} · quality ${result.quality.score.toFixed(1)}/100 (lossless PNG)`,
        });
      }
    } catch (error) {
      console.error('Capture failed:', error);
      toast({
        title: "Capture Failed",
        description: error instanceof Error ? error.message : "Please check camera connection and try again",
        variant: "destructive",
      });
    } finally {
      setIsCapturing(false);
    }
  };

  const handleFileSelect = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    // Validate file type
    if (!file.type.startsWith('image/')) {
      toast({
        title: "Invalid File",
        description: "Please select an image file (JPEG, PNG, etc.)",
        variant: "destructive",
      });
      return;
    }

    // Validate file size (max 10MB)
    if (file.size > 10 * 1024 * 1024) {
      toast({
        title: "File Too Large",
        description: "Please select an image smaller than 10MB",
        variant: "destructive",
      });
      return;
    }

    setIsUploading(true);

    try {
      // Read file as base64
      const reader = new FileReader();
      reader.onload = (e) => {
        const result = e.target?.result as string;
        // Extract base64 data (remove data:image/...;base64, prefix)
        const base64Data = result.split(',')[1];
        
        setCapturedImage(base64Data);
        setCapturedMime(file.type || 'image/jpeg');
        setMasterImageData(base64Data);
        setMasterImageRegistered(false);
        setImageQuality(null); // Uploaded images don't have quality metrics
        setViewLive(false);
        
        toast({
          title: "Image Loaded",
          description: `Successfully loaded ${file.name}`,
        });
      };

      reader.onerror = () => {
        toast({
          title: "Load Failed",
          description: "Failed to read image file",
          variant: "destructive",
        });
      };

      reader.readAsDataURL(file);
    } catch (error) {
      console.error('File upload failed:', error);
      toast({
        title: "Upload Failed",
        description: error instanceof Error ? error.message : "Failed to load image",
        variant: "destructive",
      });
    } finally {
      setIsUploading(false);
      // Reset file input
      if (event.target) {
        event.target.value = '';
      }
    }
  };

  const handleRegister = async () => {
    if (!capturedImage) {
      toast({
        title: "No Image",
        description: "Please capture an image first",
        variant: "destructive",
      });
      return;
    }

    setIsRegistering(true);

    try {
      const byteString = atob(capturedImage);
      const ab = new ArrayBuffer(byteString.length);
      const ia = new Uint8Array(ab);
      for (let i = 0; i < byteString.length; i++) {
        ia[i] = byteString.charCodeAt(i);
      }
      const ext = capturedMime === 'image/png' ? 'png' : 'jpg';
      const blob = new Blob([ab], { type: capturedMime });
      const file = new File([blob], `master_image.${ext}`, { type: capturedMime });

      if (programId != null) {
        const replacing = masterImageRegistered;
        const upload = await api.uploadMasterImage(programId, file);
        setMasterImagePath(upload.path);
        setMasterImageRegistered(true);
        toast({
          title: replacing ? 'Master Image Replaced' : 'Master Image Registered',
          description: 'Previous master image removed; new reference saved on disk.',
        });
      } else {
        setMasterImagePath(capturedImage);
        setMasterImageRegistered(true);
        toast({
          title: 'Master Image Registered',
          description: 'Reference image will be saved when you save the program.',
        });
      }
    } catch (error) {
      console.error('Registration failed:', error);
      toast({
        title: "Registration Failed",
        description: error instanceof Error ? error.message : "Failed to register master image",
        variant: "destructive",
      });
    } finally {
      setIsRegistering(false);
    }
  };

  const getQualityColor = (score: number) => {
    if (score >= 80) return 'text-green-600';
    if (score >= 70) return 'text-yellow-600';
    return 'text-red-600';
  };

  const qualityWeakestHint = (q: ImageQuality): string => {
    const rows: [string, number][] = [
      ['Exposure & clipping', q.exposure],
      ['Tonal range', q.contrast ?? 0],
      ['Edge detail', q.sharpness_index ?? 0],
      ['Information', q.information ?? 0],
    ];
    const w = rows.reduce((a, b) => (b[1] < a[1] ? b : a));
    return `${w[0]} is weakest (${w[1].toFixed(0)}/100).`;
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold">Step 1: Master Image Registration</h2>
        <p className="text-sm text-muted-foreground mt-2 max-w-3xl">
          <strong>Save a program:</strong> capture or load a file, then click <strong>Register</strong> so this image is
          stored with the program at full sensor resolution ({NATIVE_W}×{NATIVE_H}, lossless PNG).
          <strong> Tool templates only:</strong> you can go to Tool Configuration after capture
          or file load without registering — draw your ROIs, save a template (tools only, no stored image), then register
          a real master when you create an inspection program.
        </p>
      </div>

      {/* Image Display Card */}
      <Card>
        {capturedImage && !viewLive && (
          <CardHeader className="flex flex-row justify-end pb-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="shrink-0"
              onClick={() => setViewLive(true)}
              disabled={isCapturing || isRegistering || isUploading}
            >
              <Video className="mr-2 h-4 w-4" />
              Resume live view
            </Button>
          </CardHeader>
        )}
        <CardContent className="space-y-4">
          <div className="relative rounded-lg border-2 border-border bg-black overflow-hidden">
            <canvas
              ref={canvasRef}
              width={CANVAS_W}
              height={CANVAS_H}
              className="block w-full h-auto max-h-[min(70vh,540px)] object-contain bg-black"
            />
            {viewLive && !liveFrameReceived && (
              <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/75 text-muted-foreground">
                <Camera className="h-12 w-12 mb-3 opacity-70" />
                <p className="font-medium text-foreground/90">Waiting for live stream…</p>
                <p className="text-xs mt-1 px-4 text-center max-w-md text-muted-foreground">
                  {liveStreamHint ??
                    'Connecting to the vision API on port 5000. Ensure inspection-vision is running and the CSI camera is ready.'}
                </p>
              </div>
            )}
            {!viewLive && !capturedImage && (
              <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/75 text-muted-foreground">
                <Camera className="h-12 w-12 mb-3 opacity-70" />
                <p className="font-medium text-foreground/90">No image yet</p>
                <p className="text-xs mt-1">Use Capture or Load file, or resume live view</p>
              </div>
            )}
          </div>
          {viewLive && (
            <p className="text-xs text-muted-foreground flex flex-wrap items-center gap-2">
              <span
                className={`inline-block h-2 w-2 rounded-full ${liveFrameReceived ? 'animate-pulse bg-primary' : 'bg-gray-500'}`}
              />
              {liveFrameReceived
                ? `~${currentFps} fps · ${processingTime} ms · ${liveResolution || 'stream'} (full-res PNG)`
                : 'Connecting… (native resolution stream)'}
            </p>
          )}

          {captureResolution && !viewLive && (
            <p className="text-xs text-muted-foreground">
              Stored capture: <strong>{captureResolution}</strong>
              {captureResolution === `${NATIVE_W}×${NATIVE_H}` ? ' (native IMX296)' : ''}
            </p>
          )}

          {/* Quality Metrics — multi-signal model (see backend image_quality module) */}
          {imageQuality && (
            <div className="space-y-2">
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 p-4 bg-accent/50 rounded-lg">
                <div className="text-center">
                  <p className="text-xs text-muted-foreground mb-1">Avg light (0–255)</p>
                  <p className="text-lg font-bold">{imageQuality.brightness.toFixed(1)}</p>
                </div>
                <div className="text-center">
                  <p className="text-xs text-muted-foreground mb-1">Typical light (median)</p>
                  <p className="text-lg font-bold">
                    {(imageQuality.luminance_median ?? imageQuality.brightness).toFixed(1)}
                  </p>
                </div>
                <div className="text-center">
                  <p className="text-xs text-muted-foreground mb-1">Tonal range (0–100)</p>
                  <p className="text-lg font-bold">{(imageQuality.contrast ?? 0).toFixed(1)}</p>
                </div>
                <div className="text-center">
                  <p className="text-xs text-muted-foreground mb-1">Detail (0–100)</p>
                  <p className="text-lg font-bold">{(imageQuality.sharpness_index ?? 0).toFixed(1)}</p>
                  <p className="text-[10px] text-muted-foreground mt-0.5">
                    Laplace var {imageQuality.sharpness.toFixed(0)}
                  </p>
                </div>
                <div className="text-center">
                  <p className="text-xs text-muted-foreground mb-1">Exposure (0–100)</p>
                  <p className="text-lg font-bold">{imageQuality.exposure.toFixed(1)}</p>
                </div>
                <div className="text-center">
                  <p className="text-xs text-muted-foreground mb-1">Quality score</p>
                  <p className={`text-lg font-bold ${getQualityColor(imageQuality.score)}`}>
                    {imageQuality.score.toFixed(1)}
                  </p>
                </div>
              </div>
              <p className="text-[11px] text-muted-foreground px-1 leading-snug">
                Score blends lighting comfort (mean + median), tonal spread (p5–p95), highlight/shadow
                roll-off, edge energy (Laplacian + Tenengrad on a normalized preview size), and a small
                entropy term to flag nearly uniform frames.
              </p>
            </div>
          )}

          {/* Action Buttons */}
          <div className="grid grid-cols-3 gap-3">
            <Button
              onClick={handleCapture}
              disabled={isCapturing || isRegistering || isUploading}
              size="lg"
              variant="default"
            >
              {isCapturing ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Capturing...
                </>
              ) : (
                <>
                  <Camera className="mr-2 h-4 w-4" />
                  Capture
                </>
              )}
            </Button>

            <Button
              onClick={handleFileSelect}
              disabled={isCapturing || isRegistering || isUploading}
              size="lg"
              variant="outline"
            >
              {isUploading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Loading...
                </>
              ) : (
                <>
                  <Upload className="mr-2 h-4 w-4" />
                  Load File
                </>
              )}
            </Button>

            <Button
              onClick={handleRegister}
              disabled={!capturedImage || isRegistering}
              size="lg"
              variant={masterImageRegistered ? 'secondary' : 'default'}
            >
              {isRegistering ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {masterImageRegistered ? 'Replacing…' : 'Registering…'}
                </>
              ) : masterImageRegistered ? (
                <>
                  <CheckCircle2 className="mr-2 h-4 w-4" />
                  Replace Master Image
                </>
              ) : (
                <>
                  <CheckCircle2 className="mr-2 h-4 w-4" />
                  Register
                </>
              )}
            </Button>
          </div>

          {/* Hidden File Input */}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            onChange={handleFileChange}
            className="hidden"
          />

          {capturedImage && !masterImageRegistered && (
            <div className="flex items-start gap-2 p-4 bg-muted/50 border rounded-lg">
              <AlertTriangle className="h-5 w-5 text-muted-foreground flex-shrink-0 mt-0.5" />
              <div className="text-sm text-muted-foreground">
                <p className="font-medium text-foreground">Not registered yet</p>
                <p className="mt-1">
                  You can use <strong>Next</strong> to draw tools and save a <strong>tool template</strong> without
                  registering this frame. To save a full inspection program later, come back to this step and click{' '}
                  <strong>Register</strong> before IO assignment.
                </p>
              </div>
            </div>
          )}

          {/* Registration Status */}
          {masterImageRegistered && (
            <div className="flex items-center gap-2 p-4 bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800 rounded-lg">
              <CheckCircle2 className="h-5 w-5 text-green-600 dark:text-green-400 flex-shrink-0" />
              <div>
                <p className="font-semibold text-green-900 dark:text-green-100">
                  Master image registered on disk
                </p>
                <p className="text-sm text-green-700 dark:text-green-300">
                  Capture or load a new frame, then click <strong>Replace Master Image</strong> to
                  delete the old file and save the new one.
                </p>
              </div>
            </div>
          )}

          {/* Quality Warning */}
          {imageQuality && imageQuality.score < 70 && (
            <div className="flex items-center gap-2 p-4 bg-yellow-50 dark:bg-yellow-950 border border-yellow-200 dark:border-yellow-800 rounded-lg">
              <AlertTriangle className="h-5 w-5 text-yellow-600 dark:text-yellow-400 flex-shrink-0" />
              <div>
                <p className="font-semibold text-yellow-900 dark:text-yellow-100">
                  Low image quality detected
                </p>
                <p className="text-sm text-yellow-700 dark:text-yellow-300 mt-1">
                  {qualityWeakestHint(imageQuality)}
                </p>
                <p className="text-sm text-yellow-700 dark:text-yellow-300 mt-1">
                  Try stronger or more even lighting, or adjust exposure / gain in the camera setup step. Fixed-focus
                  cameras do not have a focus control.
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

