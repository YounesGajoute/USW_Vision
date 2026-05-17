"""
Custom exception hierarchy for Vision Inspection System.
Provides specific exception types for different error scenarios.
"""


class VisionSystemError(Exception):
    """Base exception for all Vision Inspection System errors."""
    
    def __init__(self, message: str, error_code: str = None, details: dict = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code or 'SYSTEM_ERROR'
        self.details = details or {}
    
    def to_dict(self):
        """Convert exception to dictionary for JSON response."""
        return {
            'error': self.error_code,
            'message': self.message,
            'details': self.details
        }


class CameraError(VisionSystemError):
    """Camera hardware and capture errors."""
    
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, 'CAMERA_ERROR', details)


class CameraNotFoundError(CameraError):
    """Camera device not found or not accessible."""
    
    def __init__(self, message: str = "Camera device not found", details: dict = None):
        super().__init__(message, details)
        self.error_code = 'CAMERA_NOT_FOUND'


class CameraTimeoutError(CameraError):
    """Camera operation timed out."""
    
    def __init__(self, message: str = "Camera operation timed out", details: dict = None):
        super().__init__(message, details)
        self.error_code = 'CAMERA_TIMEOUT'


class CameraBusyError(CameraError):
    """Camera is already in use."""
    
    def __init__(self, message: str = "Camera is busy or in use", details: dict = None):
        super().__init__(message, details)
        self.error_code = 'CAMERA_BUSY'


class InspectionError(VisionSystemError):
    """Inspection execution and processing errors."""
    
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, 'INSPECTION_ERROR', details)


class ProgramNotFoundError(InspectionError):
    """Inspection program not found."""
    
    def __init__(self, program_id: int = None, program_name: str = None):
        identifier = program_name if program_name else f"ID {program_id}"
        message = f"Inspection program not found: {identifier}"
        super().__init__(message)
        self.error_code = 'PROGRAM_NOT_FOUND'
        self.details = {'program_id': program_id, 'program_name': program_name}


class MasterImageNotFoundError(InspectionError):
    """Master image not found for program."""
    
    def __init__(self, program_id: int):
        message = f"Master image not found for program {program_id}"
        super().__init__(message)
        self.error_code = 'MASTER_IMAGE_NOT_FOUND'
        self.details = {'program_id': program_id}


class InspectionTimeoutError(InspectionError):
    """Inspection execution timed out."""
    
    def __init__(self, message: str = "Inspection execution timed out", details: dict = None):
        super().__init__(message, details)
        self.error_code = 'INSPECTION_TIMEOUT'


class DatabaseError(VisionSystemError):
    """Database operation errors."""
    
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, 'DATABASE_ERROR', details)


class DatabaseConnectionError(DatabaseError):
    """Database connection failed."""
    
    def __init__(self, message: str = "Failed to connect to database", details: dict = None):
        super().__init__(message, details)
        self.error_code = 'DATABASE_CONNECTION_ERROR'


class DatabaseConstraintError(DatabaseError):
    """Database constraint violation (e.g., unique constraint)."""
    
    def __init__(self, message: str, constraint: str = None):
        super().__init__(message)
        self.error_code = 'DATABASE_CONSTRAINT_ERROR'
        if constraint:
            self.details['constraint'] = constraint


class ConfigurationError(VisionSystemError):
    """Configuration and validation errors."""
    
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, 'CONFIGURATION_ERROR', details)


class ValidationError(ConfigurationError):
    """Input validation error."""
    
    def __init__(self, message: str, field: str = None, value = None):
        super().__init__(message)
        self.error_code = 'VALIDATION_ERROR'
        if field:
            self.details['field'] = field
        if value is not None:
            self.details['value'] = str(value)


class InvalidProgramConfigError(ConfigurationError):
    """Invalid program configuration."""
    
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, details)
        self.error_code = 'INVALID_PROGRAM_CONFIG'


class HardwareError(VisionSystemError):
    """GPIO and hardware communication errors."""
    
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, 'HARDWARE_ERROR', details)


