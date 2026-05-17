"""P9813 chainable RGB LED strip driver (2-wire DATA + CLK), BCM GPIO bit-bang.

French modules labeled "P9813 Module de pilotage de bande LED RVB" use the same
protocol as Seeed Grove Chainable LED / InnoMaker-style drivers: clocked serial,
32-bit start frame, then 32 bits per LED (ChainableLED / pjpmarques encoding).

Wiring (example — set pins in config.yaml, avoid overlap with gpio.outputs):
  - CLK → clock_pin (BCM)
  - DATA / DI → data_pin (BCM)
  - VCC / GND per module (often 5 V strip: use level shifter if Pi GPIO is 3.3 V)
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.hardware.single_gpio_lighting import SingleGpioLighting

from src.utils.logger import get_logger

logger = get_logger("p9813")

try:
    import RPi.GPIO as GPIO

    _HAS_GPIO = True
except ImportError:
    GPIO = None  # type: ignore
    _HAS_GPIO = False
    logger.warning("RPi.GPIO not available — P9813 lighting simulated.")


def _encode_pixel(r: int, g: int, b: int) -> bytes:
    """Encode one LED for P9813 (ChainableLED library format)."""
    r = int(r) & 0xFF
    g = int(g) & 0xFF
    b = int(b) & 0xFF
    byte0 = 0xC0 | (((~b) & 0xFF) >> 2) & 0x3F
    return bytes([byte0 & 0xFF, (~g) & 0xFF, (~r) & 0xFF, 0xFF])


class P9813Lighting:
    """
    Drive one or more P9813 LEDs in series (data propagates through the chain).

    Not thread-safe across multiple instances sharing the same pins; use one
    instance per chain.
    """

    def __init__(
        self,
        clock_pin: int,
        data_pin: int,
        num_leds: int = 1,
        *,
        enabled: bool = True,
        clock_period_s: float = 1.2e-5,
    ):
        self.clock_pin = int(clock_pin)
        self.data_pin = int(data_pin)
        self.num_leds = max(1, int(num_leds))
        self.enabled = bool(enabled) and _HAS_GPIO
        self._clock_period_s = max(1e-6, float(clock_period_s))
        self._pixels: List[Tuple[int, int, int]] = [(0, 0, 0)] * self.num_leds
        self._lock = threading.Lock()
        self._initialized = False

        if self.enabled:
            try:
                GPIO.setmode(GPIO.BCM)
                GPIO.setwarnings(False)
                GPIO.setup(self.clock_pin, GPIO.OUT, initial=GPIO.LOW)
                GPIO.setup(self.data_pin, GPIO.OUT, initial=GPIO.LOW)
                self._initialized = True
                logger.info(
                    "P9813 lighting: CLK=GPIO%d DATA=GPIO%d leds=%d",
                    self.clock_pin,
                    self.data_pin,
                    self.num_leds,
                )
            except Exception as e:
                logger.error("P9813 GPIO init failed: %s", e)
                self.enabled = False
                self._initialized = False
        else:
            logger.info("P9813 lighting disabled or simulated (no RPi.GPIO).")

    def is_ready(self) -> bool:
        return bool(self._initialized)

    def _half_period(self) -> None:
        time.sleep(self._clock_period_s * 0.5)

    def _clk_pulse(self) -> None:
        if not self._initialized:
            return
        GPIO.output(self.clock_pin, GPIO.HIGH)
        self._half_period()
        GPIO.output(self.clock_pin, GPIO.LOW)
        self._half_period()

    def _send_byte(self, value: int) -> None:
        v = int(value) & 0xFF
        for i in range(7, -1, -1):
            bit = (v >> i) & 1
            GPIO.output(self.data_pin, GPIO.HIGH if bit else GPIO.LOW)
            self._clk_pulse()

    def show(self) -> None:
        """Latch current pixel buffer to the chain."""
        with self._lock:
            if not self._initialized:
                logger.debug("[SIM] P9813 show skipped")
                return
            for _ in range(4):
                self._send_byte(0x00)
            for r, g, b in self._pixels:
                for byte in _encode_pixel(r, g, b):
                    self._send_byte(byte)
            for _ in range(4):
                self._send_byte(0x00)

    def set_pixel(self, index: int, r: int, g: int, b: int) -> None:
        if index < 0 or index >= self.num_leds:
            raise ValueError(f"LED index {index} out of range 0..{self.num_leds - 1}")
        px = list(self._pixels)
        px[index] = (int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF)
        self._pixels = px

    def fill(self, r: int, g: int, b: int) -> None:
        rr, gg, bb = int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF
        self._pixels = [(rr, gg, bb) for _ in range(self.num_leds)]

    def off(self) -> None:
        self.fill(0, 0, 0)
        self.show()

    def set_from_sequence(self, pixels: Sequence[Tuple[int, int, int]]) -> None:
        """Set first N LEDs from (r,g,b) tuples; remaining unchanged."""
        for i, t in enumerate(pixels):
            if i >= self.num_leds:
                break
            self.set_pixel(i, t[0], t[1], t[2])

    def close(self) -> None:
        """Turn off and release CLK/DATA pins only (not global GPIO.cleanup)."""
        with self._lock:
            if not self._initialized:
                return
            try:
                self.fill(0, 0, 0)
                for _ in range(4):
                    self._send_byte(0x00)
                for r, g, b in self._pixels:
                    for byte in _encode_pixel(r, g, b):
                        self._send_byte(byte)
                for _ in range(4):
                    self._send_byte(0x00)
            except Exception as e:
                logger.warning("P9813 shutdown sequence: %s", e)
            try:
                GPIO.cleanup(self.clock_pin)
                GPIO.cleanup(self.data_pin)
            except Exception as e:
                logger.warning("P9813 pin cleanup: %s", e)
            self._initialized = False

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def lighting_settings_from_yaml(lighting_cfg: Optional[dict]) -> dict:
    """Defaults for API / inspection behaviour (not P9813 pin config)."""
    if not lighting_cfg:
        return {
            "during_capture": True,
            "settle_ms": 2.0,
            "off_after_capture": True,
            "use_for_api_capture": True,
            "default_rgb": [255, 255, 255],
        }
    return {
        "during_capture": bool(lighting_cfg.get("during_capture", True)),
        "settle_ms": float(lighting_cfg.get("settle_ms", 2.0)),
        "off_after_capture": bool(lighting_cfg.get("off_after_capture", True)),
        "use_for_api_capture": bool(lighting_cfg.get("use_for_api_capture", True)),
        "default_rgb": [int(x) & 0xFF for x in (lighting_cfg.get("default_rgb") or [255, 255, 255])][:3],
    }


def resolve_lighting_runtime(global_lighting_cfg: Optional[dict], program_config: dict) -> dict:
    """Merge global YAML `lighting` with optional program `config.lighting` overrides."""
    base = lighting_settings_from_yaml(global_lighting_cfg or {})
    pl = (program_config or {}).get("lighting") or {}
    out = dict(base)
    if "during_capture" in pl:
        out["during_capture"] = bool(pl["during_capture"])
    if "settle_ms" in pl:
        out["settle_ms"] = float(pl["settle_ms"])
    if "off_after_capture" in pl:
        out["off_after_capture"] = bool(pl["off_after_capture"])
    if pl.get("rgb") is not None:
        out["rgb"] = [int(x) & 0xFF for x in pl["rgb"]][:3]
    else:
        out["rgb"] = list(out["default_rgb"])
    return out


def describe_lighting_controller(lc: Optional[Any]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Return (driver_name, pin_info) for /api/lighting/status."""
    if lc is None:
        return None, None
    if isinstance(lc, SingleGpioLighting):
        name = "GPIO_PWM" if lc.pwm else "GPIO_DIGITAL"
        return name, {
            "pin": lc.pin,
            "pwm": lc.pwm,
            "pwm_frequency": lc.pwm_frequency,
            "active_high": lc.active_high,
            "solid_at_full": lc.solid_at_full,
        }
    if isinstance(lc, P9813Lighting):
        return "P9813", {
            "clock": lc.clock_pin,
            "data": lc.data_pin,
            "num_leds": lc.num_leds,
        }
    return type(lc).__name__, None


