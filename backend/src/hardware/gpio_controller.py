"""GPIO Output Controller for Raspberry Pi"""

from typing import Dict, List
import time
from src.utils.logger import get_logger

logger = get_logger('gpio')

# Check if running on Raspberry Pi
try:
    import RPi.GPIO as GPIO
    RASPBERRY_PI = True
except ImportError:
    RASPBERRY_PI = False
    logger.warning("RPi.GPIO not available. Using simulated GPIO for development.")


class GPIOController:
    """
    Complete GPIO output controller with:
    - 8 output channels (pins 17, 18, 27, 22, 23, 24, 25, 8)
    - Set output states (HIGH/LOW)
    - Pulse outputs with duration
    - Output state monitoring
    - Error handling
    
    Output Mapping:
    - OUT1 (Pin 17): BUSY signal
    - OUT2 (Pin 18): OK signal
    - OUT3 (Pin 27): NG signal
    - OUT4-8 (Pins 22, 23, 24, 25, 8): Configurable outputs
    """
    
    DEFAULT_OUTPUT_PINS = [17, 18, 27, 22, 23, 24, 25, 8]
    
    def __init__(self, output_pins: List[int] = None):
        """
        Initialize GPIO controller.
        
        Args:
            output_pins: List of GPIO pin numbers (BCM mode)
        """
        self.output_pins = output_pins or self.DEFAULT_OUTPUT_PINS
        self.output_states = {i+1: False for i in range(len(self.output_pins))}
        self._simulated_states = {i+1: False for i in range(len(self.output_pins))}
        
        self._setup_gpio()
    
    def _setup_gpio(self):
        """Initialize GPIO pins."""
        if RASPBERRY_PI:
            try:
                # Use BCM pin numbering
                GPIO.setmode(GPIO.BCM)
                
                # Disable warnings
                GPIO.setwarnings(False)
                
                # Setup all pins as outputs (initially LOW)
                for pin in self.output_pins:
                    GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
                
                logger.info(f"GPIO initialized: {len(self.output_pins)} outputs on pins {self.output_pins}")
            except Exception as e:
                logger.error(f"Failed to initialize GPIO: {e}")
        else:
            logger.info("Using simulated GPIO (development mode)")
    
    def set_output(self, output_number: int, state: bool):
        """
        Set single output HIGH or LOW.
        
        Args:
            output_number: Output number (1-8)
            state: True for HIGH, False for LOW
            
        Raises:
            ValueError: If output_number is invalid
        """
        if output_number < 1 or output_number > len(self.output_pins):
            raise ValueError(f"Invalid output number: {output_number}. Must be 1-{len(self.output_pins)}")
        
        pin = self.output_pins[output_number - 1]
        
        try:
            if RASPBERRY_PI:
                GPIO.output(pin, GPIO.HIGH if state else GPIO.LOW)
            else:
                self._simulated_states[output_number] = state
                logger.debug(f"[SIM] OUT{output_number} (Pin {pin}): {'HIGH' if state else 'LOW'}")
            
            self.output_states[output_number] = state
            logger.debug(f"OUT{output_number} set to {'HIGH' if state else 'LOW'}")
            
        except Exception as e:
            logger.error(f"Failed to set OUT{output_number}: {e}")
    
    def set_outputs(self, output_states: Dict[int, bool]):
        """
        Set multiple outputs from dictionary.
        
        Args:
            output_states: Dictionary mapping output_number to state
            
        Example:
            set_outputs({1: True, 2: False, 4: True})
        """
        for output_number, state in output_states.items():
            self.set_output(output_number, state)
    
    def set_outputs_by_name(self, output_assignments: Dict[str, bool]):
        """
        Set outputs by name (OUT1, OUT2, etc.).
        
        Args:
            output_assignments: Dictionary mapping output name to state
            
        Example:
            set_outputs_by_name({'OUT1': True, 'OUT2': False})
        """
        for name, state in output_assignments.items():
            if name.startswith('OUT'):
                try:
                    output_number = int(name[3:])
                    self.set_output(output_number, state)
                except (ValueError, IndexError):
                    logger.warning(f"Invalid output name: {name}")
    
    def pulse_output(self, output_number: int, duration_ms: int):
        """
        Pulse output for specified duration.
        
        Args:
            output_number: Output number (1-8)
            duration_ms: Pulse duration in milliseconds
        """
        self.set_output(output_number, True)
        time.sleep(duration_ms / 1000.0)
        self.set_output(output_number, False)
        logger.debug(f"OUT{output_number} pulsed for {duration_ms}ms")
    
    def get_output_state(self, output_number: int) -> bool:
        """
        Get current state of an output.
        
        Args:
            output_number: Output number (1-8)
            
        Returns:
            Current state (True=HIGH, False=LOW)
        """
        return self.output_states.get(output_number, False)
    
    def get_all_states(self) -> Dict[int, bool]:
        """
        Get current states of all outputs.
        
        Returns:
            Dictionary mapping output_number to state
        """
        return self.output_states.copy()
    
    def reset_all(self):
        """Set all outputs to LOW."""
        for i in range(1, len(self.output_pins) + 1):
            self.set_output(i, False)
        logger.info("All outputs reset to LOW")
    
    def test_sequence(self):
        """Run a test sequence to verify all outputs."""
        logger.info("Running GPIO test sequence...")
        
        for i in range(1, len(self.output_pins) + 1):
            logger.info(f"Testing OUT{i}...")
            self.pulse_output(i, 500)
            time.sleep(0.2)
        
        logger.info("GPIO test sequence complete")
    
    def cleanup(self):
        """Release only this controller's output pins (BCM).

        Avoids GPIO.cleanup() without arguments so other subsystems (e.g. lighting
        on different pins) keep working until they close themselves.
        """
        try:
            self.reset_all()

            if RASPBERRY_PI:
                for pin in self.output_pins:
                    try:
                        GPIO.cleanup(pin)
                    except Exception:
                        pass
                logger.info("GPIO output pins released: %s", self.output_pins)
        except Exception as e:
            logger.error(f"Error during GPIO cleanup: {e}")
    
    def __del__(self):
        """Cleanup on deletion."""
        self.cleanup()


