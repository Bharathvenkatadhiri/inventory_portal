"""E-invoicing and e-way bills.

E-invoice (CGST rule 48(4)): a business whose turnover has crossed ₹5 crore
in any year registers each B2B tax invoice and credit/debit note with the
IRP, which returns its IRN and a signed QR code; both are printed on the
invoice. Turn it on under Settings, Company (Company.einvoice_applicable);
plans with e-invoicing only. An IRN can be cancelled within 24 hours;
after that, a credit note is the way to undo the invoice. A posted invoice
with a live IRN can't be cancelled in the books (sales.services checks).

E-way bill (CGST rule 138): needed to move goods worth more than ₹50,000
(consignment value with tax), and for inter-state job work whatever the
value. Part A comes from the delivery or job work challan; Part B is the
vehicle. It's valid for a day per 200 km (20 km for over-dimensional
cargo), each day ending at midnight; the vehicle can be changed on the way,
and the bill cancelled within 24 hours.
"""
import math
import re
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from marketplace.documents import financial_year
from organizations import access
from plans import erp

from .models import EInvoice, EwayBill, EwayBillVehicle
from .providers import CANCEL_WINDOW, ProviderError, get_provider

ZERO = Decimal('0')
CENT = Decimal('0.01')
EWB_THRESHOLD = Decimal('50000')
MAX_DISTANCE = 4000
VEHICLE = re.compile(r'^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$|^TM[A-Z0-9]{6}$')  # e.g. TN37AB1234, or a temporary number
TRANSPORTER_ID = re.compile(r'^[0-9]{2}[A-Z0-9]{13}$')


class EInvoiceError(Exception):
    """Can't do this; the message says why."""


def _money(value):
    return float(Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP))


def _check(membership, code, counterparty=None):
    decision = access.check(membership, code, counterparty)
    if not decision:
        raise EInvoiceError(decision.reason)


def _call(fn, *args):
    try:
        return fn(*args)
    except ProviderError as exc:
        raise EInvoiceError(f"The portal refused it: {exc}")


def _pincode(value):
    digits = re.sub(r'\D', '', value or '')
    return digits if len(digits) == 6 else ''


def _place(company, warehouse=None):
    """(address line, city, pincode, state code) where goods go from or a party is."""
    address = warehouse.address if warehouse is not None and warehouse.address_id else None
    if address is not None:
        return (address.line1 or company.principal_address, address.city or company.city, _pincode(address.pincode),
                address.state_code or company.state_code)
    return company.principal_address, company.city, _pincode(company.pincode), company.state_code or (company.gstin or '')[:2]


# --- E-invoices --------------------------------------------------------------------------------------

def einvoicing_on(company):
    return bool(company.einvoice_applicable) and erp.includes(company.erp_plan, erp.EINVOICE)


def active_irn(document):
    field = 'note' if document.__class__.__name__ == 'CreditDebitNote' else 'sales_invoice'
    return EInvoice.objects.filter(**{field: document}, status=EInvoice.Status.ACTIVE).first()


def _doc_type(document):
    if document.__class__.__name__ == 'CreditDebitNote':
        return 'CRN' if document.kind == 'credit' else 'DBN'
    return 'INV'


def _date(document):
    return document.note_date if document.__class__.__name__ == 'CreditDebitNote' else document.invoice_date


