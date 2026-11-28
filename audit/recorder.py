"""Records changes to the ERP's documents and masters in the audit log.

AuditMiddleware remembers, for the length of a request, who is making it
and from where. Signals on the models in TRACKED then write an AuditEvent
for every create, change (with each field's before and after) and delete,
for the company the record belongs to. Changes made outside a request
(management commands, the demo seed) are recorded with no user.

Lines, stock ledger entries and other children aren't tracked on their
own: their documents are, and the documents' totals and statuses change
with them. Fields that hold secrets or bulky payloads are left out.
"""
import contextvars
import datetime
import decimal
import ipaddress

from django.apps import apps
from django.db import models
from django.db.models.signals import post_delete, post_save, pre_save
from django.db.models.fields.files import FieldFile

_request = contextvars.ContextVar('audit_request', default=None)

TRACKED = [
    'accounts.Company',
    'organizations.Membership', 'organizations.Role', 'organizations.BusinessRelationship', 'organizations.Team',
    'organizations.Address', 'organizations.BankAccount',
    'inventory.Item', 'inventory.Warehouse', 'inventory.Batch',
    'purchase.PurchaseOrder', 'purchase.GoodsReceipt', 'purchase.PurchaseBill',
    'sales.SalesOrder', 'sales.Dispatch', 'sales.SalesInvoice',
    'finance.Payment', 'adjustments.CreditDebitNote', 'tds.TdsDeposit',
    'production.BillOfMaterials', 'production.WorkOrder', 'production.ProductionEntry', 'production.Machine',
    'production.CostSettings',
    'quality.InspectionLot', 'jobwork.JobWorkChallan', 'jobwork.JobWorkReceipt',
    'quotations.Quotation', 'quotations.Rfq', 'einvoice.EInvoice', 'einvoice.EwayBill',
    'approvals.ApprovalRule', 'approvals.ApprovalRequest',
    'maintenance.MaintenancePlan', 'maintenance.MaintenanceJob',
]
SKIP_FIELDS = {'password', 'token_hash', 'signed_qr', 'request', 'response', 'raw_response', 'created_at', 'updated_at',
               'last_login', 'search_vector'}


class AuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _request.set(request)
        try:
            return self.get_response(request)
        finally:
            _request.reset(token)


def _plain(value):
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return format(value.normalize(), 'f') if value == value.to_integral() else str(value)
    if isinstance(value, FieldFile):
        return value.name or ''
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    return str(value)


def _fields(instance):
    out = {}
    for field in instance._meta.concrete_fields:
        if field.name in SKIP_FIELDS or isinstance(field, models.AutoField):
            continue
        out[field.attname] = _plain(getattr(instance, field.attname))
    return out


def _company_id(instance):
    if instance._meta.label == 'accounts.Company':
        return instance.pk
    for name in ('company_id', 'buyer_id'):
        if getattr(instance, name, None):
            return getattr(instance, name)
    for parent in ('rfq', 'eway_bill', 'lot', 'entry'):
        related = getattr(instance, parent, None)
        if related is not None and getattr(related, 'company_id', None):
            return related.company_id
    return None


def _label(instance):
    for name in ('number', 'code', 'name', 'legal_name', 'title', 'irn'):
        value = getattr(instance, name, None)
        if value:
            return str(value)[:120]
    return ''


def _who():
    request = _request.get()
    if request is None:
        return None, '', None, ''
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        user = None
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    ip = forwarded or request.META.get('REMOTE_ADDR') or None
    try:
        ip = str(ipaddress.ip_address(ip)) if ip else None
    except ValueError:
        ip = None
    name = (user.get_full_name() or user.email) if user else ''
    return user, name[:150], ip, request.path[:200]


def _write(instance, action, changes):
    from .models import AuditEvent

    company_id = _company_id(instance)
    if company_id is None:
        return
    user, name, ip, path = _who()
    AuditEvent.objects.create(company_id=company_id, user=user, user_name=name, action=action,
                              object_type=instance._meta.label_lower, object_id=str(instance.pk),
                              object_label=_label(instance), changes=changes, ip_address=ip, path=path)


def _before(sender, instance, raw=False, **kwargs):
    if raw or instance.pk is None:
        instance._audit_before = None
        return
    old = sender._default_manager.filter(pk=instance.pk).first()
    instance._audit_before = _fields(old) if old is not None else None


def _after(sender, instance, created, raw=False, update_fields=None, **kwargs):
    if raw:
        return
    now = _fields(instance)
    before = getattr(instance, '_audit_before', None)
    if created or before is None:
        _write(instance, 'create', {k: v for k, v in now.items() if v not in (None, '', False)})
        return
    diff = {k: [before.get(k), v] for k, v in now.items() if before.get(k) != v}
    if diff:
        _write(instance, 'update', diff)


def _deleted(sender, instance, origin=None, **kwargs):
    # Only what was deleted directly: rows removed along with something else
    # (a company and everything in it, say) would be logged against a company that's gone.
    if origin is not instance or instance._meta.label == 'accounts.Company':
        return
    _write(instance, 'delete', {k: v for k, v in _fields(instance).items() if v not in (None, '', False)})


def connect():
    for label in TRACKED:
        try:
            model = apps.get_model(label)
        except LookupError:
            continue  # an app that isn't installed (yet)
        pre_save.connect(_before, sender=model, dispatch_uid=f'audit-pre-{label}')
        post_save.connect(_after, sender=model, dispatch_uid=f'audit-post-{label}')
        post_delete.connect(_deleted, sender=model, dispatch_uid=f'audit-del-{label}')
