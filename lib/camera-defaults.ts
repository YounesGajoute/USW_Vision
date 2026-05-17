/**
 * Fixed camera pipeline defaults when the wizard does not expose sensor tuning.
 * Keep in sync with backend defaults in program_manager / inspection_engine.
 */
export const DEFAULT_CAMERA_CAPTURE = {
  brightnessMode: 'normal' as const,
  focusValue: 50,
  exposureTimeUs: 5000,
  analogGain: 2,
  digitalGain: 1,
};