def payload(document):
    """The e-invoice in the IRP's INV-01 schema (version 1.1)."""
    company = document.company
    buyer = document.party if document.__class__.__name__ == 'CreditDebitNote' else document.customer
    line1, city, pin, state = _place(company)
    b_line1, b_city, b_pin, b_state = _place(buyer)
    problems = []
    if not company.gstin:
        problems.append("your GSTIN")
    if not pin:
        problems.append("your 6-digit PIN code (Settings, Company)")
    if not buyer.gstin:
        problems.append(f"{buyer.name or buyer.legal_name}'s GSTIN: e-invoices are only for registered (B2B) customers")
    if not b_pin:
        problems.append(f"{buyer.name or buyer.legal_name}'s PIN code")
    items = []
    for n, line in enumerate(document.lines.select_related('item'), start=1):
        hsn = line.hsn_code or line.item.hsn_code
        if len(hsn or '') < 6:
            problems.append(f"a 6 or 8-digit HSN code for {line.item.name}")
        tax = line.tax_amount
        cgst = sgst = igst = ZERO
        if document.is_interstate:
            igst = tax
        else:
            cgst = (tax / 2).quantize(CENT, rounding=ROUND_HALF_UP)
            sgst = tax - cgst
        quantity = line.quantity or Decimal('1')
        items.append({
            'SlNo': str(n), 'PrdDesc': line.item.name[:300], 'IsServc': 'Y' if line.item.kind == 'service' else 'N',
            'HsnCd': hsn, 'Qty': float(quantity), 'Unit': line.unit or 'OTH',
            'UnitPrice': _money(line.rate if line.rate else line.taxable_value / quantity),
            'TotAmt': _money(line.taxable_value), 'AssAmt': _money(line.taxable_value), 'GstRt': float(line.gst_rate),
            'IgstAmt': _money(igst), 'CgstAmt': _money(cgst), 'SgstAmt': _money(sgst), 'TotItemVal': _money(line.taxable_value + tax),
        })
    if problems:
        raise EInvoiceError("To register it, add " + "; ".join(dict.fromkeys(problems)) + ".")
    day = _date(document)
    return {
        'Version': '1.1', '_fy': (lambda fy: f"20{fy[:2]}-{fy[2:]}")(financial_year(day)),
        'TranDtls': {'TaxSch': 'GST', 'SupTyp': 'B2B', 'RegRev': 'N', 'IgstOnIntra': 'N'},
        'DocDtls': {'Typ': _doc_type(document), 'No': document.number, 'Dt': day.strftime('%d/%m/%Y')},
        'SellerDtls': {'Gstin': company.gstin, 'LglNm': company.legal_name or company.name, 'TrdNm': company.trade_name or None,
                       'Addr1': (line1 or company.legal_name)[:100], 'Loc': (city or '')[:50] or 'NA', 'Pin': int(pin), 'Stcd': state},
        'BuyerDtls': {'Gstin': buyer.gstin, 'LglNm': buyer.legal_name or buyer.name, 'Pos': document.place_of_supply,
                      'Addr1': (b_line1 or buyer.legal_name)[:100], 'Loc': (b_city or '')[:50] or 'NA', 'Pin': int(b_pin), 'Stcd': b_state},
        'ItemList': items,
        'ValDtls': {'AssVal': _money(document.taxable_value), 'CgstVal': _money(document.cgst), 'SgstVal': _money(document.sgst),
                    'IgstVal': _money(document.igst), 'RndOffAmt': _money(document.round_off), 'TotInvVal': _money(document.total)},
    }


@transaction.atomic
def register(membership, document):
    """Registers a posted B2B invoice or sales credit/debit note with the IRP."""
    company = membership.company
    if not erp.includes(company.erp_plan, erp.EINVOICE):
        raise EInvoiceError("E-invoicing is included on the Growth and Pro plans (an add-on on Starter).")
    if not company.einvoice_applicable:
        raise EInvoiceError("Turn on e-invoicing under Settings, Company first (it applies once turnover has crossed ₹5 crore).")
    _check(membership, 'invoice.create')
    document = document.__class__.objects.select_for_update().get(pk=document.pk)
    if document.status != 'posted':
        raise EInvoiceError("Only a posted document can be registered.")
    if document.__class__.__name__ == 'CreditDebitNote' and document.side != 'sales':
        raise EInvoiceError("Notes from suppliers are registered by the supplier.")
    if active_irn(document):
        raise EInvoiceError("This document already has an IRN.")
    body = payload(document)
    result = _call(get_provider().generate_irn, body)
    field = 'note' if document.__class__.__name__ == 'CreditDebitNote' else 'sales_invoice'
    return EInvoice.objects.create(
        company=company, **{field: document}, document_type=body['DocDtls']['Typ'], document_number=document.number,
        irn=result['irn'], ack_number=result['ack_number'], ack_date=result['ack_date'], signed_qr=result['signed_qr'],
        provider=get_provider().name, request={k: v for k, v in body.items() if not k.startswith('_')}, response=result['raw'],
        created_by=membership.user,
    )


