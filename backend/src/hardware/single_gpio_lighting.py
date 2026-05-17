"""Single-pin inspection lighting (one BCM GPIO).

Typical wiring: Pi GPIO → MOSFET gate driver → LED load (e.g. several SMD LEDs on
one switched channel). PWM mode dims 0–100%; digital mode is on/off. The REST API
still uses r,g,b; for monochrome loads use equal R,G,B — intensity is max(r,g,b).
"""

from __future__ import annotations

import threading
import time
from typing import List, Optional, Sequence, Tuple

from src.utils.logger import get_logger

logger = get_logger("lighting")

try:
    import RPi.GPIO as GPIO

    _HAS_GPIO = True
except ImportError:
    GPIO = None  # type: ignore
    _HAS_GPIO = False
    logger.warning("RPi.GPIO not available — single-GPIO lighting simulated.")


def rgb_to_brightness_percent(r: int, g: int, b: int) -> int:
    """Map RGB to 0–100; single-channel hardware has no per-channel colour."""
    m = max(int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF)
    return int(round(m / 255.0 * 100))


class SingleGpioLighting:
    """
    One output pin for illumination.

    Compatible with the same fill/show/off/set_from_sequence flow as P9813Lighting
    so inspection and API code paths stay unified.
    """

    def __init__(
        self,
        pin: int,
        *,
        pwm: bool = True,
        pwm_frequency: int = 1000,
        active_high: bool = True,
        solid_at_full: bool = True,
        enabled: bool = True,
    ):
        self.pin = int(pin)
        self.pwm = bool(pwm)
        self.pwm_frequency = max(1, int(pwm_frequency))
        self.active_high = bool(active_high)
        self.solid_at_full = bool(solid_at_full)
        self.enabled = bool(enabled) and _HAS_GPIO
        self._pwm = None  # type: ignore
        self._brightness = 0
        self._pixels: List[Tuple[int, int, int]] = [(0, 0, 0)]
        self._lock = threading.Lock()
        self._initialized = False

        if self.enabled:
            try:
                GPIO.setmode(GPIO.BCM)
                GPIO.setwarnings(False)
                GPIO.setup(self.pin, GPIO.OUT)
                if self.pwm:
                    self._pwm = GPIO.PWM(self.pin, self.pwm_frequency)
                    self._pwm.start(0)
                else:
                    GPIO.output(self.pin, self._idle_level())
                self._initialized = True
                logger.info(
                    "Single-GPIO lighting: BCM %d, mode=%s, active_high=%s solid_at_full=%s",
                    self.pin,
                    "PWM" if self.pwm else "DIGITAL",
                    self.active_high,
                    self.solid_at_full,
                )
            except Exception as e:
                logger.error("Single-GPIO lighting init failed: %s", e)
                self.enabled = False
                self._initialized = False
        else:
            logger.info("Single-GPIO lighting disabled or simulated (no RPi.GPIO).")

    def is_ready(self) -> bool:
        return bool(self._initialized)

    def _idle_level(self) -> int:
        """Pin level when output is 'off' (0% brightness)."""
        return GPIO.LOW if self.active_high else GPIO.HIGH

    def _on_level(self) -> int:
        """Pin level when MOSFET / load is fully on."""
        return GPIO.HIGH if self.active_high else GPIO.LOW

    def _pwm_duty_arg(self, level: int) -> int:
        """Duty cycle 0–100 as expected by RPi.GPIO.PWM.ChangeDutyCycle."""
        level = max(0, min(100, int(level)))
        return level if self.active_high else (100 - level)

    def _ensure_pwm(self) -> None:
        if self._pwm is not None:
            return
        GPIO.setup(self.pin, GPIO.OUT)
        self._pwm = GPIO.PWM(self.pin, self.pwm_frequency)
        self._pwm.start(0)

    def _apply_brightness(self, level: int) -> None:
        level = max(0, min(100, int(level)))
        self._brightness = level
        if not self._initialized:
            logger.debug("[SIM] single-GPIO brightness=%d%%", level)
            return
        try:
            if not self.pwm:
                GPIO.output(self.pin, self._on_level() if level > 0 else self._idle_level())
                return

            if self.solid_at_full and level >= 100:
                # RPi.GPIO soft-PWM at 100% can be weaker than a solid level; drive the pin hard.
                if self._pwm is not None:
                    self._pwm.stop()
                    self._pwm = None
                GPIO.setup(self.pin, GPIO.OUT)
                GPIO.output(self.pin, self._on_level())
                return

            self._ensure_pwm()
            d = self._pwm_duty_arg(level)
            self._pwm.ChangeDutyCycle(d)
        except Exception as e:
            logger.error("single-GPIO apply failed: %s", e)

    def fill(self, r: int, g: int, b: int) -> None:
        rr, gg, bb = int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF
        with self._lock:
            self._pixels = [(rr, gg, bb)]
            self._apply_brightness(rgb_to_brightness_percent(rr, gg, bb))

    def show(self) -> None:
        """Sync output (PWM is already live; included for API parity with P9813)."""
        with self._lock:
            if not self._pixels:
                self._apply_brightness(0)
                return
            r, g, b = self._pixels[0]
            self._apply_brightness(rgb_to_brightness_percent(r, g, b))

    def off(self) -> None:
        with self._lock:
            self._pixels = [(0, 0, 0)]
            self._apply_brightness(0)

    def set_pixel(self, index: int, r: int, g: int, b: int) -> None:
        if index != 0:
            raise ValueError("Single-GPIO lighting supports only one logical channel (index 0)")
        self.fill(r, g, b)

    def set_from_sequence(self, pixels: Sequence[Tuple[int, int, int]]) -> None:
        if not pixels:
            self.off()
            return
        p = pixels[0]
        self.fill(p[0], p[1], p[2])

    def close(self) -> None:
        """Turn off and release the pin (not global GPIO.cleanup)."""
        with self._lock:
            if not self._initialized:
                return
            try:
                if self.pwm and self._pwm is not None:
                    self._pwm.ChangeDutyCycle(self._pwm_duty_arg(0))
                    time.sleep(0.01)
                    self._pwm.stop()
                    self._pwm = None
                elif self.pwm and self._pwm is None:
                    # Was at full brightness using solid GPIO, not PWM.
                    pass
                GPIO.setup(self.pin, GPIO.OUT)
                GPIO.output(self.pin, self._idle_level())
                GPIO.cleanup(self.pin)
            except Exception as e:
                logger.warning("Single-GPIO shutdown: %s", e)
            self._initialized = False

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
