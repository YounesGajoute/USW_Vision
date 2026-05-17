"""
Marshmallow schemas for API request/response validation.
Provides input validation and serialization for all API endpoints.
"""

from marshmallow import Schema, fields, validate, validates, validates_schema, ValidationError, post_load
from typing import Dict, Any


# ==================== TOOL SCHEMAS ====================

class ROISchema(Schema):
    """Schema for Region of Interest."""
    x = fields.Integer(required=True, validate=validate.Range(min=0))
    y = fields.Integer(required=True, validate=validate.Range(min=0))
    width = fields.Integer(required=True, validate=validate.Range(min=1))
    height = fields.Integer(required=True, validate=validate.Range(min=1))


class ToolConfigSchema(Schema):
    """Schema for detection tool configuration."""
    type = fields.String(
        required=True,
        validate=validate.OneOf([
            'outline', 'area', 'color_area', 'edge_detection', 'position_adjust'
        ])
    )
    name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    color = fields.String(required=True, validate=validate.Regexp(r'^#[0-9A-Fa-f]{6}$'))
    roi = fields.Nested(ROISchema, required=True)
    threshold = fields.Integer(required=True, validate=validate.Range(min=0, max=255))
    upperLimit = fields.Integer(allow_none=True, validate=validate.Range(min=0, max=255))
    parameters = fields.Dict(keys=fields.String(), values=fields.Raw(), missing=dict)
    outputAssignment = fields.Integer(allow_none=True, validate=validate.Range(min=0, max=7))
    
    @validates_schema
    def validate_threshold_range(self, data, **kwargs):
        """Validate that upperLimit is greater than threshold if provided."""
        if data.get('upperLimit') is not None:
            if data['upperLimit'] <= data['threshold']:
                raise ValidationError('upperLimit must be greater than threshold', field_name='upperLimit')


class ProgramConfigSchema(Schema):
    """Schema for inspection program configuration."""
    masterImage = fields.String(allow_none=True)
    tools = fields.List(fields.Nested(ToolConfigSchema), required=True, validate=validate.Length(min=1))
    triggerType = fields.String(
        missing='internal',
        validate=validate.OneOf(['internal', 'external'])
    )
    triggerInterval = fields.Integer(missing=1000, validate=validate.Range(min=100, max=60000))
    judgmentMode = fields.String(
        missing='all',
        validate=validate.OneOf(['all', 'any'])
    )
    outputMode = fields.String(
        missing='first_ng',
        validate=validate.OneOf(['first_ng', 'all_ng', 'final'])
    )


class ProgramSchema(Schema):
    """Schema for inspection program."""
    name = fields.String(required=True, validate=validate.Length(min=1, max=255))
    description = fields.String(allow_none=True, validate=validate.Length(max=1000))
    config = fields.Nested(ProgramConfigSchema, required=True)
    
    @validates('name')
    def validate_name(self, value):
        """Validate program name."""
        if not value or not value.strip():
            raise ValidationError('Program name cannot be empty or whitespace only')


class ProgramUpdateSchema(Schema):
    """Schema for updating inspection program (all fields optional)."""
    name = fields.String(validate=validate.Length(min=1, max=255))
    description = fields.String(allow_none=True, validate=validate.Length(max=1000))
    config = fields.Nested(ProgramConfigSchema)
    is_active = fields.Boolean()


# ==================== INSPECTION SCHEMAS ====================

class InspectionTriggerSchema(Schema):
    """Schema for triggering inspection."""
    programId = fields.Integer(required=True, validate=validate.Range(min=1))
    continuous = fields.Boolean(missing=False)
    saveImage = fields.Boolean(missing=True)


class InspectionFilterSchema(Schema):
    """Schema for filtering inspection history."""
    programId = fields.Integer(allow_none=True, validate=validate.Range(min=1))
    status = fields.String(allow_none=True, validate=validate.OneOf(['OK', 'NG']))
    startDate = fields.DateTime(allow_none=True, format='iso')
    endDate = fields.DateTime(allow_none=True, format='iso')
    limit = fields.Integer(missing=100, validate=validate.Range(min=1, max=1000))
    offset = fields.Integer(missing=0, validate=validate.Range(min=0))
    
    @validates_schema
    def validate_date_range(self, data, **kwargs):
        """Validate that endDate is after startDate."""
        if data.get('startDate') and data.get('endDate'):
            if data['endDate'] <= data['startDate']:
                raise ValidationError('endDate must be after startDate')


# ==================== CAMERA SCHEMAS ====================

class CameraCaptureSchema(Schema):
    """Schema for camera capture settings."""
    brightnessMode = fields.String(
        missing='normal',
        validate=validate.OneOf(['normal', 'hdr', 'highgain'])
    )
    focusValue = fields.Integer(
        missing=50,
        validate=validate.Range(min=0, max=100)
    )
    saveImage = fields.Boolean(missing=False)
    fileName = fields.String(allow_none=True, validate=validate.Length(max=255))


