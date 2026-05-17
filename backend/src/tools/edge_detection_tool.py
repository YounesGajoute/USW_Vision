"""Edge Detection Tool - Edge density/complexity comparison"""

import cv2
import numpy as np
from typing import Dict
from src.tools.base_tool import BaseToolProcessor


class EdgePixelsToolProcessor(BaseToolProcessor):
    """
    Edge density/complexity comparison.
    
    Algorithm:
    1. Apply Canny edge detection to master ROI
    2. Count edge pixels as reference
    3. For test: apply Canny, count edges
    4. Calculate ratio: (test_edges / master_edges) * 100
    
    Use cases:
    - Texture analysis
    - Surface defects
    - Edge completeness
    """
    
    def __init__(self):
        super().__init__()
        self.tool_type = "edge_detection"
        self.name = "Edge Detection Tool"
        self.master_edge_pixels = 0
        self.low_threshold = 50
        self.high_threshold = 150
    
    def configure(self, roi: Dict[str, int], threshold: int, upper_limit: int = None,
                 low_threshold: int = 50, high_threshold: int = 150):
        """
        Configure edge detection tool.
        
        Args:
            roi: Region of interest
            threshold: Lower limit for matching rate
            upper_limit: Upper limit for matching rate
            low_threshold: Canny low threshold
            high_threshold: Canny high threshold
        """
        super().configure(roi, threshold, upper_limit)
        self.low_threshold = low_threshold
        self.high_threshold = high_threshold
    
    def extract_master_features(self, master_image: np.ndarray, roi: Dict[str, int]):
        """
        Extract edge pixels using Canny.
        
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
        
        # Apply Gaussian blur to reduce noise
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        
        # Apply Canny edge detection
        edges = cv2.Canny(blurred, self.low_threshold, self.high_threshold)
        
        # Count edge pixels
        self.master_edge_pixels = np.sum(edges == 255)
        
        # Store features
        self.master_features = {
            'edge_pixels': self.master_edge_pixels,
            'low_threshold': self.low_threshold,
            'high_threshold': self.high_threshold,
            'edges': edges
        }
    
    def calculate_matching_rate(self, test_image: np.ndarray) -> float:
        """
        Calculate edge pixel ratio.
        
        Args:
            test_image: Test image (RGB)
            
        Returns:
            Edge pixel ratio 0-200
        """
        if self.master_edge_pixels == 0:
            return 0.0
        
        # Extract ROI
        roi_image = self.extract_roi(test_image)
        
        # Convert to grayscale
        if len(roi_image.shape) == 3:
            gray = cv2.cvtColor(roi_image, cv2.COLOR_RGB2GRAY)
        else:
            gray = roi_image
        
        # Apply Gaussian blur
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        
        # Apply Canny edge detection with same thresholds as master
        edges = cv2.Canny(blurred, self.low_threshold, self.high_threshold)
        
        # Count edge pixels
        test_edge_pixels = np.sum(edges == 255)
        
        # Calculate ratio
        ratio = (test_edge_pixels / self.master_edge_pixels) * 100
        
        # Cap at 200%
        ratio = min(200.0, ratio)
        
        return ratio
    
    def judge(self, matching_rate: float) -> tuple:
        """
        Edge detection tool judgment.
        
        Args:
            matching_rate: Edge pixel ratio
            
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