class GPIOError(HardwareError):
    """GPIO operation error."""
    
    def __init__(self, message: str, pin: int = None):
        super().__init__(message)
        self.error_code = 'GPIO_ERROR'
        if pin is not None:
            self.details['pin'] = pin


class GPIONotAvailableError(HardwareError):
    """GPIO hardware not available."""
    
    def __init__(self, message: str = "GPIO hardware not available on this system"):
        super().__init__(message)
        self.error_code = 'GPIO_NOT_AVAILABLE'


class ImageProcessingError(VisionSystemError):
    """OpenCV and image processing errors."""
    
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, 'IMAGE_PROCESSING_ERROR', details)


class InvalidImageError(ImageProcessingError):
    """Invalid image data or format."""
    
    def __init__(self, message: str = "Invalid image data or format", details: dict = None):
        super().__init__(message, details)
        self.error_code = 'INVALID_IMAGE'


class InvalidROIError(ImageProcessingError):
    """Invalid ROI (Region of Interest) coordinates."""
    
    def __init__(self, message: str, roi: dict = None):
        super().__init__(message)
        self.error_code = 'INVALID_ROI'
        if roi:
            self.details['roi'] = roi


class ImageQualityError(ImageProcessingError):
    """Image quality below acceptable threshold."""
    
    def __init__(self, message: str, quality_metrics: dict = None):
        super().__init__(message)
        self.error_code = 'IMAGE_QUALITY_ERROR'
        if quality_metrics:
            self.details['quality_metrics'] = quality_metrics


class AuthenticationError(VisionSystemError):
    """Authentication and authorization errors."""
    
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, 'AUTHENTICATION_ERROR', details)


class InvalidCredentialsError(AuthenticationError):
    """Invalid username or password."""
    
    def __init__(self, message: str = "Invalid username or password"):
        super().__init__(message)
        self.error_code = 'INVALID_CREDENTIALS'


class TokenExpiredError(AuthenticationError):
    """JWT token has expired."""
    
    def __init__(self, message: str = "Authentication token has expired"):
        super().__init__(message)
        self.error_code = 'TOKEN_EXPIRED'


class TokenInvalidError(AuthenticationError):
    """JWT token is invalid."""
    
    def __init__(self, message: str = "Invalid authentication token"):
        super().__init__(message)
        self.error_code = 'TOKEN_INVALID'


class InsufficientPermissionsError(AuthenticationError):
    """User lacks required permissions."""
    
    def __init__(self, required_role: str = None):
        message = f"Insufficient permissions. Required role: {required_role}" if required_role else "Insufficient permissions"
        super().__init__(message)
        self.error_code = 'INSUFFICIENT_PERMISSIONS'
        if required_role:
            self.details['required_role'] = required_role


class AccountLockedError(AuthenticationError):
    """User account is locked due to failed login attempts."""
    
    def __init__(self, message: str = "Account is locked due to too many failed login attempts"):
        super().__init__(message)
        self.error_code = 'ACCOUNT_LOCKED'


class StorageError(VisionSystemError):
    """File storage and retrieval errors."""
    
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, 'STORAGE_ERROR', details)


class FileNotFoundError(StorageError):
    """File not found in storage."""
    
    def __init__(self, file_path: str):
        message = f"File not found: {file_path}"
        super().__init__(message)
        self.error_code = 'FILE_NOT_FOUND'
        self.details['file_path'] = file_path


class DiskSpaceError(StorageError):
    """Insufficient disk space."""
    
    def __init__(self, required_space: int = None):
        message = "Insufficient disk space"
        if required_space:
            message += f" (required: {required_space} bytes)"
        super().__init__(message)
        self.error_code = 'DISK_SPACE_ERROR'
        if required_space:
            self.details['required_space'] = required_space


class RateLimitExceededError(VisionSystemError):
    """API rate limit exceeded."""
    
    def __init__(self, message: str = "Rate limit exceeded. Please try again later."):
        super().__init__(message, 'RATE_LIMIT_EXCEEDED')