@transaction.atomic
def cancel_irn(membership, einvoice, reason, remark):
    einvoice = EInvoice.objects.select_for_update().get(pk=einvoice.pk)
    _check(membership, 'invoice.create')
    if einvoice.status != EInvoice.Status.ACTIVE:
        raise EInvoiceError("This IRN is already cancelled.")
    if timezone.now() - einvoice.ack_date > CANCEL_WINDOW:
        raise EInvoiceError("An IRN can only be cancelled within 24 hours. Issue a credit note instead.")
    if reason not in EInvoice.CancelReason.values:
        raise EInvoiceError("Choose why it's being cancelled.")
    remark = (remark or '').strip()[:100]
    if not remark:
        raise EInvoiceError("Add a remark for the portal.")
    result = _call(get_provider().cancel_irn, einvoice.irn, reason, remark)
    einvoice.status, einvoice.cancel_reason, einvoice.cancel_remark = EInvoice.Status.CANCELLED, reason, remark
    einvoice.cancelled_at = result['cancelled_at']
    einvoice.response = {**einvoice.response, 'cancel': result['raw']}
    einvoice.save()
    return einvoice


# --- E-way bills -------------------------------------------------------------------------------------

def validity_days(distance_km, vehicle_type=EwayBill.VehicleType.REGULAR):
    per_day = 20 if vehicle_type == EwayBill.VehicleType.ODC else 200
    return max(1, math.ceil(distance_km / per_day))


def valid_until(start, distance_km, vehicle_type=EwayBill.VehicleType.REGULAR):
    """Midnight at the end of the last day (each day runs to midnight after the day it starts)."""
    last = timezone.localtime(start).date() + timedelta(days=validity_days(distance_km, vehicle_type))
    return timezone.make_aware(datetime.combine(last, time(23, 59, 59)))


def active_ewb(document):
    field = 'job_work_challan' if document.__class__.__name__ == 'JobWorkChallan' else 'dispatch'
    return EwayBill.objects.filter(**{field: document}, status=EwayBill.Status.ACTIVE).first()


def consignment(document):
    """(lines [(item, hsn, quantity, unit, taxable value, GST rate)], from company, from warehouse, to company,
    to address, interstate, document date, supply sub-type) for a dispatch or job work challan."""
    if document.__class__.__name__ == 'JobWorkChallan':
        lines = [(l.item, l.item.hsn_code, l.quantity, l.item.unit, (l.quantity * l.rate).quantize(CENT), ZERO)
                 for l in document.lines.select_related('item')]
        to = document.job_worker
        interstate = (to.state_code or (to.gstin or '')[:2]) != (document.company.state_code or (document.company.gstin or '')[:2])
        return lines, document.from_warehouse, to, interstate, document.challan_date, '4'
    order = document.order
    lines = [(l.order_line.item, l.order_line.item.hsn_code, l.quantity, l.order_line.unit,
              (l.quantity * l.order_line.rate).quantize(CENT), l.order_line.gst_rate)
             for l in document.lines.select_related('order_line__item')]
    return lines, document.warehouse, order.customer, order.is_interstate, document.dispatch_date, '1'


def needed(document):
    """(needed, value with tax, why) for a dispatch or job work challan."""
    lines, _w, _to, interstate, _d, sub_type = consignment(document)
    value = sum((v + (v * rate / 100).quantize(CENT) for _i, _h, _q, _u, v, rate in lines), ZERO)
    if sub_type == '4' and interstate:
        return True, value, "Inter-state job work needs an e-way bill whatever its value."
    if value > EWB_THRESHOLD:
        return True, value, "The goods are worth more than ₹50,000."
    return False, value, "Under ₹50,000: not needed (unless your state's limit is lower)."


def _transport(data):
    mode = data.get('transport_mode') or EwayBill.Mode.ROAD
    if mode not in EwayBill.Mode.values:
        raise EInvoiceError("Choose how the goods are going.")
    vehicle = re.sub(r'[\s-]', '', (data.get('vehicle_number') or '')).upper()
    if mode == EwayBill.Mode.ROAD and vehicle and not VEHICLE.match(vehicle):
        raise EInvoiceError("A vehicle number looks like TN37AB1234.")
    transporter = (data.get('transporter_id') or '').strip().upper()
    if transporter and not TRANSPORTER_ID.match(transporter):
        raise EInvoiceError("A transporter ID is their 15-character GSTIN or TRANSIN.")
    if mode == EwayBill.Mode.ROAD and not vehicle and not transporter:
        raise EInvoiceError("Enter the vehicle number, or the transporter's ID so they can add it.")
    if mode != EwayBill.Mode.ROAD and not (data.get('transport_document') or '').strip():
        raise EInvoiceError("Enter the railway receipt, airway bill or bill of lading number.")
    vehicle_type = data.get('vehicle_type') or EwayBill.VehicleType.REGULAR
    if vehicle_type not in EwayBill.VehicleType.values:
        raise EInvoiceError("Choose the vehicle type.")
    return mode, vehicle, transporter, vehicle_type