def build_lighting_from_config(lighting_cfg: Optional[dict]) -> Optional[Any]:
    """
    Preferred factory: single-GPIO lighting if lighting.gpio.enabled, else optional P9813.
    """
    if not lighting_cfg:
        return None
    gpio_cfg = lighting_cfg.get("gpio") or {}
    if gpio_cfg.get("enabled", False):
        try:
            return SingleGpioLighting(
                pin=int(gpio_cfg["pin"]),
                pwm=bool(gpio_cfg.get("pwm", True)),
                pwm_frequency=int(gpio_cfg.get("pwm_frequency", 1000)),
                active_high=bool(gpio_cfg.get("active_high", True)),
                solid_at_full=bool(gpio_cfg.get("solid_at_full", True)),
                enabled=True,
            )
        except KeyError as e:
            logger.error("lighting.gpio config missing key: %s", e)
            return None
        except Exception as e:
            logger.error("lighting.gpio config error: %s", e)
            return None
    p = lighting_cfg.get("p9813") or {}
    if not p.get("enabled", False):
        return None
    try:
        return P9813Lighting(
            clock_pin=int(p["clock_pin"]),
            data_pin=int(p["data_pin"]),
            num_leds=int(p.get("num_leds", 1)),
            enabled=True,
            clock_period_s=float(p.get("clock_period_us", 12)) * 1e-6,
        )
    except KeyError as e:
        logger.error("P9813 config missing key: %s", e)
        return None
    except Exception as e:
        logger.error("P9813 config error: %s", e)
        return None


def build_p9813_from_config(lighting_cfg: Optional[dict]) -> Optional[P9813Lighting]:
    """If only P9813 is configured, returns that instance; otherwise None."""
    inst = build_lighting_from_config(lighting_cfg)
    return inst if isinstance(inst, P9813Lighting) else None
