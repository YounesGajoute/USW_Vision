"""IMX296 Global-Shutter Camera Controller via Picamera2 / libcamera (CSI only).

There is no USB / V4L2 webcam path: capture requires Picamera2 and a CSI camera.
If Picamera2 is missing or the sensor fails to open, capture_image() returns a
test pattern so the rest of the stack can still be exercised off-device.
"""

import re
import sys
import cv2
import numpy as np
from typing import Tuple, Dict, Optional, Callable, Set
import time
import threading

from src.utils.pi_venv_libcamera import ensure_system_libcamera_path

# Venvs hide apt's python3-libcamera; add system dist-packages before Picamera2 import.
ensure_system_libcamera_path()

from src.utils.logger import get_logger

logger = get_logger('camera')

PICAMERA2_IMPORT_ERROR: Optional[str] = None
try:
    from picamera2 import Picamera2  # type: ignore
    import picamera2 as _picamera2_mod

    PICAMERA2_AVAILABLE = True
    _pc2_file = getattr(_picamera2_mod, "__file__", "") or ""
    if (
        sys.prefix != sys.base_prefix
        and _pc2_file.startswith(sys.prefix)
        and "site-packages" in _pc2_file
    ):
        logger.warning(
            "picamera2 is installed in this venv (%s). On Raspberry Pi run "
            "`pip uninstall picamera2` and use only `apt install python3-picamera2` "
            "so it matches system libcamera.",
            _pc2_file,
        )
except ImportError as e:
    Picamera2 = None  # type: ignore
    PICAMERA2_AVAILABLE = False
    PICAMERA2_IMPORT_ERROR = str(e)
    logger.error(
        "Picamera2 not importable (%s). On Raspberry Pi install: "
        "`sudo apt install -y python3-libcamera python3-picamera2` "
        "(the backend adds system dist-packages to sys.path when using a venv).",
        PICAMERA2_IMPORT_ERROR,
    )

# Back-compat alias for any code that referenced the old name
RASPBERRY_PI = PICAMERA2_AVAILABLE

# IMX296 full pixel array; larger requests are clamped.
IMX296_NATIVE_W = 1456
IMX296_NATIVE_H = 1088
_VALID_ISP_MAIN_FORMATS = frozenset({"RGB161616", "BGR161616", "RGB888", "BGR888"})

# libcamera raises e.g. "Control DigitalGain is not advertised by libcamera" for some sensors / IPA builds.
_CONTROL_NOT_ADVERTISED_RE = re.compile(
    r"Control\s+(\w+)\s+is\s+not\s+advertised",
    re.IGNORECASE,
)


