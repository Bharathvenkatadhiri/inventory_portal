"""Our own errors for GST lookups. Provider adapters translate whatever
their API does (HTTP 504s, vendor error codes, odd payloads) into these, so
nothing provider-specific reaches the service, the API or the UI."""


class GSTProviderError(Exception):
    """Base class for a lookup that couldn't be completed."""
    code = 'GST_PROVIDER_ERROR'


class GSTINNotFound(GSTProviderError):
    """The provider reached GSTN and it has no such GSTIN. Not a failure of
    the provider: the GSTIN itself is invalid."""
    code = 'GSTIN_NOT_FOUND'


class GSTProviderTimeout(GSTProviderError):
    code = 'GST_PROVIDER_TIMEOUT'


class GSTServiceUnavailable(GSTProviderError):
    code = 'GST_PROVIDER_UNAVAILABLE'


class GSTProviderRateLimited(GSTProviderError):
    code = 'GST_PROVIDER_RATE_LIMITED'


class GSTInvalidResponse(GSTProviderError):
    code = 'GST_PROVIDER_INVALID_RESPONSE'
