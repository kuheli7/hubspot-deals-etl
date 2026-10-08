"""
Marshmallow schemas for the HubSpot Deals Extraction API
Input validation and serialization using Marshmallow
"""
from marshmallow import Schema, fields, validate, ValidationError, post_load

# Identifiers end up in PostgreSQL schema names and log lines, so they are
# restricted to a conservative character set (blocks injection attempts)
ID_PATTERN = r'^[a-zA-Z0-9_-]+$'
PROPERTY_NAME_PATTERN = r'^[a-z0-9_]+$'


class AuthSchema(Schema):
    """Authentication schema - HubSpot private app access token"""
    accessToken = fields.Str(
        required=True,
        validate=validate.Length(min=10, max=512),
        error_messages={'required': 'Access token is required'}
    )

    @post_load
    def validate_token_format(self, data, **kwargs):
        """Reject tokens that are only whitespace or contain spaces"""
        token = data.get('accessToken', '')
        if not token.strip() or any(ch.isspace() for ch in token.strip()):
            raise ValidationError('Access token must be a non-empty string without spaces',
                                  field_name='accessToken')
        data['accessToken'] = token.strip()
        return data


class FiltersSchema(Schema):
    """Deal extraction filters"""
    properties = fields.List(
        fields.Str(validate=validate.Regexp(
            PROPERTY_NAME_PATTERN,
            error='Property names may only contain lowercase letters, numbers and underscores'
        )),
        allow_none=True,
        validate=validate.Length(min=1, max=200),
        error_messages={'validator_failed': 'Properties list must contain 1-200 items'}
    )
    archived = fields.Bool(
        load_default=False,
        metadata={'description': 'Extract archived deals instead of active deals'}
    )
    pageSize = fields.Int(
        load_default=None,
        allow_none=True,
        validate=validate.Range(min=1, max=100),
        error_messages={'validator_failed': 'pageSize must be between 1 and 100'}
    )
    checkpointInterval = fields.Int(
        load_default=None,
        allow_none=True,
        validate=validate.Range(min=1, max=1000),
        error_messages={'validator_failed': 'checkpointInterval must be between 1 and 1000'}
    )


class ScanConfigSchema(Schema):
    """Scan configuration schema"""
    scanId = fields.Str(
        required=True,
        validate=[
            validate.Length(min=1, max=255),
            validate.Regexp(
                ID_PATTERN,
                error='Scan ID can only contain letters, numbers, underscores, and hyphens'
            )
        ],
        error_messages={'required': 'Scan ID is required'}
    )
    organizationId = fields.Str(
        required=True,
        validate=[
            validate.Length(min=1, max=40),
            validate.Regexp(
                ID_PATTERN,
                error='Organization ID can only contain letters, numbers, underscores, and hyphens'
            )
        ],
        error_messages={'required': 'Organization ID is required'}
    )
    type = fields.List(
        fields.Str(validate=validate.OneOf(['deal'])),
        required=True,
        validate=validate.Length(min=1),
        error_messages={
            'required': 'Type is required',
            'validator_failed': 'Type must contain at least one value and only "deal" is supported'
        }
    )
    auth = fields.Nested(
        AuthSchema,
        required=True,
        error_messages={'required': 'Authentication is required'}
    )
    filters = fields.Nested(FiltersSchema, load_default=dict)


class ScanRequestSchema(Schema):
    """Complete scan request schema"""
    config = fields.Nested(
        ScanConfigSchema,
        required=True,
        error_messages={'required': 'Config is required'}
    )


class ValidateCredentialsSchema(Schema):
    """Request body for POST /auth/validate"""
    accessToken = fields.Str(
        required=True,
        validate=validate.Length(min=10, max=512),
        error_messages={'required': 'Access token is required'}
    )


class PaginationSchema(Schema):
    """Pagination parameters schema"""
    limit = fields.Int(
        validate=validate.Range(min=1, max=1000),
        load_default=100,
        error_messages={'validator_failed': 'Limit must be between 1 and 1000'}
    )
    offset = fields.Int(
        validate=validate.Range(min=0),
        load_default=0,
        error_messages={'validator_failed': 'Offset cannot be negative'}
    )


class CleanupRequestSchema(Schema):
    """Cleanup request schema"""
    daysOld = fields.Int(
        validate=validate.Range(min=1, max=365),
        load_default=7,
        error_messages={
            'validator_failed': 'daysOld must be between 1 and 365 days'
        }
    )


class ScanConfig:
    """Scan configuration data class"""
    def __init__(self, scanId: str, organizationId: str, type: list, auth: dict, filters: dict = None):
        self.scanId = scanId
        self.organizationId = organizationId
        self.type = type
        self.auth = auth
        self.filters = filters or {}


# Schema instances for reuse
scan_config_schema = ScanConfigSchema()
scan_request_schema = ScanRequestSchema()
pagination_schema = PaginationSchema()
cleanup_request_schema = CleanupRequestSchema()
validate_credentials_schema = ValidateCredentialsSchema()


def validate_scan_request(json_data: dict) -> dict:
    """Validate scan request data and return validated config"""
    validated = scan_request_schema.load(json_data)
    config = validated['config']
    # Drop unset optional filters so stored job config stays minimal
    config['filters'] = {k: v for k, v in (config.get('filters') or {}).items() if v is not None}
    return config


def validate_pagination_params(limit, offset, max_limit: int = 1000) -> tuple:
    """Validate pagination parameters"""
    data = {'limit': limit, 'offset': offset}
    # Create a temporary schema with custom max limit
    temp_schema = PaginationSchema()
    temp_schema.fields['limit'].validate = validate.Range(min=1, max=max_limit)
    temp_schema.fields['limit'].validators = [validate.Range(min=1, max=max_limit)]
    validated = temp_schema.load(data)
    return validated['limit'], validated['offset']


def validate_cleanup_request(json_data: dict) -> int:
    """Validate cleanup request and return days_old"""
    validated = cleanup_request_schema.load(json_data)
    return validated['daysOld']


def validate_credentials_request(json_data: dict) -> str:
    """Validate a credential check request and return the token"""
    validated = validate_credentials_schema.load(json_data)
    return validated['accessToken'].strip()
