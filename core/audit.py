"""Records staff actions on other accounts to core.AuditLogEntry."""
import logging

from .models import AuditLogEntry

logger = logging.getLogger(__name__)


def record(request, action, target, detail=''):
    """Logs `action` (e.g. "supplier.deactivate") by the request's user on
    `target` (any model instance). Never raises: an audit write failing
    must not undo or block the action it describes, so a failure is
    logged loudly instead."""
    user = getattr(request, 'user', None)
    try:
        AuditLogEntry.objects.create(
            actor=user if getattr(user, 'is_authenticated', False) else None,
            actor_email=getattr(user, 'email', '') or '',
            action=action,
            target_type=target._meta.label_lower,
            target_id=str(target.pk),
            target_repr=str(target)[:200],
            detail=detail,
            ip_address=request.META.get('REMOTE_ADDR') or None,
        )
    except Exception:
        logger.exception("Could not write audit log entry %s on %r", action, target)