class CameraSettingsSchema(Schema):
    """Schema for camera settings."""
    resolution = fields.List(
        fields.Integer(validate=validate.Range(min=1)),
        validate=validate.Length(equal=2),
        allow_none=True
    )
    fps = fields.Integer(allow_none=True, validate=validate.Range(min=1, max=60))
    autoExposure = fields.Boolean(missing=True)
    exposureValue = fields.Integer(allow_none=True, validate=validate.Range(min=0, max=100))
    whiteBalance = fields.String(
        missing='auto',
        validate=validate.OneOf(['auto', 'daylight', 'cloudy', 'tungsten', 'fluorescent'])
    )


# ==================== GPIO SCHEMAS ====================

class GPIOOutputSchema(Schema):
    """Schema for GPIO output control."""
    state = fields.Boolean(required=True)
    duration = fields.Integer(allow_none=True, validate=validate.Range(min=0, max=60000))


# ==================== AUTHENTICATION SCHEMAS ====================

class UserRegistrationSchema(Schema):
    """Schema for user registration."""
    username = fields.String(
        required=True,
        validate=[
            validate.Length(min=3, max=50),
            validate.Regexp(r'^[a-zA-Z0-9_]+$', error='Username can only contain letters, numbers, and underscores')
        ]
    )
    password = fields.String(
        required=True,
        validate=validate.Length(min=8),
        load_only=True  # Never include in serialization
    )
    role = fields.String(
        missing='OPERATOR',
        validate=validate.OneOf(['ADMIN', 'OPERATOR', 'VIEWER'])
    )
    
    @validates('password')
    def validate_password(self, value):
        """Validate password complexity."""
        import re
        if not re.match(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d@$!%*#?&]{8,}$', value):
            raise ValidationError('Password must contain at least one letter and one number')


class UserLoginSchema(Schema):
    """Schema for user login."""
    username = fields.String(required=True)
    password = fields.String(required=True, load_only=True)


class PasswordChangeSchema(Schema):
    """Schema for password change."""
    oldPassword = fields.String(required=True, load_only=True)
    newPassword = fields.String(
        required=True,
        validate=validate.Length(min=8),
        load_only=True
    )
    
    @validates('newPassword')
    def validate_new_password(self, value):
        """Validate new password complexity."""
        import re
        if not re.match(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d@$!%*#?&]{8,}$', value):
            raise ValidationError('Password must contain at least one letter and one number')


class TokenRefreshSchema(Schema):
    """Schema for token refresh."""
    refreshToken = fields.String(required=True)


class UserUpdateSchema(Schema):
    """Schema for updating user (admin only)."""
    role = fields.String(validate=validate.OneOf(['ADMIN', 'OPERATOR', 'VIEWER']))
    is_active = fields.Boolean()


# ==================== SYSTEM SCHEMAS ====================

class SystemLogFilterSchema(Schema):
    """Schema for filtering system logs."""
    level = fields.String(
        allow_none=True,
        validate=validate.OneOf(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'])
    )
    category = fields.String(allow_none=True)
    startDate = fields.DateTime(allow_none=True, format='iso')
    endDate = fields.DateTime(allow_none=True, format='iso')
    limit = fields.Integer(missing=100, validate=validate.Range(min=1, max=1000))


class BackupConfigSchema(Schema):
    """Schema for backup configuration."""
    includeDatabase = fields.Boolean(missing=True)
    includeImages = fields.Boolean(missing=True)
    includeLogs = fields.Boolean(missing=False)
    compressionLevel = fields.Integer(missing=6, validate=validate.Range(min=0, max=9))


# ==================== VALIDATION DECORATORS ====================

def validate_schema(schema_class):
    """
    Decorator to validate request JSON against a marshmallow schema.
    
    Usage:
        @api.route('/endpoint', methods=['POST'])
        @validate_schema(ProgramSchema)
        def create_program():
            data = request.validated_data  # Access validated data
            ...
    """
    from functools import wraps
    from flask import request, jsonify
    
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Get request JSON
            json_data = request.get_json(silent=True)
            
            if json_data is None:
                return jsonify({
                    'success': False,
                    'error': 'INVALID_JSON',
                    'message': 'Request body must be valid JSON'
                }), 400
            
            # Validate against schema
            schema = schema_class()
            try:
                validated_data = schema.load(json_data)
                # Store validated data in request for access in route handler
                request.validated_data = validated_data
                
            except ValidationError as err:
                return jsonify({
                    'success': False,
                    'error': 'VALIDATION_ERROR',
                    'message': 'Request validation failed',
                    'errors': err.messages
                }), 400
            
            return f(*args, **kwargs)
        
        return decorated_function
    
    return decorator


def validate_query_params(schema_class):
    """
    Decorator to validate query parameters against a marshmallow schema.
    
    Usage:
        @api.route('/endpoint', methods=['GET'])
        @validate_query_params(InspectionFilterSchema)
        def get_inspections():
            filters = request.validated_params
            ...
    """
    from functools import wraps
    from flask import request, jsonify
    
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Get query parameters
            query_params = request.args.to_dict()
            
            # Validate against schema
            schema = schema_class()
            try:
                validated_params = schema.load(query_params)
                # Store validated params in request
                request.validated_params = validated_params
                
            except ValidationError as err:
                return jsonify({
                    'success': False,
                    'error': 'VALIDATION_ERROR',
                    'message': 'Query parameter validation failed',
                    'errors': err.messages
                }), 400
            
            return f(*args, **kwargs)
        
        return decorated_function
    
    return decorator