@transaction.atomic
def generate_ewb(membership, document, data):
    """An e-way bill for a dispatch or a job work challan."""
    company = membership.company
    is_jw = document.__class__.__name__ == 'JobWorkChallan'
    _check(membership, 'job_work.manage' if is_jw else 'dispatch.post')
    document = document.__class__.objects.select_for_update().get(pk=document.pk)
    if is_jw and document.status == 'cancelled':
        raise EInvoiceError("This challan is cancelled.")
    if active_ewb(document):
        raise EInvoiceError("This consignment already has an e-way bill.")
    lines, warehouse, to, interstate, day, sub_type = consignment(document)
    if not lines:
        raise EInvoiceError("There's nothing on it to move.")
    try:
        distance = int(data.get('distance_km') or 0)
    except (TypeError, ValueError):
        distance = 0
    if not 1 <= distance <= MAX_DISTANCE:
        raise EInvoiceError(f"Enter the distance by road, 1 to {MAX_DISTANCE} km.")
    mode, vehicle, transporter, vehicle_type = _transport(data)
    f_line, f_city, f_pin, f_state = _place(company, warehouse)
    t_line, t_city, t_pin, t_state = _place(to)
    if not f_pin or not t_pin:
        raise EInvoiceError("Both ends need a 6-digit PIN code: the warehouse's (or your company's) and the receiver's.")
    if not company.gstin:
        raise EInvoiceError("Your GSTIN is needed for an e-way bill.")
    _needed, value, _why = needed(document)
    taxable = sum((v for *_rest, v, _r in lines), ZERO)
    tax = value - taxable
    body = {
        'supplyType': 'O', 'subSupplyType': sub_type, 'docType': 'CHL', 'docNo': document.number, 'docDate': day.strftime('%d/%m/%Y'),
        'fromGstin': company.gstin, 'fromTrdName': company.legal_name or company.name, 'fromAddr1': (f_line or '')[:120],
        'fromPlace': f_city or '', 'fromPincode': int(f_pin), 'fromStateCode': int(f_state or 0), 'actFromStateCode': int(f_state or 0),
        'toGstin': to.gstin or 'URP', 'toTrdName': to.legal_name or to.name, 'toAddr1': (t_line or '')[:120], 'toPlace': t_city or '',
        'toPincode': int(t_pin), 'toStateCode': int(t_state or 0), 'actToStateCode': int(t_state or 0),
        'transactionType': 1, 'totalValue': _money(taxable),
        'cgstValue': 0 if interstate else _money(tax / 2), 'sgstValue': 0 if interstate else _money(tax - (tax / 2).quantize(CENT)),
        'igstValue': _money(tax) if interstate else 0, 'totInvValue': _money(value),
        'transMode': mode, 'transDistance': str(distance), 'transporterId': transporter, 'transporterName': (data.get('transporter_name') or '')[:100],
        'transDocNo': (data.get('transport_document') or '')[:15],
        'transDocDate': data['transport_document_date'].strftime('%d/%m/%Y') if data.get('transport_document_date') else '',
        'vehicleNo': vehicle, 'vehicleType': vehicle_type,
        'itemList': [{'productName': item.name[:100], 'hsnCode': hsn, 'quantity': float(q), 'qtyUnit': unit, 'taxableAmount': _money(v),
                      'igstRate' if interstate else 'cgstRate': float(rate) if interstate else float(rate / 2),
                      **({} if interstate else {'sgstRate': float(rate / 2)})}
                     for item, hsn, q, unit, v, rate in lines],
    }
    result = _call(get_provider().generate_ewb, body)
    ewb = EwayBill.objects.create(
        company=company, **{('job_work_challan' if is_jw else 'dispatch'): document}, number=result['number'],
        generated_at=result['generated_at'],
        valid_until=valid_until(result['generated_at'], distance, vehicle_type) if vehicle or mode != EwayBill.Mode.ROAD else None,
        document_type='CHL', document_number=document.number, supply_sub_type=sub_type, value=value, from_pincode=f_pin,
        to_pincode=t_pin, distance_km=distance, transport_mode=mode, vehicle_type=vehicle_type, vehicle_number=vehicle,
        transport_document=body['transDocNo'], transport_document_date=data.get('transport_document_date'),
        transporter_id=transporter, transporter_name=body['transporterName'], provider=get_provider().name, request=body,
        response=result['raw'], created_by=membership.user,
    )
    if vehicle:
        EwayBillVehicle.objects.create(eway_bill=ewb, vehicle_number=vehicle, from_place=f_city or '', updated_at=result['generated_at'],
                                       created_by=membership.user)
        if hasattr(document, 'vehicle_number') and not document.vehicle_number:
            document.vehicle_number = vehicle
            document.save(update_fields=['vehicle_number'])
    return ewb


