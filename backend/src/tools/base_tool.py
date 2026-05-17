"""Base class for all vision inspection tools"""

from abc import ABC, abstractmethod
from typing import Dict, Tuple, Optional
import numpy as np
import cv2


class BaseToolProcessor(ABC):
    """
    Abstract base class for all detection tools.
    
    All tools must implement:
    - extract_master_features: Extract features from reference image
    - calculate_matching_rate: Compare test image with master (returns 0-200)
    - judge: Determine OK/NG based on matching rate and threshold
    """
    
    def __init__(self):
        """Initialize base tool processor."""
        self.roi = None
        self.threshold = 65
        self.upper_limit = None
        self.master_features = None
        self.master_image = None
        self.tool_type = "base"
        self.name = "Base Tool"
    
    def configure(
        self,
        roi: Dict[str, int],
        threshold: int,
        upper_limit: Optional[int] = None,
        **kwargs
    ):
        """
        Configure tool parameters.
        
        Args:
            roi: Region of interest {'x': x, 'y': y, 'width': w, 'height': h}
            threshold: Matching threshold (0-100)
            upper_limit: Optional upper limit for range-based judgment
            **kwargs: Additional tool-specific parameters
        """
        self.roi = roi
        self.threshold = threshold
        self.upper_limit = upper_limit
    
    def extract_roi(self, image: np.ndarray) -> np.ndarray:
        """
        Extract ROI from image.
        
        Args:
            image: Full image array
            
        Returns:
            ROI image
        """
        if self.roi is None:
            return image
        
        x = self.roi['x']
        y = self.roi['y']
        w = self.roi['width']
        h = self.roi['height']
        
        # Ensure ROI is within image bounds
        h_img, w_img = image.shape[:2]
        x = max(0, min(x, w_img - 1))
        y = max(0, min(y, h_img - 1))
        w = min(w, w_img - x)
        h = min(h, h_img - y)
        
        return image[y:y+h, x:x+w]
    
    @abstractmethod
    def extract_master_features(self, master_image: np.ndarray, roi: Dict[str, int]):
        """
        Extract features from master reference image.
        
        Args:
            master_image: Master reference image (RGB)
            roi: Region of interest
            
        This method should store extracted features in self.master_features
        """
        pass
    
    @abstractmethod
    def calculate_matching_rate(self, test_image: np.ndarray) -> float:
        """
        Calculate matching rate between test image and master.
        
        Args:
            test_image: Test image to compare (RGB)
            
        Returns:
            Matching rate (0-200, typically 0-100 for similarity)
        """
        pass
    
    def judge(self, matching_rate: float) -> Tuple[str, float]:
        """
        Determine OK or NG based on matching rate and threshold.
        
        Args:
            matching_rate: Calculated matching rate
            
        Returns:
            Tuple of (status, matching_rate)
            status: 'OK' or 'NG'
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
    
    def process(self, test_image: np.ndarray) -> Dict:
        """
        Process test image and return result.
        
        Args:
            test_image: Test image to inspect
            
        Returns:
            Result dictionary with status, matching_rate, and details
        """
        if self.master_features is None:
            raise RuntimeError(f"{self.name}: Master features not extracted. Call extract_master_features first.")
        
        # Calculate matching rate
        matching_rate = self.calculate_matching_rate(test_image)
        
        # Make judgment
        status, final_rate = self.judge(matching_rate)
        
        return {
            'tool_type': self.tool_type,
            'name': self.name,
            'status': status,
            'matching_rate': final_rate,
            'threshold': self.threshold,
            'upper_limit': self.upper_limit
        }
    
    def get_config(self) -> Dict:
        """
        Get tool configuration.
        
        Returns:
            Configuration dictionary
        """
        return {
            'tool_type': self.tool_type,
            'name': self.name,
            'roi': self.roi,
            'threshold': self.threshold,
            'upper_limit': self.upper_limit
        }
    
    def visualize_roi(self, image: np.ndarray, color: Tuple[int, int, int] = (0, 255, 0)) -> np.ndarray:
        """
        Draw ROI rectangle on image for visualization.
        
        Args:
            image: Image to draw on
            color: BGR color for rectangle
            
        Returns:
            Image with ROI drawn
        """
        if self.roi is None:
            return image
        
        viz_image = image.copy()
        x, y, w, h = self.roi['x'], self.roi['y'], self.roi['width'], self.roi['height']
        
        # Draw rectangle
        cv2.rectangle(viz_image, (x, y), (x + w, y + h), color, 2)
        
        # Draw label
        cv2.rectangle(viz_image, (x, y - 25), (x + 150, y), color, -1)
        cv2.putText(viz_image, self.name, (x + 5, y - 8),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        
        return viz_image

