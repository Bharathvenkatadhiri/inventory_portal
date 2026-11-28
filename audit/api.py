"""/api/v1/audit: the audit log of the organization the request is for.
Needs audit.view (Org Admin by default; Pro plan). Read only."""
from datetime import date, datetime, time, timedelta
from typing import Any, List, Optional

from django.db.models import Q
from django.utils import timezone
from ninja import Router, Schema

from organizations.app_api import membership_for

from .models import AuditEvent

router = Router(tags=['audit'])
PAGE = 100

# What each tracked model is called on screen.
NAMES = {
    'accounts.company': 'Company settings', 'organizations.membership': 'Person', 'organizations.role': 'Role',
    'organizations.businessrelationship': 'Partner', 'organizations.team': 'Team', 'organizations.address': 'Address',
    'organizations.bankaccount': 'Bank account', 'inventory.item': 'Item', 'inventory.warehouse': 'Warehouse',
    'inventory.batch': 'Batch', 'purchase.purchaseorder': 'Purchase order', 'purchase.goodsreceipt': 'Goods receipt',
    'purchase.purchasebill': 'Purchase bill', 'sales.salesorder': 'Sales order', 'sales.dispatch': 'Dispatch',
    'sales.salesinvoice': 'Sales invoice', 'finance.payment': 'Payment', 'adjustments.creditdebitnote': 'Credit/debit note',
    'tds.tdsdeposit': 'TDS deposit', 'production.billofmaterials': 'BOM', 'production.workorder': 'Work order',
    'production.productionentry': 'Production entry', 'production.machine': 'Machine', 'production.costsettings': 'Cost rates',
    'quality.inspectionlot': 'QC lot', 'jobwork.jobworkchallan': 'Job work challan', 'jobwork.jobworkreceipt': 'Job work receipt',
    'quotations.quotation': 'Quotation', 'quotations.rfq': 'RFQ', 'einvoice.einvoice': 'E-invoice', 'einvoice.ewaybill': 'E-way bill',
    'approvals.approvalrule': 'Approval rule', 'approvals.approvalrequest': 'Approval',
    'maintenance.maintenanceplan': 'Maintenance plan', 'maintenance.maintenancejob': 'Maintenance job',
}


class AuditEventOut(Schema):
    id: int
    created_at: datetime
    user_name: str
    action: str
    action_label: str
    object_type: str
    object_name: str
    object_id: str
    object_label: str
    changes: Any
    ip_address: Optional[str]


class AuditPageOut(Schema):
    events: List[AuditEventOut]
    more: bool
    object_types: List[List[str]]


@router.get('/audit', response=AuditPageOut)
def audit_log(request, object_type: str = '', object_id: str = '', user_id: Optional[int] = None, action: str = '',
              q: str = '', start: Optional[date] = None, end: Optional[date] = None, before_id: Optional[int] = None):
    """Newest first, a page at a time (pass the last id as before_id for the next)."""
    company = membership_for(request, 'audit.view').company
    events = AuditEvent.objects.filter(company=company)
    if object_type:
        events = events.filter(object_type=object_type)
    if object_id:
        events = events.filter(object_id=object_id)
    if user_id:
        events = events.filter(user_id=user_id)
    if action:
        events = events.filter(action=action)
    if q.strip():
        events = events.filter(Q(object_label__icontains=q.strip()) | Q(user_name__icontains=q.strip()))
    tz = timezone.get_current_timezone()
    if start:
        events = events.filter(created_at__gte=datetime.combine(start, time.min, tz))
    if end:
        events = events.filter(created_at__lt=datetime.combine(end + timedelta(days=1), time.min, tz))
    if before_id:
        events = events.filter(id__lt=before_id)
    rows = list(events.order_by('-id')[:PAGE + 1])
    return {
        'events': [{
            'id': e.pk, 'created_at': e.created_at, 'user_name': e.user_name or 'System', 'action': e.action,
            'action_label': e.get_action_display(), 'object_type': e.object_type,
            'object_name': NAMES.get(e.object_type, e.object_type), 'object_id': e.object_id, 'object_label': e.object_label,
            'changes': e.changes, 'ip_address': e.ip_address,
        } for e in rows[:PAGE]],
        'more': len(rows) > PAGE,
        'object_types': [[k, v] for k, v in sorted(NAMES.items(), key=lambda kv: kv[1])],
    }
