"""Area Tool - Monochrome area comparison based on brightness threshold"""

import cv2
import numpy as np
from typing import Dict
from src.tools.base_tool import BaseToolProcessor


class AreaToolProcessor(BaseToolProcessor):
    """
    Monochrome area comparison based on brightness threshold.
    
    Algorithm:
    1. Convert master ROI to grayscale
    2. Apply Otsu's threshold or manual threshold
    3. Count white pixels as reference area
    4. For test image: apply same threshold, count pixels
    5. Calculate ratio: (test_area / master_area) * 100
    6. Cap at 200 (can exceed 100% for larger areas)
    
    Use cases:
    - Stains detection
    - Holes detection
    - Material presence/absence
    """
    
    def __init__(self):
        super().__init__()
        self.tool_type = "area"
        self.name = "Area Tool"
        self.master_area_pixels = 0
        self.threshold_value = 127  # Binary threshold value
        self.use_otsu = True
    
    def configure(self, roi: Dict[str, int], threshold: int, upper_limit: int = None, 
                 use_otsu: bool = True, threshold_value: int = 127):
        """
        Configure area tool.
        
        Args:
            roi: Region of interest
            threshold: Lower limit for matching rate
            upper_limit: Upper limit for matching rate
            use_otsu: Whether to use Otsu's automatic thresholding
            threshold_value: Manual threshold value if not using Otsu
        """
        super().configure(roi, threshold, upper_limit)
        self.use_otsu = use_otsu
        self.threshold_value = threshold_value
    
    def extract_master_features(self, master_image: np.ndarray, roi: Dict[str, int]):
        """
        Extract master area using Otsu threshold.
        
        Args:
            master_image: Master reference image (RGB)
            roi: Region of interest
        """
        self.configure(roi=roi, threshold=self.threshold, upper_limit=self.upper_limit)
        self.master_image = master_image.copy()
        
        # Extract ROI
        roi_image = self.extract_roi(master_image)
        
        # Convert to grayscale
        if len(roi_image.shape) == 3:
            gray = cv2.cvtColor(roi_image, cv2.COLOR_RGB2GRAY)
        else:
            gray = roi_image
        
        # Apply threshold
        if self.use_otsu:
            # Otsu's automatic threshold
            threshold_value, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            self.threshold_value = threshold_value
        else:
            # Manual threshold
            _, binary = cv2.threshold(gray, self.threshold_value, 255, cv2.THRESH_BINARY)
        
        # Count white pixels (area)
        self.master_area_pixels = np.sum(binary == 255)
        
        # Store features
        self.master_features = {
            'area_pixels': self.master_area_pixels,
            'threshold_value': self.threshold_value,
            'binary_image': binary
        }
    
    def calculate_matching_rate(self, test_image: np.ndarray) -> float:
        """
        Calculate area ratio.
        
        Args:
            test_image: Test image (RGB)
            
        Returns:
            Area ratio 0-200 (can exceed 100% if test area is larger)
        """
        if self.master_area_pixels == 0:
            return 0.0
        
        # Extract ROI
        roi_image = self.extract_roi(test_image)
        
        # Convert to grayscale
        if len(roi_image.shape) == 3:
            gray = cv2.cvtColor(roi_image, cv2.COLOR_RGB2GRAY)
        else:
            gray = roi_image
        
        # Apply same threshold as master
        _, binary = cv2.threshold(gray, self.threshold_value, 255, cv2.THRESH_BINARY)
        
        # Count white pixels
        test_area_pixels = np.sum(binary == 255)
        
        # Calculate ratio (can be >100% if test area is larger)
        ratio = (test_area_pixels / self.master_area_pixels) * 100
        
        # Cap at 200%
        ratio = min(200.0, ratio)
        
        return ratio
    
    def judge(self, matching_rate: float) -> tuple:
        """
        Area tool typically uses range-based judgment.
        
        Args:
            matching_rate: Area ratio
            
        Returns:
            Tuple of (status, matching_rate)
        """
        if self.upper_limit is not None:
            # Range-based: OK if threshold <= rate <= upper_limit
            if self.threshold <= matching_rate <= self.upper_limit:
                return ('OK', matching_rate)
            else:
                return ('NG', matching_rate)
        else:
            # Single threshold: OK if rate >= threshold
            if matching_rate >= self.threshold:
                return ('OK', matching_rate)
            else:
                return ('NG', matching_rate)