class CameraController:
    """
    IMX296 Global-Shutter camera controller.

    When Picamera2 / libcamera is available the IMX296 is driven directly over CSI.
    The sensor delivers 10-bit Bayer RAW; the ISP can output **RGB161616** (16-bit
    per channel, preferred) or **RGB888**, then we normalize to **uint8 RGB** for
    OpenCV, JPEG, and inspection tools.

    Output size is clamped to the native **1456×1088** pixel array.

    Controls applied to the IMX296:
    - ExposureTime  (µs)  – sensor exposure
    - AnalogueGain          – sensor gain (1.0–16.0 for IMX296)
    - No AF / LensPosition  – the IMX296 is a fixed-focus global-shutter sensor
    """

    # Preset exposure/gain combinations for common lighting conditions.
    # IMX296 integrates for at most ~16 ms at 60 fps (1/60 s ≈ 16 666 µs).
    BRIGHTNESS_MODES = {
        'normal':   {'AnalogueGain': 1.0, 'ExposureTime': 5000},   # well-lit inspection
        'hdr':      {'AnalogueGain': 1.0, 'ExposureTime': 15000},  # longer integration, no gain
        'highgain': {'AnalogueGain': 8.0, 'ExposureTime': 15000},  # dim / backlit scenes
    }

    def _set_controls_resilient(self, controls: Dict) -> None:
        """
        Apply Picamera2 ``set_controls``, removing keys libcamera rejects as "not advertised".

        Avoids spamming logs on every capture when e.g. DigitalGain exists in the API but
        this sensor pipeline does not expose it (common on IMX296 + current libcamera).
        """
        if not self.camera or not controls:
            return
        pending = dict(controls)
        # Upper bound avoids infinite loop if error message format changes.
        for _ in range(max(12, len(pending) + 4)):
            try:
                self.camera.set_controls(pending)
                return
            except Exception as e:
                msg = str(e)
                m = _CONTROL_NOT_ADVERTISED_RE.search(msg)
                if not m:
                    logger.warning("Could not apply sensor controls: %s", e)
                    return
                bad = m.group(1)
                drop_key = None
                for k in pending:
                    ks = k if isinstance(k, str) else str(k)
                    if ks == bad or ks.lower() == bad.lower():
                        drop_key = k
                        break
                if drop_key is None:
                    logger.warning("Could not apply sensor controls: %s", e)
                    return
                if bad not in self._controls_skip_logged:
                    self._controls_skip_logged.add(bad)
                    logger.info(
                        "Sensor/libcamera does not advertise control %r — omitting from "
                        "set_controls (other controls still apply). This is expected on some IMX296 builds.",
                        bad,
                    )
                del pending[drop_key]
                if not pending:
                    logger.debug("No sensor controls left after omitting unsupported keys: %s", msg)
                    return

    @staticmethod
    def _manual_exposure_with_awb(controls: Dict) -> Dict:
        """
        Manual exposure/gain needs AE off so values stick; keep AWB on so images
        don't pick up a green cast (common when AWB is disabled with fixed gains).
        """
        merged = dict(controls)
        merged['AeEnable'] = False
        merged['AwbEnable'] = True
        return merged

    @staticmethod
    def _clamp_resolution_to_sensor(resolution: Tuple[int, int]) -> Tuple[int, int]:
        w, h = int(resolution[0]), int(resolution[1])
        cw = min(max(w, 1), IMX296_NATIVE_W)
        ch = min(max(h, 1), IMX296_NATIVE_H)
        if (cw, ch) != (w, h):
            logger.warning(
                "Camera resolution %sx%s clamped to IMX296 maximum %sx%s",
                w, h, cw, ch,
            )
        return cw, ch

    def _isp_buffer_to_rgb_u8(self, buf: np.ndarray) -> np.ndarray:
        """
        Picamera2 main streams named RGB888 / RGB161616 expose BGR-ordered memory.
        On newer libcamera / ISP builds the buffer can be 4-channel (e.g. BGRX); using
        COLOR_BGR2RGB then fails with OpenCV 'Bad number of channels'. Branch on C.

        Some stacks return planar **(C, H, W)** or packed **YUYV** as **(H, W, 2)** while the
        stream is still labelled RGB888 — normalize layout first, then convert with fallbacks
        so capture never dies on ``scn is 2`` / ``Bad number of channels``.
        """
        if buf.dtype not in (np.uint8, np.uint16):
            raise TypeError(f"Unexpected capture dtype {buf.dtype}")

        buf = np.ascontiguousarray(np.asarray(buf))

        # Picamera2 / libcamera may expose CHW planar buffers; OpenCV expects HWC.
        if buf.ndim == 3:
            d0, d1, d2 = (int(buf.shape[0]), int(buf.shape[1]), int(buf.shape[2]))
            if d0 in (1, 2, 3, 4) and min(d1, d2) >= 32 and d0 < min(d1, d2):
                buf = np.ascontiguousarray(np.transpose(buf, (1, 2, 0)))

        def _tw_crop_hwc(b: np.ndarray) -> np.ndarray:
            tw = int(self.resolution[0])
            if b.ndim == 3 and b.shape[1] > tw:
                return b[:, :tw, ...]
            return b

        def _yuyv_to_rgb(b2: np.ndarray) -> np.ndarray:
            b8 = (b2 >> 8).astype(np.uint8) if b2.dtype == np.uint16 else b2.astype(np.uint8)
            return cv2.cvtColor(b8, cv2.COLOR_YUV2RGB_YUY2)

        def _uyvy_to_rgb(b2: np.ndarray) -> np.ndarray:
            b8 = (b2 >> 8).astype(np.uint8) if b2.dtype == np.uint16 else b2.astype(np.uint8)
            return cv2.cvtColor(b8, cv2.COLOR_YUV2RGB_UYVY)

        def _luma_to_rgb(b: np.ndarray) -> np.ndarray:
            if b.ndim == 2:
                g = (b >> 8).astype(np.uint8) if b.dtype == np.uint16 else b.astype(np.uint8)
            else:
                g0 = b[:, :, 0]
                g = (g0 >> 8).astype(np.uint8) if g0.dtype == np.uint16 else g0.astype(np.uint8)
            return cv2.cvtColor(g, cv2.COLOR_GRAY2RGB)

        rgb: np.ndarray

        if buf.ndim == 2:
            rgb = _luma_to_rgb(buf)
        elif buf.ndim == 3:
            c = int(buf.shape[2])
            if c == 4:
                try:
                    rgb = cv2.cvtColor(buf, cv2.COLOR_BGRA2RGB)
                except cv2.error:
                    rgb = _yuyv_to_rgb(buf[:, :, :2])
            elif c == 3:
                try:
                    rgb = cv2.cvtColor(buf, cv2.COLOR_BGR2RGB)
                except cv2.error as e:
                    # Mis-tagged packed YUV / stride quirks: try 2-channel chroma paths, then luma.
                    if not self._logged_nonstandard_isp_buffer:
                        logger.warning(
                            "BGR2RGB failed on Picamera2 buffer shape=%s dtype=%s strides=%s (%s); "
                            "trying YUV / luma fallbacks",
                            buf.shape,
                            buf.dtype,
                            buf.strides,
                            e,
                        )
                        self._logged_nonstandard_isp_buffer = True
                    twb = _tw_crop_hwc(buf)
                    try:
                        rgb = _yuyv_to_rgb(twb[:, :, :2])
                    except cv2.error:
                        try:
                            rgb = _uyvy_to_rgb(twb[:, :, :2])
                        except cv2.error:
                            rgb = _luma_to_rgb(twb)
            elif c == 2:
                twb = _tw_crop_hwc(buf)
                try:
                    rgb = _yuyv_to_rgb(twb)
                except cv2.error:
                    try:
                        rgb = _uyvy_to_rgb(twb)
                    except cv2.error:
                        rgb = _luma_to_rgb(twb)
            elif c == 1:
                rgb = cv2.cvtColor(buf, cv2.COLOR_GRAY2RGB)
            else:
                raise TypeError(f"Unexpected capture channel count {c} shape={buf.shape}")
            if c != 3 and not self._logged_nonstandard_isp_buffer:
                logger.info(
                    "Picamera2 capture buffer shape=%s dtype=%s — using extended ISP→RGB path",
                    buf.shape,
                    buf.dtype,
                )
                self._logged_nonstandard_isp_buffer = True
        else:
            raise TypeError(f"Unexpected capture ndim={buf.ndim} shape={buf.shape}")

        if buf.dtype == np.uint16 and rgb.dtype == np.uint16:
            return np.clip(
                np.rint(rgb.astype(np.float32) * (255.0 / 65535.0)), 0, 255
            ).astype(np.uint8)
        return rgb

    def __init__(
        self,
        resolution: Tuple[int, int] = (IMX296_NATIVE_W, IMX296_NATIVE_H),
        camera_device: int = 0,
        *,
        allow_test_pattern: bool = True,
        isp_output_format: str = "RGB161616",
    ):
        """
        Initialize camera controller.

        Args:
            resolution: Output resolution (ISP scales/clamps to IMX296 max 1456×1088).
            camera_device: Picamera2 camera index (`rpicam-still --list-cameras`, usually 0 for IMX296).
            allow_test_pattern: If False (production / Ethernet slave), missing CSI returns None instead
                of a synthetic pattern — no fake images in inspection or live stream.
            isp_output_format: Picamera2 main stream format (RGB161616 recommended for max tonal precision
                from the 10-bit sensor; falls back to RGB888 if configure fails).
        """
        fmt = (isp_output_format or "RGB161616").strip()
        if fmt not in _VALID_ISP_MAIN_FORMATS:
            logger.warning("Unknown isp_output_format %r — using RGB161616", fmt)
            fmt = "RGB161616"
        self.isp_output_format_requested = fmt
        self.isp_effective_format: str = fmt
        self.resolution = self._clamp_resolution_to_sensor(resolution)
        self.camera_device = camera_device
        self.allow_test_pattern = bool(allow_test_pattern)
        self.camera = None
        self.is_previewing = False
        self._logged_test_pattern_fallback = False
        self._logged_nonstandard_isp_buffer = False
        self._logged_capture_size_mismatch = False
        self._capture_fail_log_next = 0.0
        # Names of Picamera2 controls we skipped after libcamera said they are not advertised (log once each).
        self._controls_skip_logged: Set[str] = set()
        self._capture_lock = threading.Lock()
        self._connect()

    def _connect(self):
        """Open the CSI camera via Picamera2."""
        if not PICAMERA2_AVAILABLE:
            self.camera = None
            return
        try:
            self.camera = Picamera2(camera_num=int(self.camera_device))
            colour_space = None
            try:
                import libcamera  # type: ignore

                colour_space = libcamera.ColorSpace.Srgb()
            except Exception:
                pass

            def _try_configure(fmt: str) -> None:
                kwargs: Dict = {"main": {"size": self.resolution, "format": fmt}}
                if colour_space is not None:
                    kwargs["colour_space"] = colour_space
                cfg = self.camera.create_still_configuration(**kwargs)
                self.camera.configure(cfg)

            preferred = self.isp_output_format_requested
            try:
                _try_configure(preferred)
                self.isp_effective_format = preferred
            except Exception as e:
                if preferred != "RGB888":
                    logger.warning(
                        "Picamera2 configure format=%s failed (%s); falling back to RGB888",
                        preferred,
                        e,
                    )
                    _try_configure("RGB888")
                    self.isp_effective_format = "RGB888"
                else:
                    raise

            self.camera.start()
            logger.info(
                "Picamera2 CSI camera_num=%s at %sx%s isp=%s",
                self.camera_device,
                self.resolution[0],
                self.resolution[1],
                self.isp_effective_format,
            )
        except Exception as e:
            logger.error("Failed to initialize CSI camera (Picamera2): %s", e)
            self.camera = None

    def capture_image(
        self,
        brightness_mode: str = 'normal',
        focus_value: int = 50,
        exposure_time_us: Optional[int] = None,
        analog_gain: Optional[float] = None,
        digital_gain: Optional[float] = None,
        for_stream: bool = False,
        live_preview: bool = False,
    ) -> Optional[np.ndarray]:
        """
        Capture a single frame with the requested sensor controls.

        Args:
            brightness_mode: Preset name ('normal', 'hdr', 'highgain') used when
                             explicit exposure_time_us / analog_gain are not given.
            focus_value: 0-100.  Stored for record-keeping but has no effect on the
                         IMX296, which is a fixed-focus global-shutter sensor.
            exposure_time_us: Override exposure time in µs (ignores brightness_mode).
            analog_gain: Override analogue gain (ignores brightness_mode).
            digital_gain: Optional digital gain multiplier (applied by ISP).
            for_stream: If True (Picamera2), skip per-frame set_controls, settle sleep,
                        and logging — only capture_array(). Call once with False first
                        to apply exposure, then True for live preview FPS.
            live_preview: Web UI live stream only — full auto AE + AWB (like rpicam-still
                          preview). Ignored if exposure_time_us or analog_gain is set.

        Returns:
            RGB numpy array or None on failure.
        """
        with self._capture_lock:
            return self._capture_image_unlocked(
                brightness_mode=brightness_mode,
                focus_value=focus_value,
                exposure_time_us=exposure_time_us,
                analog_gain=analog_gain,
                digital_gain=digital_gain,
                for_stream=for_stream,
                live_preview=live_preview,
            )

    def _capture_image_unlocked(
        self,
        brightness_mode: str = 'normal',
        focus_value: int = 50,
        exposure_time_us: Optional[int] = None,
        analog_gain: Optional[float] = None,
        digital_gain: Optional[float] = None,
        for_stream: bool = False,
        live_preview: bool = False,
    ) -> Optional[np.ndarray]:
        """Internal capture implementation (caller must hold _capture_lock when camera is open)."""
        if not self.camera:
            if not self._logged_test_pattern_fallback:
                self._logged_test_pattern_fallback = True
                if self.allow_test_pattern:
                    logger.warning(
                        "CSI camera not available — using test pattern for captures/live feed "
                        "(set camera.allow_test_pattern false for production; fix Picamera2/libcamera)."
                    )
                else:
                    logger.error(
                        "CSI camera not available — captures return None (production mode; fix hardware)."
                    )
            if not self.allow_test_pattern:
                return None
            return self._generate_test_pattern()

        try:
            manual_exp = exposure_time_us is not None or analog_gain is not None
            if live_preview and not manual_exp:
                controls: Dict = {'AeEnable': True, 'AwbEnable': True}
                if digital_gain is not None:
                    controls['DigitalGain'] = float(digital_gain)
            elif manual_exp:
                controls = {}
                if exposure_time_us is not None:
                    controls['ExposureTime'] = int(exposure_time_us)
                if analog_gain is not None:
                    controls['AnalogueGain'] = float(analog_gain)
                if digital_gain is not None:
                    controls['DigitalGain'] = float(digital_gain)
                controls = self._manual_exposure_with_awb(controls)
            else:
                controls = self.BRIGHTNESS_MODES.get(
                    brightness_mode, self.BRIGHTNESS_MODES['normal']
                ).copy()
                if digital_gain is not None:
                    controls['DigitalGain'] = float(digital_gain)
                controls = self._manual_exposure_with_awb(controls)

            if not for_stream:
                self._set_controls_resilient(controls)
                # Auto AE needs a moment to settle on first live-preview frame.
                time.sleep(0.08 if live_preview and not manual_exp else 0.05)

            # Picamera2 stream names "RGB888"/"RGB161616" use BGR-ordered memory; normalize to RGB uint8.
            image = self._isp_buffer_to_rgb_u8(self.camera.capture_array())
            # ISP buffers often include horizontal stride (e.g. 1472 cols for 1456 px); crop to active pixels.
            tw, th = self.resolution
            if image.shape[0] >= th and image.shape[1] >= tw:
                if image.shape[0] != th or image.shape[1] != tw:
                    image = image[:th, :tw, :]
            elif not self._logged_capture_size_mismatch:
                logger.warning(
                    "Capture size %sx%s smaller than configured %sx%s",
                    image.shape[1],
                    image.shape[0],
                    tw,
                    th,
                )
                self._logged_capture_size_mismatch = True

            if not for_stream:
                if live_preview and not manual_exp:
                    logger.info("IMX296 captured %s | live_preview auto Ae/Awb", image.shape)
                else:
                    logger.info(
                        "IMX296 captured %s | mode=%s exp=%sµs gain=%s",
                        image.shape,
                        brightness_mode,
                        controls.get("ExposureTime", "?"),
                        controls.get("AnalogueGain", "?"),
                    )
            return image

        except Exception as e:
            now = time.monotonic()
            if now >= self._capture_fail_log_next:
                logger.error("Capture failed: %s", e)
                # Live stream polls quickly; avoid filling logs with identical OpenCV errors.
                self._capture_fail_log_next = now + 2.0
            return None
    
    def get_camera_info(self) -> Dict:
        """
        Return hardware information about the connected camera.

        For Picamera2 sensors the model and pixel-array size are read directly
        from the libcamera camera properties.  Returns a simulated info dict
        when no real camera is available.
        """
        if self.camera and PICAMERA2_AVAILABLE:
            try:
                props = self.camera.camera_properties
                model = props.get('Model', 'Unknown')
                native_w, native_h = props.get('PixelArraySize', self.resolution)
                return {
                    'model': model,
                    'sensor': model,
                    'native_resolution': f'{native_w}×{native_h}',
                    'output_resolution': f'{self.resolution[0]}×{self.resolution[1]}',
                    'interface': 'CSI (Picamera2 / libcamera)',
                    'format': 'SRGGB10_CSI2P (10-bit Bayer RAW)',
                    'isp_output': self.isp_effective_format,
                    'isp_output_bits': 16 if '161616' in self.isp_effective_format else 8,
                    'max_fps': 60,
                    'sensor_type': 'Global Shutter',
                    'bit_depth': 10,
                    'focus_type': 'Fixed (no AF)',
                    'device': f'camera_num={self.camera_device}',
                    'simulated': False,
                }
            except Exception as e:
                logger.warning("Could not read camera properties: %s", e)

        return {
            'model': 'Simulated / unavailable CSI',
            'sensor': 'simulated',
            'native_resolution': f'{self.resolution[0]}×{self.resolution[1]}',
            'output_resolution': f'{self.resolution[0]}×{self.resolution[1]}',
            'interface': 'None (Picamera2 missing or CSI init failed)',
            'format': 'RGB888',
            'isp_output': self.isp_effective_format,
            'isp_output_bits': 16 if '161616' in self.isp_effective_format else 8,
            'max_fps': 30,
            'sensor_type': 'Simulated',
            'bit_depth': 8,
            'focus_type': 'N/A',
            'device': f'camera_num={self.camera_device}',
            'simulated': True,
        }

    def _generate_test_pattern(self) -> np.ndarray:
        """Generate test pattern image for development."""
        image = np.zeros((self.resolution[1], self.resolution[0], 3), dtype=np.uint8)

        # Checkerboard background
        square_size = 40
        for i in range(0, self.resolution[1], square_size):
            for j in range(0, self.resolution[0], square_size):
                if ((i // square_size) + (j // square_size)) % 2 == 0:
                    image[i:i+square_size, j:j+square_size] = [200, 200, 200]

        cx, cy = self.resolution[0] // 2, self.resolution[1] // 2
        cv2.circle(image, (cx, cy), 50, (255, 0, 0), -1)
        cv2.rectangle(image, (cx - 110, cy - 110), (cx - 10, cy - 10), (0, 255, 0), -1)

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(image, timestamp, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.putText(image, "IMX296 TEST PATTERN", (10, self.resolution[1] - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        # OpenCV draws in BGR; capture_image() contract is RGB for downstream JPEG path
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    def auto_optimize_focus(
        self,
        before_capture: Optional[Callable[[], None]] = None,
        after_capture: Optional[Callable[[], None]] = None,
    ) -> Tuple[int, float]:
        """
        For fixed-focus sensors (IMX296) focus is always optimal.
        Capture one frame and return sharpness at the fixed value 50.

        Returns:
            Tuple of (focus_value=50, sharpness_score)
        """
        logger.info("IMX296 is fixed-focus — measuring sharpness at default position.")
        if before_capture:
            before_capture()
        try:
            image = self.capture_image()
        finally:
            if after_capture:
                after_capture()
        if image is not None:
            sharpness = self._calculate_sharpness(image)
            logger.info(f"Fixed-focus sharpness score: {sharpness:.2f}")
            return 50, sharpness
        return 50, 0.0
    
    def auto_optimize_brightness(
        self,
        before_capture: Optional[Callable[[], None]] = None,
        after_capture: Optional[Callable[[], None]] = None,
    ) -> Tuple[str, Dict[str, float]]:
        """
        Test all brightness modes and return best.
        
        Returns:
            Tuple of (optimal_mode, scores_dict)
        """
        logger.info("Starting brightness optimization...")
        
        scores = {}
        best_mode = 'normal'
        best_score = 0.0
        
        for mode in self.BRIGHTNESS_MODES.keys():
            if before_capture:
                before_capture()
            try:
                image = self.capture_image(brightness_mode=mode)
            finally:
                if after_capture:
                    after_capture()
            if image is not None:
                quality = self.validate_image_quality(image)
                score = quality['score']
                scores[mode] = score
                
                logger.debug(f"Mode {mode}: score = {score:.2f}")
                
                if score > best_score:
                    best_score = score
                    best_mode = mode
        
        logger.info(f"Optimal brightness mode: {best_mode} (score: {best_score:.2f})")
        return best_mode, scores
    
    def validate_image_quality(self, image: np.ndarray) -> Dict[str, float]:
        """
        Multi-signal image quality (exposure, luminance comfort, contrast, detail, entropy).

        See ``src.utils.image_quality.analyze_image_quality_rgb`` for metric definitions.
        """
        if image is None or image.size == 0:
            return {
                'brightness': 0.0,
                'luminance_median': 0.0,
                'contrast': 0.0,
                'sharpness': 0.0,
                'sharpness_index': 0.0,
                'exposure': 0.0,
                'information': 0.0,
                'score': 0.0,
            }

        from src.utils.image_quality import analyze_image_quality_rgb

        q = analyze_image_quality_rgb(image)
        return {k: float(v) for k, v in q.items()}
    
    def validate_image_consistency(
        self,
        master_image: np.ndarray,
        captured_image: np.ndarray
    ) -> Dict[str, any]:
        """
        Validate that master image and captured image have consistent quality
        for accurate matching. This is critical for template matching algorithms.
        
        Checks:
        - Resolution consistency
        - Brightness difference (should be within 20%)
        - Sharpness difference (should be within 30%)
        - Overall quality consistency
        
        Args:
            master_image: Master reference image (RGB)
            captured_image: Captured test image (RGB)
            
        Returns:
            Dictionary with:
            - consistent: bool (True if images are consistent)
            - issues: List of consistency issues found
            - master_quality: Quality metrics of master image
            - captured_quality: Quality metrics of captured image
            - warnings: List of warnings (non-critical issues)
        """
        issues = []
        warnings = []
        
        # Check resolution consistency
        if master_image.shape != captured_image.shape:
            issues.append(
                f"Resolution mismatch: Master {master_image.shape} vs "
                f"Captured {captured_image.shape}"
            )
        
        # Get quality metrics for both images
        master_quality = self.validate_image_quality(master_image)
        captured_quality = self.validate_image_quality(captured_image)
        
        # Brightness consistency — compare shadow-robust medians, fall back to mean.
        med_m = float(master_quality.get("luminance_median", master_quality["brightness"]))
        med_c = float(captured_quality.get("luminance_median", captured_quality["brightness"]))
        brightness_diff = abs(med_m - med_c)
        brightness_threshold = max(20.0, 0.24 * max(med_m, med_c, 1.0))
        if brightness_diff > brightness_threshold:
            warnings.append(
                f"Typical-light mismatch: Δ={brightness_diff:.1f} "
                f"(master median gray ≈{med_m:.0f}, capture ≈{med_c:.0f})"
            )
        
        # Check sharpness consistency (within 30%)
        sharpness_ratio = (
            captured_quality['sharpness'] / master_quality['sharpness']
            if master_quality['sharpness'] > 0 else 1.0
        )
        if sharpness_ratio < 0.7 or sharpness_ratio > 1.3:
            warnings.append(
                f"Sharpness inconsistency: Captured image is "
                f"{sharpness_ratio*100:.0f}% of master sharpness"
            )
        
        # Check overall quality scores (both should be reasonable)
        if master_quality['score'] < 50:
            warnings.append(
                f"Master image quality is low: {master_quality['score']:.1f}/100"
            )
        if captured_quality['score'] < 50:
            warnings.append(
                f"Captured image quality is low: {captured_quality['score']:.1f}/100"
            )
        
        # Determine if consistent (no critical issues)
        consistent = len(issues) == 0
        
        return {
            'consistent': consistent,
            'issues': issues,
            'warnings': warnings,
            'master_quality': master_quality,
            'captured_quality': captured_quality,
            'recommendation': (
                'Images are consistent for matching' if consistent and not warnings
                else 'Check warnings - may affect matching accuracy' if consistent
                else 'Critical issues found - matching may fail'
            )
        }
    
    def _calculate_sharpness(self, image: np.ndarray) -> float:
        """
        Laplacian variance on resolution-normalized grayscale (see ``image_quality`` module).

        Comparable across sensor resolutions; used by auto-optimize and consistency checks.
        """
        from src.utils import image_quality as iq

        try:
            g = iq.prepare_gray_for_metrics(image)
            return iq.laplacian_variance(g)
        except ValueError:
            return 0.0
    
    def start_preview(self):
        """Start live camera preview (for streaming)."""
        self.is_previewing = True
        logger.info("Preview started")
    
    def stop_preview(self):
        """Stop live camera preview."""
        self.is_previewing = False
        logger.info("Preview stopped")
    
    def get_preview_frame(self) -> Optional[np.ndarray]:
        """
        Get a single preview frame.
        
        Returns:
            Preview frame or None
        """
        if not self.is_previewing:
            return None
        return self.capture_image()
    
    def close(self):
        """Close Picamera2 CSI camera."""
        try:
            if self.camera:
                self.camera.stop()
                self.camera.close()
                logger.info("CSI camera closed")
        except Exception as e:
            logger.error("Error closing camera: %s", e)
    
    def __del__(self):
        """Cleanup on deletion."""
        self.close()

