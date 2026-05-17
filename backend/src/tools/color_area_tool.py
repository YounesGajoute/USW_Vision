"""Color Area Tool - Color-based area comparison in HSV space"""

import cv2
import numpy as np
from typing import Dict, List, Optional, Tuple
from src.tools.base_tool import BaseToolProcessor


class ColorAreaToolProcessor(BaseToolProcessor):
    """
    Color-based area comparison in HSV space.
    
    Algorithm:
    1. Convert master ROI to HSV
    2. Auto-detect dominant color or use samples
    3. Create color range with tolerance (H±15, S±40, V±40)
    4. Apply color mask, count colored pixels
    5. For test: apply same mask, calculate ratio
    
    Use cases:
    - Color verification
    - Colored component detection
    - Color stains/defects
    """
    
    def __init__(self):
        super().__init__()
        self.tool_type = "color_area"
        self.name = "Color Area Tool"
        self.master_color_pixels = 0
        self.color_lower = None
        self.color_upper = None
        self.target_hsv = None
    
    def configure(self, roi: Dict[str, int], threshold: int, upper_limit: int = None,
                 target_color: Optional[Tuple[int, int, int]] = None,
                 color_tolerance: int = 15):
        """
        Configure color area tool.
        
        Args:
            roi: Region of interest
            threshold: Lower limit for matching rate
            upper_limit: Upper limit for matching rate
            target_color: Optional target color in HSV (H, S, V)
            color_tolerance: Tolerance for color matching (Hue ±tolerance)
        """
        super().configure(roi, threshold, upper_limit)
        self.target_color = target_color
        self.color_tolerance = color_tolerance
    
    def extract_master_features(self, master_image: np.ndarray, roi: Dict[str, int],
                                color_samples: Optional[List[Tuple[int, int]]] = None):
        """
        Extract color features in HSV.
        
        Args:
            master_image: Master reference image (RGB)
            roi: Region of interest
            color_samples: Optional list of (x, y) pixel coordinates to sample color from
        """
        self.configure(roi=roi, threshold=self.threshold, upper_limit=self.upper_limit)
        self.master_image = master_image.copy()
        
        # Extract ROI
        roi_image = self.extract_roi(master_image)
        
        # Convert to HSV
        hsv = cv2.cvtColor(roi_image, cv2.COLOR_RGB2HSV)
        
        # Determine target color
        if color_samples and len(color_samples) > 0:
            # Sample colors from specified points
            sampled_colors = []
            for x, y in color_samples:
                # Adjust coordinates relative to ROI
                rel_x = x - self.roi['x']
                rel_y = y - self.roi['y']
                if 0 <= rel_x < roi_image.shape[1] and 0 <= rel_y < roi_image.shape[0]:
                    sampled_colors.append(hsv[rel_y, rel_x])
            
            if sampled_colors:
                # Average sampled colors
                self.target_hsv = np.mean(sampled_colors, axis=0).astype(np.uint8)
            else:
                # Fall back to auto-detection
                self.target_hsv = self.auto_detect_color_range(hsv)
        else:
            # Auto-detect dominant color
            self.target_hsv = self.auto_detect_color_range(hsv)
        
        # Create color range with tolerance
        h, s, v = self.target_hsv
        
        # HSV ranges
        h_tolerance = self.color_tolerance if hasattr(self, 'color_tolerance') else 15
        s_tolerance = 40
        v_tolerance = 40
        
        self.color_lower = np.array([
            max(0, h - h_tolerance),
            max(0, s - s_tolerance),
            max(0, v - v_tolerance)
        ], dtype=np.uint8)
        
        self.color_upper = np.array([
            min(179, h + h_tolerance),  # H max is 179 in OpenCV
            min(255, s + s_tolerance),
            min(255, v + v_tolerance)
        ], dtype=np.uint8)
        
        # Apply color mask
        mask = cv2.inRange(hsv, self.color_lower, self.color_upper)
        
        # Count colored pixels
        self.master_color_pixels = np.sum(mask == 255)
        
        # Store features
        self.master_features = {
            'color_pixels': self.master_color_pixels,
            'target_hsv': self.target_hsv,
            'color_lower': self.color_lower,
            'color_upper': self.color_upper,
            'mask': mask
        }
    
    def auto_detect_color_range(self, hsv_image: np.ndarray) -> np.ndarray:
        """
        Auto-detect dominant color range.
        
        Args:
            hsv_image: HSV image
            
        Returns:
            Dominant color as [H, S, V]
        """
        # Calculate median color (more robust than mean)
        h_median = np.median(hsv_image[:, :, 0])
        s_median = np.median(hsv_image[:, :, 1])
        v_median = np.median(hsv_image[:, :, 2])
        
        return np.array([h_median, s_median, v_median], dtype=np.uint8)
    
    def calculate_matching_rate(self, test_image: np.ndarray) -> float:
        """
        Calculate color area ratio.
        
        Args:
            test_image: Test image (RGB)
            
        Returns:
            Color area ratio 0-200
        """
        if self.master_color_pixels == 0:
            return 0.0
        
        # Extract ROI
        roi_image = self.extract_roi(test_image)
        
        # Convert to HSV
        hsv = cv2.cvtColor(roi_image, cv2.COLOR_RGB2HSV)
        
        # Apply same color mask as master
        mask = cv2.inRange(hsv, self.color_lower, self.color_upper)
        
        # Count colored pixels
        test_color_pixels = np.sum(mask == 255)
        
        # Calculate ratio
        ratio = (test_color_pixels / self.master_color_pixels) * 100
        
        # Cap at 200%
        ratio = min(200.0, ratio)
        
        return ratio
    
    def judge(self, matching_rate: float) -> tuple:
        """
        Color area tool judgment.
        
        Args:
            matching_rate: Color area ratio
            
        Returns:
            Tuple of (status, matching_rate)
        """
        if self.upper_limit is not None:
            # Range-based judgment
            if self.threshold <= matching_rate <= self.upper_limit:
                return ('OK', matching_rate)
            else:
                return ('NG', matching_rate)
        else:
            # Single threshold judgment
            if matching_rate >= self.threshold:
                return ('OK', matching_rate)
            else:
                return ('NG', matching_rate)