class OutputManager:
    """
    High-level output manager for inspection results.
    Handles the standard output assignments:
    - OUT1: BUSY (active during inspection)
    - OUT2: OK (triggered when inspection passes)
    - OUT3: NG (triggered when inspection fails)
    - OUT4-8: Configurable per program
    """
    
    def __init__(self, gpio_controller: GPIOController):
        """
        Initialize output manager.
        
        Args:
            gpio_controller: GPIOController instance
        """
        self.gpio = gpio_controller
    
    def set_busy(self, busy: bool):
        """Set BUSY output (OUT1)."""
        self.gpio.set_output(1, busy)
    
    def trigger_ok(self, duration_ms: int = 100):
        """Trigger OK output (OUT2) for specified duration."""
        self.gpio.pulse_output(2, duration_ms)
    
    def trigger_ng(self, duration_ms: int = 100):
        """Trigger NG output (OUT3) for specified duration."""
        self.gpio.pulse_output(3, duration_ms)
    
    def set_custom_outputs(self, assignments: Dict[str, bool]):
        """
        Set custom outputs (OUT4-8) based on program configuration.
        
        Args:
            assignments: Dictionary like {'OUT4': 'OK', 'OUT5': 'NG', ...}
                        Maps output names to conditions
        """
        output_states = {}
        
        for output_name, condition in assignments.items():
            if output_name.startswith('OUT'):
                try:
                    output_number = int(output_name[3:])
                    # Condition can be 'OK', 'NG', 'Always ON', 'Always OFF', etc.
                    # This is handled by the inspection engine
                    if output_number >= 4:  # Only custom outputs
                        output_states[output_number] = condition
                except (ValueError, IndexError):
                    logger.warning(f"Invalid output name: {output_name}")
        
        return output_states
    
    def apply_inspection_result(
        self,
        status: str,
        custom_output_config: Dict[str, str],
        pulse_duration_ms: int = 100
    ):
        """
        Apply outputs based on inspection result.
        
        Args:
            status: Inspection status ('OK' or 'NG')
            custom_output_config: Dict mapping output names to conditions
                                 e.g., {'OUT4': 'OK', 'OUT5': 'NG', 'OUT6': 'Always ON'}
            pulse_duration_ms: Duration for OK/NG pulses
        """
        # Trigger standard outputs
        if status == 'OK':
            self.trigger_ok(pulse_duration_ms)
        else:
            self.trigger_ng(pulse_duration_ms)
        
        # Set custom outputs based on configuration
        for output_name, condition in custom_output_config.items():
            if output_name.startswith('OUT') and output_name not in ['OUT1', 'OUT2', 'OUT3']:
                try:
                    output_number = int(output_name[3:])
                    
                    # Determine output state based on condition
                    if condition == 'Always ON':
                        state = True
                    elif condition == 'Always OFF':
                        state = False
                    elif condition == 'OK':
                        state = (status == 'OK')
                    elif condition == 'NG':
                        state = (status == 'NG')
                    else:
                        state = False
                    
                    self.gpio.set_output(output_number, state)
                    
                except (ValueError, IndexError):
                    logger.warning(f"Invalid output configuration: {output_name}")

