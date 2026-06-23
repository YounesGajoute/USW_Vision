import type { CaptureOptions, LiveFeedSubscribeOptions } from '@/types';
import { DEFAULT_CAMERA_CAPTURE } from '@/lib/camera-defaults';

/** Build subscribe_live_feed payload matching POST /camera/capture. */
export function liveFeedSubscribePayload(
  fps: number,
  fullResolution: boolean,
  captureOptions?: CaptureOptions
): LiveFeedSubscribeOptions {
  const opts = captureOptions ?? {
    brightnessMode: DEFAULT_CAMERA_CAPTURE.brightnessMode,
    focusValue: DEFAULT_CAMERA_CAPTURE.focusValue,
    exposureTime: DEFAULT_CAMERA_CAPTURE.exposureTimeUs,
    analogGain: DEFAULT_CAMERA_CAPTURE.analogGain,
    digitalGain: DEFAULT_CAMERA_CAPTURE.digitalGain,
  };
  return {
    fps,
    fullResolution,
    useCaptureSettings: true,
    /** Native 1456×1088 + capture-grade sensor settle (backend default when fullResolution). */
    captureGrade: fullResolution,
    ...opts,
  };
}
