"""Outline Tool - Shape-based matching using contour comparison"""

import cv2
import numpy as np
from typing import Dict, Tuple
from src.tools.base_tool import BaseToolProcessor


class OutlineToolProcessor(BaseToolProcessor):
    """
    Shape-based matching using contour comparison.
    
    Algorithm:
    1. Extract contours from master image using Canny edge detection
    2. Store master contour features (Hu moments, area, perimeter)
    3. For test image: extract contours, compare with master
    4. Use multiple methods: Hu moments matching + template matching
    5. Return matching rate 0-100
    
    Use cases:
    - Missing components
    - Shape defects
    - Orientation verification
    """
    
    def __init__(self):
        super().__init__()
        self.tool_type = "outline"
        self.name = "Outline Tool"
        self.master_contour = None
        self.master_hu_moments = None
        self.master_area = 0
        self.master_edges = None
    
    def extract_master_features(self, master_image: np.ndarray, roi: Dict[str, int]):
        """
        Extract master contours using:
        - Grayscale conversion
        - Gaussian blur (5x5)
        - Canny edge detection (50, 150)
        - Find contours (RETR_EXTERNAL)
        - Calculate Hu moments
        - Store largest contour
        
        Args:
            master_image: Master reference image (RGB)
            roi: Region of interest
        """
        self.configure(roi=roi, threshold=self.threshold)
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
        
        # Canny edge detection
        edges = cv2.Canny(blurred, 50, 150)
        self.master_edges = edges
        
        # Find contours
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if not contours:
            raise ValueError("No contours found in master image ROI")
        
        # Get largest contour
        self.master_contour = max(contours, key=cv2.contourArea)
        
        # Calculate Hu moments
        moments = cv2.moments(self.master_contour)
        self.master_hu_moments = cv2.HuMoments(moments).flatten()
        
        # Store area and perimeter
        self.master_area = cv2.contourArea(self.master_contour)
        
        # Store features
        self.master_features = {
            'contour': self.master_contour,
            'hu_moments': self.master_hu_moments,
            'area': self.master_area,
            'edges': self.master_edges
        }
    
    def calculate_matching_rate(self, test_image: np.ndarray) -> float:
        """
        Calculate matching using:
        1. matchShapes with Hu moments
        2. Template matching on edge images
        3. Area comparison
        4. Average scores for final rate
        
        Args:
            test_image: Test image (RGB)
            
        Returns:
            Matching rate 0-100
        """
        # Extract ROI
        roi_image = self.extract_roi(test_image)
        
        # Convert to grayscale
        if len(roi_image.shape) == 3:
            gray = cv2.cvtColor(roi_image, cv2.COLOR_RGB2GRAY)
        else:
            gray = roi_image
        
        # Apply same preprocessing
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)
        
        # Find contours
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if not contours:
            # No contours found - very bad match
            return 0.0
        
        # Get largest contour
        test_contour = max(contours, key=cv2.contourArea)
        
        # Method 1: Hu moments shape matching
        shape_match_score = self._compare_hu_moments(test_contour)
        
        # Method 2: Template matching on edges
        template_match_score = self._template_match_edges(edges)
        
        # Method 3: Area comparison
        test_area = cv2.contourArea(test_contour)
        area_ratio = min(test_area, self.master_area) / max(test_area, self.master_area)
        area_score = area_ratio * 100
        
        # Weighted average
        final_score = (
            0.5 * shape_match_score +
            0.3 * template_match_score +
            0.2 * area_score
        )
        
        return min(100.0, max(0.0, final_score))
    
    def _compare_hu_moments(self, test_contour: np.ndarray) -> float:
        """
        Compare Hu moments between master and test contours.
        
        Args:
            test_contour: Test contour
            
        Returns:
            Similarity score 0-100
        """
        # Calculate Hu moments for test contour
        moments = cv2.moments(test_contour)
        test_hu_moments = cv2.HuMoments(moments).flatten()
        
        # Use cv2.matchShapes (returns distance, lower is better)
        # CONTOURS_MATCH_I1 method
        distance = cv2.matchShapes(self.master_contour, test_contour, cv2.CONTOURS_MATCH_I1, 0)
        
        # Convert distance to similarity score (0-100)
        # Typical good matches: 0.0-0.01, bad matches: >0.1
        if distance < 0.001:
            score = 100
        elif distance < 0.01:
            score = 100 - (distance * 1000)
        elif distance < 0.1:
            score = 90 - (distance * 100)
        else:
            score = max(0, 100 - distance * 100)
        
        return score
    
    def _template_match_edges(self, test_edges: np.ndarray) -> float:
        """
        Perform template matching on edge images.
        
        Args:
            test_edges: Test edge image
            
        Returns:
            Match score 0-100
        """
        # Ensure same size for template matching
        if test_edges.shape != self.master_edges.shape:
            test_edges = cv2.resize(test_edges, (self.master_edges.shape[1], self.master_edges.shape[0]))
        
        # Template matching using normalized cross-correlation
        result = cv2.matchTemplate(test_edges, self.master_edges, cv2.TM_CCORR_NORMED)
        
        # Get maximum match value
        max_val = cv2.minMaxLoc(result)[1]
        
        # Convert to percentage
        score = max_val * 100
        
        return score

