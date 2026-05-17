"""Position Adjustment Tool - Template matching for position correction"""

import cv2
import numpy as np
from typing import Dict, List, Tuple
from src.tools.base_tool import BaseToolProcessor


class PositionAdjustmentToolProcessor(BaseToolProcessor):
    """
    Template matching for position correction.
    IMPORTANT: Maximum 1 per program, processed FIRST.
    
    Algorithm:
    1. Extract template from master image
    2. For test image: perform template matching
    3. Find position offset (dx, dy)
    4. Adjust all other tool ROIs by offset
    5. Return matching rate for validity check
    
    Use cases:
    - Part misalignment compensation
    - Position drift correction
    """
    
    def __init__(self):
        super().__init__()
        self.tool_type = "position_adjust"
        self.name = "Position Adjustment Tool"
        self.master_template = None
        self.expected_position = None
        self.search_margin = 50  # Pixels to search around expected position
    
    def configure(self, roi: Dict[str, int], threshold: int = 70, 
                 search_margin: int = 50):
        """
        Configure position adjustment tool.
        
        Args:
            roi: Region of interest (template area)
            threshold: Minimum matching confidence (0-100)
            search_margin: Margin to search around expected position (pixels)
        """
        super().configure(roi, threshold)
        self.search_margin = search_margin
    
    def extract_master_features(self, master_image: np.ndarray, roi: Dict[str, int]):
        """
        Extract template region.
        
        Args:
            master_image: Master reference image (RGB)
            roi: Region of interest (template area)
        """
        self.configure(roi=roi, threshold=self.threshold)
        self.master_image = master_image.copy()
        
        # Extract template ROI
        template = self.extract_roi(master_image)
        
        # Convert to grayscale for matching
        if len(template.shape) == 3:
            self.master_template = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
        else:
            self.master_template = template
        
        # Store expected position (center of ROI)
        self.expected_position = (
            roi['x'] + roi['width'] // 2,
            roi['y'] + roi['height'] // 2
        )
        
        # Store features
        self.master_features = {
            'template': self.master_template,
            'expected_position': self.expected_position,
            'roi': roi
        }
    
    def find_position_offset(self, test_image: np.ndarray) -> Tuple[int, int, float]:
        """
        Use cv2.matchTemplate with TM_CCOEFF_NORMED.
        Find best match location.
        Calculate offset from expected position.
        
        Args:
            test_image: Test image (RGB)
            
        Returns:
            Tuple of (dx, dy, match_confidence)
        """
        # Convert test image to grayscale
        if len(test_image.shape) == 3:
            gray = cv2.cvtColor(test_image, cv2.COLOR_RGB2GRAY)
        else:
            gray = test_image
        
        # Define search region (expected position ± search margin)
        x_start = max(0, self.roi['x'] - self.search_margin)
        y_start = max(0, self.roi['y'] - self.search_margin)
        x_end = min(gray.shape[1], self.roi['x'] + self.roi['width'] + self.search_margin)
        y_end = min(gray.shape[0], self.roi['y'] + self.roi['height'] + self.search_margin)
        
        search_region = gray[y_start:y_end, x_start:x_end]
        
        # Perform template matching
        result = cv2.matchTemplate(search_region, self.master_template, cv2.TM_CCOEFF_NORMED)
        
        # Find best match
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
        
        # Calculate actual position in full image
        match_x = x_start + max_loc[0] + self.master_template.shape[1] // 2
        match_y = y_start + max_loc[1] + self.master_template.shape[0] // 2
        
        # Calculate offset from expected position
        dx = match_x - self.expected_position[0]
        dy = match_y - self.expected_position[1]
        
        # Match confidence (0-100)
        confidence = max_val * 100
        
        return dx, dy, confidence
    
    def calculate_matching_rate(self, test_image: np.ndarray) -> float:
        """
        Calculate matching rate for position tool.
        
        Args:
            test_image: Test image (RGB)
            
        Returns:
            Match confidence 0-100
        """
        dx, dy, confidence = self.find_position_offset(test_image)
        return confidence
    
    def adjust_rois(self, original_rois: List[Dict], offset: Tuple[int, int]) -> List[Dict]:
        """
        Adjust all ROI coordinates by (dx, dy).
        
        Args:
            original_rois: List of ROI dictionaries
            offset: Tuple of (dx, dy) offset
            
        Returns:
            List of adjusted ROI dictionaries
        """
        dx, dy = offset
        adjusted_rois = []
        
        for roi in original_rois:
            adjusted_roi = roi.copy()
            adjusted_roi['x'] += dx
            adjusted_roi['y'] += dy
            adjusted_rois.append(adjusted_roi)
        
        return adjusted_rois
    
    def judge(self, matching_rate: float) -> tuple:
        """
        OK if match is good enough to trust offset.
        
        Args:
            matching_rate: Match confidence
            
        Returns:
            Tuple of (status, matching_rate)
        """
        if matching_rate >= self.threshold:
            return ('OK', matching_rate)
        else:
            return ('NG', matching_rate)
    
    def process(self, test_image: np.ndarray) -> Dict:
        """
        Process test image and return result with offset information.
        
        Args:
            test_image: Test image to inspect
            
        Returns:
            Result dictionary with status, matching_rate, offset
        """
        if self.master_features is None:
            raise RuntimeError(f"{self.name}: Master features not extracted.")
        
        # Find position offset
        dx, dy, confidence = self.find_position_offset(test_image)
        
        # Make judgment
        status, final_rate = self.judge(confidence)
        
        return {
            'tool_type': self.tool_type,
            'name': self.name,
            'status': status,
            'matching_rate': final_rate,
            'threshold': self.threshold,
            'offset': {'dx': int(dx), 'dy': int(dy)},
            'confidence': confidence
        }