def _ewb_permission(membership, ewb):
    _check(membership, 'job_work.manage' if ewb.job_work_challan_id else 'dispatch.post')


@transaction.atomic
def update_vehicle(membership, ewb, data):
    """Part B: the vehicle (first entry, or a change on the way)."""
    ewb = EwayBill.objects.select_for_update().get(pk=ewb.pk)
    _ewb_permission(membership, ewb)
    if ewb.status != EwayBill.Status.ACTIVE:
        raise EInvoiceError("This e-way bill is cancelled.")
    now = timezone.now()
    if ewb.valid_until and ewb.valid_until < now:
        raise EInvoiceError("This e-way bill has expired.")
    vehicle = re.sub(r'[\s-]', '', (data.get('vehicle_number') or '')).upper()
    if not VEHICLE.match(vehicle):
        raise EInvoiceError("A vehicle number looks like TN37AB1234.")
    first = not ewb.vehicles.exists()
    reason = EwayBillVehicle.Reason.FIRST if first else (data.get('reason') or '')
    if reason not in EwayBillVehicle.Reason.values:
        raise EInvoiceError("Say why the vehicle changed: breakdown, transshipment or other.")
    place = (data.get('from_place') or '').strip()[:50]
    if not first and not place:
        raise EInvoiceError("Say where the goods are being moved to the new vehicle.")
    result = _call(get_provider().update_vehicle, ewb.number, {'vehicleNo': vehicle, 'fromPlace': place, 'reasonCode': reason})
    EwayBillVehicle.objects.create(eway_bill=ewb, vehicle_number=vehicle, from_place=place, reason=reason,
                                   remark=(data.get('remark') or '').strip()[:50], updated_at=result['updated_at'], created_by=membership.user)
    ewb.vehicle_number = vehicle
    if ewb.valid_until is None:  # validity starts with the first Part B
        ewb.valid_until = valid_until(result['updated_at'], ewb.distance_km, ewb.vehicle_type)
    ewb.save(update_fields=['vehicle_number', 'valid_until'])
    return ewb


@transaction.atomic
def cancel_ewb(membership, ewb, reason, remark):
    ewb = EwayBill.objects.select_for_update().get(pk=ewb.pk)
    _ewb_permission(membership, ewb)
    if ewb.status != EwayBill.Status.ACTIVE:
        raise EInvoiceError("This e-way bill is already cancelled.")
    if timezone.now() - ewb.generated_at > CANCEL_WINDOW:
        raise EInvoiceError("An e-way bill can only be cancelled within 24 hours of making it.")
    if reason not in EwayBill.CancelReason.values:
        raise EInvoiceError("Choose why it's being cancelled.")
    remark = (remark or '').strip()[:100]
    if not remark:
        raise EInvoiceError("Add a remark for the portal.")
    result = _call(get_provider().cancel_ewb, ewb.number, reason, remark)
    ewb.status, ewb.cancel_reason, ewb.cancel_remark, ewb.cancelled_at = EwayBill.Status.CANCELLED, reason, remark, result['cancelled_at']
    ewb.response = {**ewb.response, 'cancel': result['raw']}
    ewb.save()
    return ewb
