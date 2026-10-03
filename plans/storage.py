"""File storage per company: what counts, how much is used, and whether a
new upload fits the plan (plans.catalog's storage_bytes)."""
import logging

from django.contrib.contenttypes.models import ContentType
from django.db.models import Sum

from accounts import team

logger = logging.getLogger(__name__)


def _company_of_user_id(user_id):
    from core.models import User
    user = User.objects.filter(pk=user_id).first()
    return team.company(user) if user else None


# model label -> (file fields, the company the files belong to)
TRACKED = {
    'marketplace.requirement': (('file',), lambda obj: _company_of_user_id(obj.user_id)),
    'marketplace.requirementpart': (('file',), lambda obj: _company_of_user_id(obj.requirement.user_id)),
    'marketplace.quote': (('quote_file',), lambda obj: obj.supplier),
    'marketplace.productionupdate': (('photo', 'document'), lambda obj: obj.order.supplier),
    'marketplace.message': (('attachment',), lambda obj: _company_of_user_id(obj.sender_id)),
    'accounts.manufacturerprofile': (('cover_image',), lambda obj: obj),
    'accounts.manufacturerphoto': (('image',), lambda obj: obj.manufacturer),
    'accounts.certification': (('document',), lambda obj: obj.manufacturer),
}


def _owner_kwargs(profile):
    from accounts.models import ConsumerProfile
    return {'buyer': profile} if isinstance(profile, ConsumerProfile) else {'supplier': profile}


def record(instance):
    """Brings the ledger in line with `instance`'s file fields."""
    from .models import StoredFile
    fields, owner_of = TRACKED[instance._meta.label_lower]
    content_type = ContentType.objects.get_for_model(instance, for_concrete_model=True)
    profile = None
    for field in fields:
        fieldfile = getattr(instance, field)
        rows = StoredFile.objects.filter(content_type=content_type, object_id=instance.pk, field=field)
        if not fieldfile or not fieldfile.name:
            rows.delete()
            continue
        existing = rows.first()
        if existing is not None and existing.name == fieldfile.name:
            continue
        profile = profile or owner_of(instance)
        if profile is None:
            continue
        try:
            size = fieldfile.size
        except (OSError, ValueError):
            logger.warning("Couldn't read the size of %s for the storage ledger", fieldfile.name)
            size = 0
        StoredFile.objects.update_or_create(
            content_type=content_type, object_id=instance.pk, field=field,
            defaults={'name': fieldfile.name[:500], 'size': size, 'buyer': None, 'supplier': None, **_owner_kwargs(profile)},
        )


def forget(instance):
    from .models import StoredFile
    content_type = ContentType.objects.get_for_model(instance, for_concrete_model=True)
    StoredFile.objects.filter(content_type=content_type, object_id=instance.pk).delete()


def used_bytes(profile):
    from .models import StoredFile
    if profile is None:
        return 0
    return StoredFile.objects.filter(**_owner_kwargs(profile)).aggregate(total=Sum('size'))['total'] or 0


def upload_problem(user, *uploads):
    """Why these new uploads don't fit the company's plan — '' if they do.
    Call before saving; `uploads` may include None/False/existing files,
    which don't count."""
    from . import access, catalog
    profile = team.company(user)
    incoming = sum(getattr(u, 'size', 0) or 0 for u in uploads if u and hasattr(u, 'content_type'))
    if profile is None or not incoming:
        return ''
    limit = access.limit(profile, catalog.STORAGE_BYTES)
    if limit is None or used_bytes(profile) + incoming <= limit:
        return ''
    return (f"This upload would take your company past its {catalog.describe_limit(catalog.STORAGE_BYTES, limit)} "
            "of file storage. Upgrade the plan, or remove files you no longer need.")


def human(size):
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f"{size:.0f} {unit}" if unit == 'bytes' else f"{size:.1f} {unit}"
        size /= 1024
