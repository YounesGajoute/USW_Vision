#!/usr/bin/env python3
"""
Hardware Test Suite for Vision Inspection System
Tests all hardware components: camera, GPIO, and LED control
"""

import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from src.hardware.camera import CameraController
from src.hardware.gpio_controller import GPIOController
from src.hardware.led_controller import LEDController
from src.utils.logger import setup_logging, get_logger
import time

# Setup logging
setup_logging({'level': 'INFO', 'file': './logs/hardware_test.log'})
logger = get_logger('hardware_test')


def test_camera():
    """Test camera connection and capture"""
    print("\n=== Testing Camera ===")
    
    try:
        camera = CameraController()
        
        # Test basic capture
        print("Testing image capture...")
        image = camera.capture_image()
        
        if image is None:
            print("❌ FAILED: Could not capture image")
            return False
        
        print(f"✓ Image captured successfully: {image.shape}")
        
        # Test image quality validation
        print("Testing image quality validation...")
        quality = camera.validate_image_quality(image)
        
        print(f"  Brightness: {quality['brightness']:.1f}")
        print(f"  Sharpness: {quality['sharpness']:.1f}")
        print(f"  Exposure: {quality['exposure']:.1f}")
        print(f"  Overall Score: {quality['score']:.1f}/100")
        
        if quality['score'] < 50:
            print("⚠️  WARNING: Image quality is low")
        else:
            print("✓ Image quality is acceptable")
        
        # Test different brightness modes
        print("\nTesting brightness modes...")
        for mode in ['normal', 'hdr', 'highgain']:
            img = camera.capture_image(brightness_mode=mode)
            if img is not None:
                print(f"  ✓ {mode.upper()}: OK")
            else:
                print(f"  ❌ {mode.upper()}: FAILED")
        
        # Cleanup
        camera.close()
        
        print("\n✓ Camera tests PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ Camera tests FAILED: {e}")
        logger.error(f"Camera test failed: {e}", exc_info=True)
        return False


def test_gpio_outputs():
    """Test all GPIO outputs"""
    print("\n=== Testing GPIO Outputs ===")
    
    try:
        gpio = GPIOController()
        
        print("Testing individual outputs...")
        
        # Test each output
        for i in range(1, 9):
            print(f"\nTesting OUT{i}...")
            
            # Set HIGH
            gpio.set_output(i, True)
            time.sleep(0.2)
            state = gpio.get_output_state(i)
            
            if state:
                print(f"  ✓ OUT{i} HIGH: OK")
            else:
                print(f"  ❌ OUT{i} HIGH: FAILED")
            
            # Set LOW
            gpio.set_output(i, False)
            time.sleep(0.2)
            state = gpio.get_output_state(i)
            
            if not state:
                print(f"  ✓ OUT{i} LOW: OK")
            else:
                print(f"  ❌ OUT{i} LOW: FAILED")
        
        # Test pulse function
        print("\nTesting pulse function...")
        for i in [1, 4, 8]:
            gpio.pulse_output(i, 100)
            print(f"  ✓ OUT{i} pulse: OK")
            time.sleep(0.2)
        
        # Reset all outputs
        print("\nResetting all outputs...")
        gpio.reset_all()
        
        # Verify all are LOW
        states = gpio.get_all_states()
        all_low = all(not state for state in states.values())
        
        if all_low:
            print("  ✓ All outputs reset: OK")
        else:
            print("  ❌ Reset failed")
        
        # Cleanup
        gpio.cleanup()
        
        print("\n✓ GPIO tests PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ GPIO tests FAILED: {e}")
        logger.error(f"GPIO test failed: {e}", exc_info=True)
        return False


def test_led_control():
    """Test LED brightness control"""
    print("\n=== Testing LED Control ===")
    
    try:
        led = LEDController()
        
        # Test brightness levels
        print("Testing brightness levels...")
        for level in [0, 25, 50, 75, 100]:
            led.set_brightness(level)
            time.sleep(0.3)
            current = led.get_brightness()
            
            if current == level:
                print(f"  ✓ Brightness {level}%: OK")
            else:
                print(f"  ❌ Brightness {level}%: Expected {level}, got {current}")
        
        # Test fade
        print("\nTesting fade effect...")
        led.fade_to(100, duration_ms=500)
        time.sleep(0.6)
        led.fade_to(0, duration_ms=500)
        time.sleep(0.6)
        print("  ✓ Fade: OK")
        
        # Cleanup
        led.cleanup()
        
        print("\n✓ LED control tests PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ LED control tests FAILED: {e}")
        logger.error(f"LED test failed: {e}", exc_info=True)
        return False


def main():
    """Main test suite"""
    print("=" * 60)
    print("  VISION INSPECTION SYSTEM - HARDWARE TEST SUITE")
    print("=" * 60)
    
    results = {
        'camera': test_camera(),
        'gpio': test_gpio_outputs(),
        'led': test_led_control(),
    }
    
    print("\n" + "=" * 60)
    print("  TEST SUMMARY")
    print("=" * 60)
    
    for component, passed in results.items():
        status = "✓ PASSED" if passed else "❌ FAILED"
        print(f"{component.upper():20s}: {status}")
    
    all_passed = all(results.values())
    
    print("\n" + "=" * 60)
    if all_passed:
        print("  ALL TESTS PASSED ✓")
    else:
        print("  SOME TESTS FAILED ❌")
    print("=" * 60)
    
    return 0 if all_passed else 1


if __name__ == '__main__':
    sys.exit(main())

