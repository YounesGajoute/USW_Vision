"""
IMX477 Camera Configuration Backend
Raspberry Pi 5 + OpenCV Enhancement Pipeline
FastAPI implementation with real-time streaming
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, Literal
import cv2
import numpy as np
from picamera2 import Picamera2, Preview
import asyncio
import time
from collections import deque
import psutil
import subprocess

app = FastAPI(title="IMX477 Camera Configuration API")

# ==================== Data Models ====================

class SensorConfig(BaseModel):
    """IMX477 sensor configuration"""
    lighting_mode: Literal["bright", "normal", "low", "astro"]
    exposure_time: int = Field(ge=114, le=5000000, description="Exposure in microseconds")
    analog_gain: float = Field(ge=1.0, le=16.0, description="Analog gain 1.0-16.0x")
    digital_gain: float = Field(ge=1.0, le=8.0, description="Digital gain 1.0-8.0x")
    wb_mode: Literal["auto", "daylight", "manual"]
    awb_red_gain: Optional[float] = Field(default=1.5, ge=0.5, le=3.0)
    awb_blue_gain: Optional[float] = Field(default=1.8, ge=0.5, le=3.0)

class DenoisingConfig(BaseModel):
    """Noise reduction configuration"""
    mode: Literal["none", "gaussian", "bilateral", "nlmeans", "temporal"]
    h_parameter: int = Field(ge=5, le=20, description="Denoising strength")
    template_size: int = Field(default=7, ge=5, le=11)
    search_size: int = Field(default=21, ge=11, le=35)

class CLAHEConfig(BaseModel):
    """CLAHE enhancement configuration"""
    enabled: bool = True
    clip_limit: float = Field(ge=1.0, le=5.0, description="Contrast limit")
    tile_size: int = Field(ge=4, le=16, description="Grid tile size")

class SharpenConfig(BaseModel):
    """Unsharp mask sharpening configuration"""
    enabled: bool = True
    amount: float = Field(ge=0.5, le=2.0, description="Sharpening strength")
    sigma: float = Field(ge=1.0, le=3.0, description="Gaussian blur sigma")
    threshold: int = Field(default=0, ge=0, le=50, description="Low-contrast threshold")

class OpenCVConfig(BaseModel):
    """Complete OpenCV enhancement pipeline"""
    denoising: DenoisingConfig
    clahe: CLAHEConfig
    sharpen: SharpenConfig

class PerformanceConfig(BaseModel):
    """Performance optimization settings"""
    resolution: Literal["480p", "720p", "1080p", "4k"]
    target_fps: Literal[15, 30, 60]
    neon_enabled: bool = True
    dual_stream_enabled: bool = True

class CameraConfig(BaseModel):
    """Complete camera configuration"""
    sensor: SensorConfig
    opencv: OpenCVConfig
    performance: PerformanceConfig

# ==================== Global State ====================

class CameraState:
    def __init__(self):
        self.picam2 = None
        self.current_config = None
        self.opencv_config = None
        self.performance_config = None
        self.is_streaming = False
        self.fps_counter = deque(maxlen=30)
        self.processing_times = deque(maxlen=30)
        
        # Pre-computed maps for lens correction
        self.remap_maps = None
        
        # CLAHE objects (create once for efficiency)
        self.clahe = None

camera_state = CameraState()

# ==================== Camera Initialization ====================

def initialize_camera():
    """Initialize Picamera2 with optimal settings for Pi 5"""
    if camera_state.picam2 is None:
        camera_state.picam2 = Picamera2()
        
    picam2 = camera_state.picam2
    
    # Get resolution from performance config
    resolution_map = {
        "480p": (640, 480),
        "720p": (1280, 720),
        "1080p": (1920, 1080),
        "4k": (4056, 3040)
    }
    
    perf_config = camera_state.performance_config
    if perf_config and perf_config.dual_stream_enabled:
        # Dual-stream: high-res main, low-res processing
        main_res = resolution_map.get(perf_config.resolution, (1920, 1080))
        lores_res = (640, 480)  # Always process at 480p for speed
        
        config = picam2.create_video_configuration(
            main={"size": main_res, "format": "RGB888"},
            lores={"size": lores_res, "format": "YUV420"},
            buffer_count=4
        )
    else:
        # Single stream
        res = resolution_map.get(
            perf_config.resolution if perf_config else "1080p", 
            (1920, 1080)
        )
        config = picam2.create_video_configuration(
            main={"size": res, "format": "RGB888"},
            buffer_count=4
        )
    
    picam2.configure(config)
    return picam2

def apply_sensor_config(config: SensorConfig):
    """Apply sensor configuration to camera"""
    if camera_state.picam2 is None:
        initialize_camera()
    
    picam2 = camera_state.picam2
    
    # Build controls dictionary
    controls = {
        "ExposureTime": config.exposure_time,
        "AnalogueGain": config.analog_gain,
    }
    
    # Only apply digital gain if analog is maxed
    if config.analog_gain >= 16.0:
        controls["DigitalGain"] = config.digital_gain
    
    # White balance
    if config.wb_mode == "auto":
        controls["AwbEnable"] = True
    elif config.wb_mode == "daylight":
        controls["AwbEnable"] = False
        controls["ColourGains"] = (1.5, 1.5)  # Daylight preset
    else:  # manual
        controls["AwbEnable"] = False
        controls["ColourGains"] = (config.awb_red_gain, config.awb_blue_gain)
    
    # Apply controls
    picam2.set_controls(controls)
    camera_state.current_config = config

# ==================== OpenCV Enhancement Pipeline ====================

class ImageEnhancer:
    """Systematic OpenCV enhancement pipeline"""
    
    def __init__(self, config: OpenCVConfig):
        self.config = config
        self.clahe = None
        if config.clahe.enabled:
            self.clahe = cv2.createCLAHE(
                clipLimit=config.clahe.clip_limit,
                tileGridSize=(config.clahe.tile_size, config.clahe.tile_size)
            )
    
    def denoise(self, frame: np.ndarray) -> np.ndarray:
        """Step 1: Noise reduction"""
        mode = self.config.denoising.mode
        h = self.config.denoising.h_parameter
        
        if mode == "none":
            return frame
        
        elif mode == "gaussian":
            # Fast Gaussian blur - suitable for real-time
            kernel_size = h if h % 2 == 1 else h + 1
            return cv2.GaussianBlur(frame, (kernel_size, kernel_size), 0)
        
        elif mode == "bilateral":
            # Edge-preserving bilateral filter
            return cv2.bilateralFilter(frame, 9, h * 7.5, h * 7.5)
        
        elif mode == "nlmeans":
            # Best quality, slow - for offline processing
            return cv2.fastNlMeansDenoisingColored(
                frame, None,
                h=h,
                hColor=h,
                templateWindowSize=self.config.denoising.template_size,
                searchWindowSize=self.config.denoising.search_size
            )
        
        elif mode == "temporal":
            # Requires frame buffer - implement temporal averaging
            # Note: This is simplified, real implementation needs frame history
            return frame
        
        return frame
    
    def enhance_contrast(self, frame: np.ndarray) -> np.ndarray:
        """Step 2: CLAHE enhancement in LAB space"""
        if not self.config.clahe.enabled or self.clahe is None:
            return frame
        
        # Convert to LAB color space
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        
        # Apply CLAHE only to L channel
        lab[:, :, 0] = self.clahe.apply(lab[:, :, 0])
        
        # Convert back to BGR
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    
    def sharpen(self, frame: np.ndarray) -> np.ndarray:
        """Step 3: Unsharp mask sharpening"""
        if not self.config.sharpen.enabled:
            return frame
        
        amount = self.config.sharpen.amount
        sigma = self.config.sharpen.sigma
        threshold = self.config.sharpen.threshold
        
        # Create blurred version
        blurred = cv2.GaussianBlur(frame, (0, 0), sigma)
        
        # Unsharp mask formula
        sharpened = cv2.addWeighted(
            frame, 1.0 + amount,
            blurred, -amount,
            0
        )
        
        # Apply threshold to prevent sharpening in low-contrast areas
        if threshold > 0:
            low_contrast_mask = np.absolute(frame - blurred) < threshold
            np.copyto(sharpened, frame, where=low_contrast_mask)
        
        return sharpened
    
    def enhance(self, frame: np.ndarray) -> np.ndarray:
        """Apply complete enhancement pipeline"""
        # Critical: Process in correct order!
        frame = self.denoise(frame)        # 1. Remove noise
        frame = self.enhance_contrast(frame)  # 2. Enhance contrast
        frame = self.sharpen(frame)        # 3. Sharpen edges
        
        return frame

# ==================== Performance Monitoring ====================

def get_cpu_temperature():
    """Get Raspberry Pi CPU temperature"""
    try:
        result = subprocess.run(
            ['vcgencmd', 'measure_temp'],
            capture_output=True,
            text=True
        )
        temp_str = result.stdout.strip()
        temp = float(temp_str.split('=')[1].split("'")[0])
        return temp
    except:
        return 0.0

def get_performance_metrics():
    """Collect current performance metrics"""
    metrics = {
        "fps": calculate_fps(),
        "avg_processing_time": calculate_avg_processing_time(),
        "cpu_usage": psutil.cpu_percent(interval=0.1),
        "memory_usage": psutil.virtual_memory().percent,
        "temperature": get_cpu_temperature(),
        "throttled": check_throttling()
    }
    return metrics

def calculate_fps():
    """Calculate current FPS from frame timestamps"""
    if len(camera_state.fps_counter) < 2:
        return 0.0
    
    time_span = camera_state.fps_counter[-1] - camera_state.fps_counter[0]
    if time_span > 0:
        return len(camera_state.fps_counter) / time_span
    return 0.0

def calculate_avg_processing_time():
    """Calculate average processing time per frame"""
    if not camera_state.processing_times:
        return 0.0
    return sum(camera_state.processing_times) / len(camera_state.processing_times)

def check_throttling():
    """Check if Pi is thermally throttled"""
    try:
        result = subprocess.run(
            ['vcgencmd', 'get_throttled'],
            capture_output=True,
            text=True
        )
        # Parse throttle value
        throttled = int(result.stdout.split('=')[1], 16)
        return throttled != 0
    except:
        return False

# ==================== API Endpoints ====================

@app.post("/api/camera/config/sensor")
async def configure_sensor(config: SensorConfig):
    """Apply sensor configuration"""
    try:
        apply_sensor_config(config)
        return {
            "success": True,
            "message": "Sensor configuration applied",
            "iso_equivalent": int(100 * config.analog_gain * config.digital_gain)
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/camera/config/opencv")
async def configure_opencv(config: OpenCVConfig):
    """Apply OpenCV enhancement configuration"""
    try:
        camera_state.opencv_config = config
        return {
            "success": True,
            "message": "OpenCV configuration applied"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/camera/config/performance")
async def configure_performance(config: PerformanceConfig):
    """Apply performance configuration"""
    try:
        camera_state.performance_config = config
        
        # Reinitialize camera with new settings
        if camera_state.picam2 is not None:
            camera_state.picam2.stop()
        initialize_camera()
        
        return {
            "success": True,
            "message": "Performance configuration applied"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/camera/config/complete")
async def configure_complete(config: CameraConfig):
    """Apply complete camera configuration"""
    try:
        # Apply all configurations
        camera_state.performance_config = config.performance
        initialize_camera()
        
        apply_sensor_config(config.sensor)
        camera_state.opencv_config = config.opencv
        
        return {
            "success": True,
            "message": "Complete configuration applied",
            "status": {
                "sensor": "configured",
                "opencv": "configured",
                "performance": "configured"
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/camera/metrics")
async def get_metrics():
    """Get current performance metrics"""
    return get_performance_metrics()

@app.get("/api/camera/status")
async def get_camera_status():
    """Get current camera status"""
    return {
        "initialized": camera_state.picam2 is not None,
        "streaming": camera_state.is_streaming,
        "sensor_config": camera_state.current_config.dict() if camera_state.current_config else None,
        "opencv_enabled": camera_state.opencv_config is not None,
        "performance": get_performance_metrics()
    }

# ==================== Video Streaming ====================

async def generate_enhanced_frames():
    """Generate enhanced video frames"""
    if camera_state.picam2 is None:
        initialize_camera()
    
    picam2 = camera_state.picam2
    
    # Start camera
    if not camera_state.is_streaming:
        picam2.start()
        camera_state.is_streaming = True
    
    # Create enhancer
    enhancer = None
    if camera_state.opencv_config:
        enhancer = ImageEnhancer(camera_state.opencv_config)
    
    while camera_state.is_streaming:
        start_time = time.time()
        
        # Capture frame (use lores if dual-stream)
        if camera_state.performance_config and camera_state.performance_config.dual_stream_enabled:
            frame = picam2.capture_array("lores")
            # Convert YUV420 to BGR
            frame = cv2.cvtColor(frame, cv2.COLOR_YUV420p2BGR)
        else:
            frame = picam2.capture_array("main")
        
        # Apply OpenCV enhancement
        if enhancer:
            frame = enhancer.enhance(frame)
        
        # Encode as JPEG
        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        frame_bytes = buffer.tobytes()
        
        # Update metrics
        processing_time = (time.time() - start_time) * 1000  # ms
        camera_state.processing_times.append(processing_time)
        camera_state.fps_counter.append(time.time())
        
        # Yield frame for streaming
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        
        # Control frame rate
        target_fps = camera_state.performance_config.target_fps if camera_state.performance_config else 30
        frame_time = 1.0 / target_fps
        elapsed = time.time() - start_time
        if elapsed < frame_time:
            await asyncio.sleep(frame_time - elapsed)

@app.get("/api/camera/stream")
async def video_stream():
    """MJPEG video stream endpoint"""
    return StreamingResponse(
        generate_enhanced_frames(),
        media_type='multipart/x-mixed-replace; boundary=frame'
    )

@app.websocket("/ws/camera/metrics")
async def websocket_metrics(websocket: WebSocket):
    """WebSocket for real-time metrics"""
    await websocket.accept()
    
    try:
        while True:
            metrics = get_performance_metrics()
            await websocket.send_json(metrics)
            await asyncio.sleep(0.1)  # 10Hz update rate
            
    except WebSocketDisconnect:
        pass

# ==================== Control Endpoints ====================

@app.post("/api/camera/start")
async def start_camera():
    """Start camera streaming"""
    try:
        if camera_state.picam2 is None:
            initialize_camera()
        
        if not camera_state.is_streaming:
            camera_state.picam2.start()
            camera_state.is_streaming = True
        
        return {"success": True, "message": "Camera started"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/camera/stop")
async def stop_camera():
    """Stop camera streaming"""
    try:
        if camera_state.picam2 and camera_state.is_streaming:
            camera_state.picam2.stop()
            camera_state.is_streaming = False
        
        return {"success": True, "message": "Camera stopped"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/camera/capture")
async def capture_image():
    """Capture a single high-resolution image"""
    try:
        if camera_state.picam2 is None:
            initialize_camera()
        
        # Capture main stream (high-res)
        frame = camera_state.picam2.capture_array("main")
        
        # Apply enhancement if configured
        if camera_state.opencv_config:
            enhancer = ImageEnhancer(camera_state.opencv_config)
            frame = enhancer.enhance(frame)
        
        # Save to file
        timestamp = int(time.time())
        filename = f"capture_{timestamp}.jpg"
        cv2.imwrite(filename, frame)
        
        return {
            "success": True,
            "filename": filename,
            "resolution": frame.shape[:2]
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# ==================== Utility Functions ====================

@app.get("/api/camera/presets")
async def get_presets():
    """Get predefined configuration presets"""
    return {
        "realtime": {
            "name": "Real-time (30+ FPS)",
            "description": "Gaussian + CLAHE + Light sharpen",
            "sensor": {
                "lighting_mode": "normal",
                "exposure_time": 5000,
                "analog_gain": 2.0,
                "digital_gain": 1.0,
                "wb_mode": "auto"
            },
            "opencv": {
                "denoising": {"mode": "gaussian", "h_parameter": 5},
                "clahe": {"enabled": True, "clip_limit": 2.0, "tile_size": 8},
                "sharpen": {"enabled": True, "amount": 1.0, "sigma": 1.5}
            },
            "performance": {
                "resolution": "1080p",
                "target_fps": 30,
                "neon_enabled": True,
                "dual_stream_enabled": True
            }
        },
        "balanced": {
            "name": "Balanced (15-20 FPS)",
            "description": "Bilateral + CLAHE + Medium sharpen",
            "sensor": {
                "lighting_mode": "normal",
                "exposure_time": 10000,
                "analog_gain": 4.0,
                "digital_gain": 1.0,
                "wb_mode": "auto"
            },
            "opencv": {
                "denoising": {"mode": "bilateral", "h_parameter": 10},
                "clahe": {"enabled": True, "clip_limit": 2.5, "tile_size": 8},
                "sharpen": {"enabled": True, "amount": 1.5, "sigma": 1.5}
            },
            "performance": {
                "resolution": "1080p",
                "target_fps": 30,
                "neon_enabled": True,
                "dual_stream_enabled": True
            }
        },
        "quality": {
            "name": "Maximum Quality (Offline)",
            "description": "NL Means + Aggressive CLAHE + Gentle sharpen",
            "sensor": {
                "lighting_mode": "low",
                "exposure_time": 33333,
                "analog_gain": 8.0,
                "digital_gain": 1.0,
                "wb_mode": "manual",
                "awb_red_gain": 1.5,
                "awb_blue_gain": 1.8
            },
            "opencv": {
                "denoising": {"mode": "nlmeans", "h_parameter": 15},
                "clahe": {"enabled": True, "clip_limit": 3.0, "tile_size": 8},
                "sharpen": {"enabled": True, "amount": 0.5, "sigma": 1.5}
            },
            "performance": {
                "resolution": "4k",
                "target_fps": 15,
                "neon_enabled": True,
                "dual_stream_enabled": False
            }
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

