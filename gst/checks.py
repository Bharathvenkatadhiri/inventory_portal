from django.conf import settings
from django.core.checks import Error, Warning, register


@register()
def gst_provider_check(app_configs, **kwargs):
    from .services.providers import NOT_IMPLEMENTED, PROVIDERS
    name = getattr(settings, 'GST_PROVIDER', 'mock')
    if name not in PROVIDERS:
        return [Error(f"Unknown GST_PROVIDER {name!r}.", hint=f"Use one of: {', '.join(sorted(PROVIDERS))}.", id='gst.E001')]
    if name in NOT_IMPLEMENTED:
        return [Warning(
            f"GST_PROVIDER={name}, but that adapter's API call isn't implemented yet, so every GSTIN verification will fail.",
            hint=f"Implement _fetch() in gst/services/providers/{name}.py, or set GST_PROVIDER=mock.", id='gst.W001',
        )]
    return []
