"""LED Lighting Controller for Inspection"""

from typing import Optional
from src.utils.logger import get_logger

logger = get_logger('led')

# Check if running on Raspberry Pi
try:
    import RPi.GPIO as GPIO
    RASPBERRY_PI = True
except ImportError:
    RASPBERRY_PI = False
    logger.warning("RPi.GPIO not available. Using simulated LED control.")


class LEDController:
    """
    Control inspection lighting using PWM.
    
    This controller manages LED brightness for optimal imaging conditions.
    Can be connected to GPIO pin with PWM support.
    """
    
    def __init__(self, led_pin: int = 12, pwm_frequency: int = 1000):
        """
        Initialize LED controller.
        
        Args:
            led_pin: GPIO pin for LED control (BCM numbering)
            pwm_frequency: PWM frequency in Hz
        """
        self.led_pin = led_pin
        self.pwm_frequency = pwm_frequency
        self.pwm = None
        self.current_brightness = 0
        
        self._setup_led()
    
    def _setup_led(self):
        """Initialize LED PWM control."""
        if RASPBERRY_PI:
            try:
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(self.led_pin, GPIO.OUT)
                
                # Create PWM instance
                self.pwm = GPIO.PWM(self.led_pin, self.pwm_frequency)
                self.pwm.start(0)  # Start with 0% duty cycle
                
                logger.info(f"LED controller initialized on pin {self.led_pin}")
            except Exception as e:
                logger.error(f"Failed to initialize LED controller: {e}")
        else:
            logger.info("Using simulated LED control (development mode)")
    
    def set_brightness(self, level: int):
        """
        Set LED brightness 0-100%.
        
        Args:
            level: Brightness level (0=off, 100=full brightness)
        """
        # Clamp level to valid range
        level = max(0, min(100, level))
        
        try:
            if RASPBERRY_PI and self.pwm:
                self.pwm.ChangeDutyCycle(level)
            else:
                logger.debug(f"[SIM] LED brightness set to {level}%")
            
            self.current_brightness = level
            logger.debug(f"LED brightness: {level}%")
            
        except Exception as e:
            logger.error(f"Failed to set LED brightness: {e}")
    
    def turn_on(self, brightness: int = 100):
        """
        Turn LED on at specified brightness.
        
        Args:
            brightness: Brightness level (0-100)
        """
        self.set_brightness(brightness)
    
    def turn_off(self):
        """Turn LED off."""
        self.set_brightness(0)
    
    def get_brightness(self) -> int:
        """
        Get current brightness level.
        
        Returns:
            Current brightness (0-100)
        """
        return self.current_brightness
    
    def fade_to(self, target_brightness: int, duration_ms: int = 500, steps: int = 50):
        """
        Fade to target brightness over specified duration.
        
        Args:
            target_brightness: Target brightness level (0-100)
            duration_ms: Fade duration in milliseconds
            steps: Number of steps in the fade
        """
        import time
        
        start_brightness = self.current_brightness
        step_delay = duration_ms / 1000.0 / steps
        brightness_step = (target_brightness - start_brightness) / steps
        
        for i in range(steps):
            new_brightness = int(start_brightness + brightness_step * (i + 1))
            self.set_brightness(new_brightness)
            time.sleep(step_delay)
        
        # Ensure final brightness is exact
        self.set_brightness(target_brightness)
    
    def cleanup(self):
        """Cleanup LED resources."""
        try:
            self.turn_off()
            
            if RASPBERRY_PI and self.pwm:
                self.pwm.stop()
                GPIO.cleanup(self.led_pin)
                logger.info("LED controller cleanup complete")
        except Exception as e:
            logger.error(f"Error during LED cleanup: {e}")
    
    def __del__(self):
        """Cleanup on deletion."""
        self.cleanup()

