"""Main Inspection Engine - Orchestrates the complete inspection flow"""

import time

import cv2
import numpy as np
from typing import Any, Dict, List, Optional, Tuple
from src.hardware.camera import CameraController
from src.hardware.gpio_controller import GPIOController, OutputManager
from src.hardware.p9813_lighting import resolve_lighting_runtime
from src.tools.outline_tool import OutlineToolProcessor
from src.core.tool_roi import clamp_roi
from src.tools.area_tool import AreaToolProcessor
from src.tools.color_area_tool import ColorAreaToolProcessor
from src.tools.edge_detection_tool import EdgePixelsToolProcessor
from src.tools.position_adjustment import PositionAdjustmentToolProcessor
from src.utils.logger import get_logger

logger = get_logger('inspection')


class InspectionEngine:
    """
    Main inspection controller that orchestrates the complete inspection flow.
    
    Flow:
    1. Set BUSY output HIGH
    2. Capture image from camera, then scale to master width/height when needed (same pixel grid as reference)
    3. If position tool exists: find offset, adjust ROIs
    4. Process all detection tools in sequence
    5. Aggregate results (OK if all tools OK)
    6. Set output states based on configuration
    7. Set BUSY output LOW
    8. Log results to database
    9. Update statistics
    """
    
    TOOL_CLASSES = {
        'outline': OutlineToolProcessor,
        'area': AreaToolProcessor,
        'color_area': ColorAreaToolProcessor,
        'edge_detection': EdgePixelsToolProcessor,
        'position_adjust': PositionAdjustmentToolProcessor
    }
    
    def __init__(
        self,
        program_config: Dict,
        camera: Optional[CameraController] = None,
        gpio: Optional[GPIOController] = None,
        lighting: Optional[Any] = None,
        lighting_global: Optional[Dict] = None,
        release_hardware_on_cleanup: bool = False,
    ):
        """
        Initialize inspection engine.
        
        Args:
            program_config: Program configuration dictionary
            camera: Optional camera controller (creates new if None)
            gpio: Optional GPIO controller (creates new if None)
            lighting: Optional inspection illumination (single-GPIO or P9813)
            lighting_global: Global lighting section from config.yaml (merge with program)
            release_hardware_on_cleanup: If True, cleanup() closes camera and GPIO (use only
                when this engine owns exclusive hardware instances; never for shared app controllers).
        """
        self.program_config = program_config
        self._release_hardware_on_cleanup = bool(release_hardware_on_cleanup)
        self.program_id = program_config.get('id')
        self.program_name = program_config.get('name', 'Unknown')
        
        # Initialize hardware
        self.camera = camera or CameraController()
        self.gpio = gpio or GPIOController()
        self.output_manager = OutputManager(self.gpio)
        self.lighting = lighting
        self._lighting_global = lighting_global or {}
        
        # Tool processors
        self.tools: List = []
        self.position_tool: Optional[PositionAdjustmentToolProcessor] = None
        
        # Configuration
        self.trigger_type = program_config.get('triggerType', 'internal')
        self.brightness_mode = program_config.get('brightnessMode', 'normal')
        self.focus_value = program_config.get('focusValue', 50)
        # Explicit sensor controls (wizard / production)
        self.exposure_time_us = program_config.get('exposureTimeUs')
        self.analog_gain = program_config.get('analogGain')
        self.digital_gain = program_config.get('digitalGain')
        self.output_config = program_config.get('outputs', {})
        
        # Load program
        self.load_program(program_config)
        self._lighting_rt = resolve_lighting_runtime(self._lighting_global, program_config)

        logger.info(f"Inspection engine initialized for program: {self.program_name}")
    
    def load_program(self, config: Dict):
        """
        Load program configuration and initialize tools.
        
        Args:
            config: Program configuration dictionary
        """
        tools_config = config.get('tools', [])
        master_image_path = config.get('masterImage')
        
        if not master_image_path:
            raise ValueError("No master image specified in program configuration")
        
        # Load master image
        master_image = cv2.imread(master_image_path)
        if master_image is None:
            raise FileNotFoundError(f"Master image not found: {master_image_path}")
        
        # Convert BGR to RGB (copy so engine holds stable reference for quality checks)
        master_image = cv2.cvtColor(master_image, cv2.COLOR_BGR2RGB)
        self.master_image = master_image.copy()
        mh, mw = master_image.shape[:2]

        # Initialize tools
        self.tools = []
        self.position_tool = None

        for tool_config in tools_config:
            tool_type = tool_config['type']
            
            if tool_type not in self.TOOL_CLASSES:
                logger.warning(f"Unknown tool type: {tool_type}")
                continue
            
            # Create tool instance
            tool_class = self.TOOL_CLASSES[tool_type]
            tool = tool_class()
            
            # Configure tool (ROIs from wizard/DB may be floats — OpenCV needs int slices)
            roi = clamp_roi(tool_config['roi'], mw, mh)
            threshold = tool_config['threshold']
            upper_limit = tool_config.get('upperLimit')
            
            # Extract master features
            try:
                tool.configure(roi=roi, threshold=threshold, upper_limit=upper_limit)
                tool.extract_master_features(master_image, roi)
                
                # Handle position adjustment tool specially
                if tool_type == 'position_adjust':
                    if self.position_tool is not None:
                        logger.warning("Multiple position adjustment tools found. Only first will be used.")
                    else:
                        self.position_tool = tool
                else:
                    self.tools.append(tool)
                
                logger.info(f"Loaded tool: {tool.name} (threshold: {threshold})")
                
            except Exception as e:
                logger.error(f"Failed to initialize tool {tool_type}: {e}")

        if not self.tools:
            raise ValueError(
                "No detection tools loaded. Fix tool configuration or check logs for "
                "failed tool initialisation."
            )

        # Baseline ROIs from program config — reapplied every cycle so position_adjust
        # offsets never accumulate across continuous inspections.
        self._detection_tool_baseline_rois = [dict(t.roi) for t in self.tools]

        logger.info(
            f"Program loaded with {len(self.tools)} detection tools"
            + (f" + 1 position tool" if self.position_tool else "")
        )

    def _apply_light_for_capture(self) -> None:
        if not self.lighting or not self.lighting.is_ready():
            return
        if not self._lighting_rt.get("during_capture", True):
            return
        r, g, b = self._lighting_rt["rgb"]
        self.lighting.fill(int(r), int(g), int(b))
        self.lighting.show()
        time.sleep(float(self._lighting_rt.get("settle_ms", 2.0)) / 1000.0)

    def _release_light_after_capture(self) -> None:
        if not self.lighting or not self.lighting.is_ready():
            return
        if not self._lighting_rt.get("off_after_capture", True):
            return
        self.lighting.off()

    def _resize_capture_to_master_frame(self, image: np.ndarray) -> np.ndarray:
        """
        Scale the live RGB capture to the master image width/height.

        ROIs and templates are defined in master pixel coordinates (native IMX296
        1456×1088 after register-master). Resizing keeps each trigger frame on the same
        pixel grid as the stored reference so metrics and template matching stay consistent.
        """
        if self.master_image is None or image is None or image.size == 0:
            return image
        mh, mw = int(self.master_image.shape[0]), int(self.master_image.shape[1])
        ih, iw = int(image.shape[0]), int(image.shape[1])
        if (ih, iw) == (mh, mw):
            return image
        if ih >= mh and iw >= mw:
            interp = cv2.INTER_AREA
        else:
            interp = cv2.INTER_LANCZOS4
        aligned = cv2.resize(image, (mw, mh), interpolation=interp)
        if not getattr(self, "_logged_capture_aligned_to_master", False):
            logger.info(
                "Aligned inspection capture from %dx%d to master frame %dx%d "
                "(same pixel grid as reference)",
                iw,
                ih,
                mw,
                mh,
            )
            self._logged_capture_aligned_to_master = True
        else:
            logger.debug("Capture resized to master frame %dx%d", mw, mh)
        return aligned

    def run_inspection_cycle(self) -> Tuple[str, List[Dict], float, Optional[np.ndarray]]:
        """
        Execute single inspection cycle.
        
        Returns:
            Tuple of (status, tool_results, processing_time_ms, captured_image)
            - status: 'OK' or 'NG'
            - tool_results: List of tool result dictionaries
            - processing_time_ms: Total processing time
            - captured_image: Captured image (RGB)
        """
        start_time = time.time()
        
        try:
            # Step 1: Set BUSY output HIGH
            self.output_manager.set_busy(True)

            # Step 2: Illumination + capture (optional)
            logger.debug("Capturing image...")
            self._apply_light_for_capture()
            try:
                image = self.camera.capture_image(
                    brightness_mode=self.brightness_mode,
                    focus_value=self.focus_value,
                    exposure_time_us=self.exposure_time_us,
                    analog_gain=self.analog_gain,
                    digital_gain=self.digital_gain,
                )
            finally:
                self._release_light_after_capture()

            if image is None:
                raise RuntimeError("Failed to capture image")

            # Align capture to stored master geometry (native 1456×1088 when registered at full res).
            image = self._resize_capture_to_master_frame(image)

            # Reset detection ROIs to program baseline before applying this cycle's offset
            self._reset_detection_tool_rois()

            # Quality consistency check (first cycle only)
            # This validates that captured images have consistent quality with master image
            # Critical for accurate template matching and inspection
            if not hasattr(self, '_quality_checked'):
                self._quality_checked = True
                if self.master_image is not None:
                    consistency = self.camera.validate_image_consistency(
                        self.master_image,
                        image
                    )
                    if not consistency['consistent']:
                        logger.error(
                            f"Image quality consistency check failed: "
                            f"{consistency['issues']}"
                        )
                        # Don't fail inspection, but log warnings
                    if consistency['warnings']:
                        logger.warning(
                            f"Image quality warnings (may affect matching accuracy): "
                            f"{consistency['warnings']}"
                        )
                    logger.info(f"Quality check: {consistency['recommendation']}")
            
            # Step 3: Position adjustment (if configured)
            position_offset = None
            position_result = None
            
            if self.position_tool:
                logger.debug("Processing position adjustment...")
                position_result = self.position_tool.process(image)
                
                if position_result['status'] == 'OK':
                    position_offset = position_result['offset']
                    logger.debug(f"Position offset: dx={position_offset['dx']}, dy={position_offset['dy']}")
                    
                    # Adjust all tool ROIs
                    if position_offset['dx'] != 0 or position_offset['dy'] != 0:
                        for tool in self.tools:
                            tool.roi["x"] += position_offset["dx"]
                            tool.roi["y"] += position_offset["dy"]
                            logger.debug(f"Adjusted {tool.name} ROI by offset")
                else:
                    logger.warning(f"Position adjustment failed (confidence: {position_result['matching_rate']:.1f})")
            
            # Step 4: Process all detection tools
            logger.debug(f"Processing {len(self.tools)} detection tools...")
            tool_results = self.process_tools(image)
            
            # Add position tool result if present
            if position_result:
                tool_results.insert(0, position_result)
            
            # Step 5: Aggregate results (OK if all tools OK)
            overall_status = self.aggregate_results(tool_results)
            
            # Step 6: Set output states
            self.set_output_states(overall_status, tool_results)
            
            # Calculate processing time
            processing_time_ms = (time.time() - start_time) * 1000
            
            logger.info(f"Inspection complete: {overall_status} ({processing_time_ms:.1f}ms)")
            
            return overall_status, tool_results, processing_time_ms, image
            
        except Exception as e:
            logger.error(f"Inspection cycle failed: {e}", exc_info=True)
            raise
            
        finally:
            # Step 7: Set BUSY output LOW
            self.output_manager.set_busy(False)

    def _reset_detection_tool_rois(self) -> None:
        """Restore each detection tool ROI from the baseline captured at load time."""
        if not getattr(self, "_detection_tool_baseline_rois", None):
            return
        for tool, base in zip(self.tools, self._detection_tool_baseline_rois):
            tool.roi = dict(base)

    def process_tools(self, image: np.ndarray) -> List[Dict]:
        """
        Process all tools and return results.
        
        Args:
            image: Captured image (RGB)
            
        Returns:
            List of tool result dictionaries
        """
        tool_results = []
        
        for tool in self.tools:
            try:
                result = tool.process(image)
                tool_results.append(result)
                
                logger.debug(f"{tool.name}: {result['status']} (rate: {result['matching_rate']:.1f})")
                
            except Exception as e:
                logger.error(f"Tool {tool.name} failed: {e}")
                # Add failed result
                tool_results.append({
                    'tool_type': tool.tool_type,
                    'name': tool.name,
                    'status': 'NG',
                    'matching_rate': 0.0,
                    'error': str(e)
                })
        
        return tool_results
    
    def aggregate_results(self, tool_results: List[Dict]) -> str:
        """
        Aggregate tool results to determine overall status.
        
        Args:
            tool_results: List of tool result dictionaries
            
        Returns:
            Overall status: 'OK' if all tools OK, 'NG' otherwise
        """
        # OK only if ALL tools are OK (including position_adjust when present)
        if not tool_results:
            return "NG"

        for result in tool_results:
            if result["status"] == "NG":
                return "NG"

        return "OK"
    
    def set_output_states(self, overall_status: str, tool_results: List[Dict]):
        """
        Set GPIO outputs based on configuration.
        
        Args:
            overall_status: Overall inspection status
            tool_results: List of tool results (not currently used, but available for custom logic)
        """
        # Apply inspection result to outputs
        self.output_manager.apply_inspection_result(
            status=overall_status,
            custom_output_config=self.output_config,
            pulse_duration_ms=100
        )
    
    def run_continuous(
        self,
        interval_ms: int = 1000,
        callback: Optional[callable] = None,
        stop_flag: Optional[callable] = None
    ):
        """
        Run continuous inspection loop.
        
        Args:
            interval_ms: Interval between inspections (for internal trigger)
            callback: Optional callback function(status, tool_results, processing_time, image)
            stop_flag: Optional function that returns True when loop should stop
        """
        logger.info(f"Starting continuous inspection (trigger: {self.trigger_type})")
        
        inspection_count = 0
        
        try:
            while True:
                # Check stop flag
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected, ending continuous inspection")
                    break
                
                # Run inspection
                try:
                    status, tool_results, processing_time, image = self.run_inspection_cycle()
                    inspection_count += 1
                    
                    # Call callback if provided
                    if callback:
                        callback(status, tool_results, processing_time, image)
                    
                except Exception as e:
                    logger.error(f"Inspection cycle {inspection_count + 1} failed: {e}")
                
                # Wait for next trigger
                if self.trigger_type == 'internal':
                    # Internal trigger: wait for interval
                    time.sleep(interval_ms / 1000.0)
                else:
                    # External trigger: wait for GPIO signal
                    # TODO: Implement GPIO trigger monitoring
                    time.sleep(0.1)  # Polling interval
                    
        except KeyboardInterrupt:
            logger.info("Continuous inspection interrupted by user")
        
        finally:
            logger.info(f"Continuous inspection ended. Total inspections: {inspection_count}")
    
    def cleanup(self):
        """Release camera/GPIO only when release_hardware_on_cleanup was True at init."""
        if not self._release_hardware_on_cleanup:
            return
        logger.info("Cleaning up inspection engine resources (exclusive hardware)...")
        if self.camera:
            self.camera.close()
        if self.gpio:
            self.gpio.cleanup()
    
    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.cleanup()
        except Exception:
            pass

