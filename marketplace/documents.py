"""Purchase orders (issued at award) and GST tax invoices (issued at dispatch).

Everything printed on a document is copied into the OrderDocument row when
it's created, as the Company model's notes require: a later profile edit or
GST re-verification must never change an issued document. The PDF is then
rendered from that copy, so it can always be rebuilt identically.
"""
import io
import logging
import re
from datetime import date
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import DocumentSequence, OrderDocument

logger = logging.getLogger(__name__)

GSTIN_PATTERN = re.compile(r'^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$')
CENT = Decimal('0.01')

# GST state codes, for "place of supply" and working out intra- vs
# inter-state supply when a party has no GSTIN.
STATE_CODES = {
    'jammu and kashmir': '01', 'himachal pradesh': '02', 'punjab': '03', 'chandigarh': '04', 'uttarakhand': '05',
    'haryana': '06', 'delhi': '07', 'rajasthan': '08', 'uttar pradesh': '09', 'bihar': '10', 'sikkim': '11',
    'arunachal pradesh': '12', 'nagaland': '13', 'manipur': '14', 'mizoram': '15', 'tripura': '16', 'meghalaya': '17',
    'assam': '18', 'west bengal': '19', 'jharkhand': '20', 'odisha': '21', 'chhattisgarh': '22', 'madhya pradesh': '23',
    'gujarat': '24', 'dadra and nagar haveli and daman and diu': '26', 'maharashtra': '27', 'karnataka': '29',
    'goa': '30', 'lakshadweep': '31', 'kerala': '32', 'tamil nadu': '33', 'puducherry': '34',
    'andaman and nicobar islands': '35', 'telangana': '36', 'andhra pradesh': '37', 'ladakh': '38',
}
STATE_ALIASES = {'mh': 'maharashtra', 'ka': 'karnataka', 'tn': 'tamil nadu', 'dl': 'delhi', 'new delhi': 'delhi',
                 'gj': 'gujarat', 'up': 'uttar pradesh', 'ts': 'telangana', 'ap': 'andhra pradesh', 'wb': 'west bengal',
                 'orissa': 'odisha', 'pondicherry': 'puducherry'}
STATE_NAMES = {code: name.title() for name, code in STATE_CODES.items()}


def _state_code(state):
    key = re.sub(r'[^a-z ]', '', (state or '').lower()).strip()
    key = STATE_ALIASES.get(key, key).replace(' & ', ' and ')
    return STATE_CODES.get(key)


def valid_gstin(value):
    value = (value or '').strip().upper()
    return value if GSTIN_PATTERN.match(value) else ''


def financial_year(when):
    """Indian financial year label: April 2026 – March 2027 -> "2627"."""
    start = when.year if when.month >= 4 else when.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def _next_number(kind, issuer_key, prefix, when):
    """PO-2627-0001 style: per issuer, per kind, per financial year, no gaps."""
    fy = financial_year(when)
    DocumentSequence.objects.get_or_create(issuer_key=issuer_key, kind=kind, financial_year=fy)
    sequence = DocumentSequence.objects.select_for_update().get(issuer_key=issuer_key, kind=kind, financial_year=fy)
    sequence.last_number += 1
    sequence.save(update_fields=['last_number'])
    return f"{prefix}-{fy}-{sequence.last_number:04d}"


# --- Snapshots ---------------------------------------------------------------

def seller_snapshot(supplier):
    """The manufacturer as registered for GST (their verified Company),
    falling back to their marketplace profile where the Company has gaps."""
    company = supplier.company
    gstin = valid_gstin(company.gstin if company else '')
    state = (company.state if company and company.state else '') or supplier.state
    return {
        'name': (company.legal_name if company and company.legal_name else '') or supplier.companyname or str(supplier),
        'trade_name': (company.trade_name if company else '') or supplier.companyname or '',
        'gstin': gstin,
        'address': (company.registered_address if company and company.registered_address else '') or supplier.address,
        'city': (company.city if company and company.city else '') or supplier.city,
        'state': state,
        'state_code': gstin[:2] if gstin else _state_code(state),
        'pincode': (company.pincode if company else '') or supplier.company_postalcode or '',
        'country': supplier.country,
        'email': supplier.email,
        'phone': supplier.phone,
    }


def buyer_snapshot(customer):
    """The buyer's company from their profile. Their tax ID is kept as
    entered; it's treated as a GSTIN only when it's a valid one."""
    gstin = valid_gstin(customer.VAT_number)
    return {
        'name': customer.Name,
        'gstin': gstin,
        'tax_id': '' if gstin else (customer.VAT_number or ''),
        'address': customer.Address or '',
        'city': customer.city,
        'state': customer.state,
        'state_code': gstin[:2] if gstin else _state_code(customer.state),
        'country': customer.country,
        'email': customer.email,
        'phone': customer.phone,
    }


def _lines(order):
    quote = order.quote
    unit_price = quote.quote_price or Decimal('0')
    lines = []
    for part in order.requirement.requirement_parts.order_by('pk'):
        description = part.part_name + (f" — {part.Part_desc}" if part.Part_desc else '')
        lines.append({
            'description': description,
            'spec': f"{part.get_technology_display()}, {part.get_Material_display()}",
            'quantity': part.quantity,
            'unit_price': str(unit_price),
            'amount': str((unit_price * part.quantity).quantize(CENT)),
        })
    if quote.tooling_cost:
        lines.append({'description': 'Tooling / setup (one-time)', 'spec': '', 'quantity': 1,
                      'unit_price': str(quote.tooling_cost), 'amount': str(quote.tooling_cost.quantize(CENT))})
    return lines


def _tax(seller, buyer, breakdown):
    """Splits the order's GST into CGST + SGST (same state) or IGST
    (different states, or a buyer outside India). Totals match the order."""
    gst = breakdown['gst']
    in_india = (buyer.get('country') or 'India').strip().lower() in ('india', 'in', 'bharat')
    same_state = in_india and seller['state_code'] and seller['state_code'] == buyer['state_code']
    cgst = (gst / 2).quantize(CENT) if same_state else Decimal('0')
    place_code = buyer['state_code'] if in_india else ''
    return {
        'subtotal': str(breakdown['subtotal'].quantize(CENT)),
        'rate': '18',
        'intra_state': bool(same_state),
        'cgst': str(cgst),
        'sgst': str((gst - cgst) if same_state else Decimal('0')),
        'igst': str(Decimal('0') if same_state else gst),
        'gst': str(gst),
        'total': str(breakdown['total'].quantize(CENT)),
        'place_of_supply': (
            f"{STATE_NAMES.get(place_code, buyer['state'])} ({place_code})" if place_code
            else (buyer['state'] if in_india else f"Outside India ({buyer.get('country')})")
        ),
    }


def _references(order):
    quote = order.quote
    return {
        'order': f"ORD-{order.billno}",
        'rfq': f"RFQ-{order.requirement_id}",
        'rfq_title': order.requirement.title,
        'quote': f"Quote #{quote.pk}",
        'payment_terms': quote.get_payment_terms_display() or '',
        'lead_time': f"{quote.lead_time_value} {quote.get_lead_time_unit_display().lower()}" if quote.lead_time_value else '',
        'ship_by': order.ship_by_date.isoformat() if order.ship_by_date else '',
        'valid_until': quote.valid_until.isoformat() if quote.valid_until else '',
    }


def _shipment(order):
    courier = order.courier_other if order.courier == 'other' else order.get_courier_display()
    return {'courier': courier or '', 'tracking_number': order.tracking_number, 'eway_bill_number': order.eway_bill_number}


def _issue(order, kind):
    existing = OrderDocument.objects.filter(order=order, kind=kind).first()
    if existing:
        return existing
    now = timezone.now()
    seller = seller_snapshot(order.supplier)
    buyer = buyer_snapshot(order.customer)
    details = {
        'references': _references(order),
        'lines': _lines(order),
        'tax': _tax(seller, buyer, order.quote.get_breakdown()),
    }
    if kind == OrderDocument.INVOICE:
        details['shipment'] = _shipment(order)
        issuer_key, prefix = f"supplier:{order.supplier_id}", 'INV'
    else:
        issuer_key, prefix = f"buyer:{order.customer_id}", 'PO'
    try:
        with transaction.atomic():
            document = OrderDocument.objects.create(
                order=order, kind=kind, issuer_key=issuer_key, issued_at=now,
                number=_next_number(kind, issuer_key, prefix, timezone.localtime(now)),
                seller=seller, buyer=buyer, details=details,
                currency=order.requirement.quote_currency or 'INR', total=Decimal(details['tax']['total']),
            )
    except IntegrityError:
        # Issued by a concurrent request; its number stands (ours was rolled back with it).
        return OrderDocument.objects.get(order=order, kind=kind)
    logger.info("%s %s issued for order #%s", document.get_kind_display(), document.number, order.billno)
    # Render once the surrounding transaction (e.g. the award) commits, so a
    # rollback can't leave a PDF behind for a document that doesn't exist.
    transaction.on_commit(lambda: ensure_pdf(document))
    return document


def issue_purchase_order(order):
    """The buyer's PO to the manufacturer, issued when the quote is awarded."""
    return _issue(order, OrderDocument.PURCHASE_ORDER)


def issue_invoice(order):
    """The manufacturer's GST tax invoice, issued when the order is dispatched."""
    return _issue(order, OrderDocument.INVOICE)


def ensure_pdf(document):
    """Renders and stores the PDF if it isn't stored yet."""
    if document.pdf:
        return document
    document.pdf.save(f"{document.number}.pdf", ContentFile(render_pdf(document)), save=True)
    return document


# --- Amount in words (Indian numbering) -----------------------------------------

_ONES = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve',
         'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
_TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']


def _words_below_1000(n):
    words = []
    if n >= 100:
        words += [_ONES[n // 100], 'Hundred']
        n %= 100
    if n >= 20:
        words.append(_TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else ''))
    elif n:
        words.append(_ONES[n])
    return words


def amount_in_words(amount, currency='INR'):
    """Decimal('118000.50') -> "Rupees One Lakh Eighteen Thousand and Fifty Paise Only"."""
    amount = Decimal(amount).quantize(CENT)
    whole, fraction = int(amount), int((amount - int(amount)) * 100)
    words = []
    for size, label in ((10**7, 'Crore'), (10**5, 'Lakh'), (1000, 'Thousand')):
        if whole >= size:
            words += _words_below_1000(whole // size) + [label]
            whole %= size
    words += _words_below_1000(whole)
    unit, sub = ('Rupees', 'Paise') if currency == 'INR' else (currency, 'Cents')
    text = f"{unit} {' '.join(words) or 'Zero'}"
    if fraction:
        text += f" and {' '.join(_words_below_1000(fraction))} {sub}"
    return text + ' Only'


# --- PDF ------------------------------------------------------------------------

def _money(value, currency):
    value = Decimal(value)
    if currency == 'INR':
        # Indian grouping; the built-in PDF fonts have no rupee glyph, hence "INR".
        whole, cents = f"{value:.2f}".split('.')
        sign = '-' if whole.startswith('-') else ''
        whole = whole.lstrip('-')
        if len(whole) > 3:
            head, tail = whole[:-3], whole[-3:]
            head = ','.join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
            whole = f"{head},{tail}"
        return f"{sign}{whole}.{cents}"
    return f"{value:,.2f}"


def render_pdf(document):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    navy = colors.HexColor('#11355c')
    grey = colors.HexColor('#6b7280')
    rule = colors.HexColor('#e5e7eb')
    styles = getSampleStyleSheet()
    body = ParagraphStyle('body', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11)
    small = ParagraphStyle('small', parent=body, fontSize=7.5, leading=9.5, textColor=grey)
    label = ParagraphStyle('label', parent=body, fontName='Helvetica-Bold', fontSize=7, leading=9, textColor=grey)
    title = ParagraphStyle('title', parent=body, fontName='Helvetica-Bold', fontSize=16, leading=19, textColor=navy)
    right = ParagraphStyle('right', parent=body, alignment=TA_RIGHT)
    right_bold = ParagraphStyle('right_bold', parent=right, fontName='Helvetica-Bold')

    def esc(text):
        return (str(text or '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    currency = document.currency
    refs = document.details['references']
    tax = document.details['tax']
    is_invoice = document.kind == OrderDocument.INVOICE
    issued = timezone.localtime(document.issued_at)

    def party(heading, data):
        lines = [f"<b>{esc(data['name'])}</b>"]
        if data.get('trade_name') and data['trade_name'] != data['name']:
            lines.append(f"Trading as {esc(data['trade_name'])}")
        address = ', '.join(filter(None, [data.get('address'), data.get('city'), data.get('pincode')]))
        if address:
            lines.append(esc(address))
        state = data.get('state') or ''
        if data.get('state_code'):
            state = f"{STATE_NAMES.get(data['state_code'], state)} (State code {data['state_code']})"
        lines.append(esc(', '.join(filter(None, [state, data.get('country')]))))
        if data.get('gstin'):
            lines.append(f"<b>GSTIN:</b> {esc(data['gstin'])}")
        elif data.get('tax_id'):
            lines.append(f"<b>Tax ID:</b> {esc(data['tax_id'])}")
        else:
            lines.append('<b>GSTIN:</b> Unregistered')
        lines.append(esc(' · '.join(filter(None, [data.get('email'), data.get('phone')]))))
        return [Paragraph(heading.upper(), label), Spacer(1, 2)] + [Paragraph(line, body) for line in lines]

    buffer = io.BytesIO()
    pdf = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
        title=f"{document.get_kind_display()} {document.number}", author='ManufactureHub',
    )
    width = A4[0] - 32 * mm
    story = []

    meta_rows = [
        [Paragraph('NUMBER', label), Paragraph(esc(document.number), right_bold)],
        [Paragraph('DATE', label), Paragraph(issued.strftime('%d %b %Y'), right)],
        [Paragraph('ORDER', label), Paragraph(esc(refs['order']), right)],
        [Paragraph('RFQ', label), Paragraph(esc(refs['rfq']), right)],
    ]
    if is_invoice:
        meta_rows.append([Paragraph('PLACE OF SUPPLY', label), Paragraph(esc(tax['place_of_supply']), right)])
    header = Table(
        [[
            [Paragraph('TAX INVOICE' if is_invoice else 'PURCHASE ORDER', title),
             Paragraph('Original for recipient' if is_invoice else 'Issued through ManufactureHub', small)],
            Table(meta_rows, colWidths=[30 * mm, 48 * mm], style=[('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOTTOMPADDING', (0, 0), (-1, -1), 1), ('TOPPADDING', (0, 0), (-1, -1), 1)]),
        ]],
        colWidths=[width - 80 * mm, 80 * mm],
    )
    header.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    story += [header, Spacer(1, 8 * mm)]

    if is_invoice:
        parties = [party('Supplier', document.seller), party('Bill to / Ship to', document.buyer)]
    else:
        parties = [party('Buyer (issued by)', document.buyer), party('Supplier (issued to)', document.seller)]
    party_table = Table([parties], colWidths=[width / 2, width / 2])
    party_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOX', (0, 0), (-1, -1), 0.6, rule), ('LINEAFTER', (0, 0), (0, 0), 0.6, rule),
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story += [party_table, Spacer(1, 6 * mm)]

    story.append(Paragraph(f"<b>{esc(refs['rfq_title'])}</b> &nbsp; <font color='#6b7280'>{esc(refs['quote'])}</font>", body))
    story.append(Spacer(1, 3 * mm))
    label_right = ParagraphStyle('label_right', parent=label, alignment=TA_RIGHT)
    rows = [[Paragraph('#', label), Paragraph('DESCRIPTION', label)]
            + [Paragraph(h, label_right) for h in ('QTY', f'UNIT PRICE ({currency})', f'AMOUNT ({currency})')]]
    for index, line in enumerate(document.details['lines'], start=1):
        description = [Paragraph(esc(line['description']), body)]
        if line.get('spec'):
            description.append(Paragraph(esc(line['spec']), small))
        rows.append([Paragraph(str(index), body), description, Paragraph(str(line['quantity']), right),
                     Paragraph(_money(line['unit_price'], currency), right), Paragraph(_money(line['amount'], currency), right)])
    items = Table(rows, colWidths=[8 * mm, width - 98 * mm, 18 * mm, 36 * mm, 36 * mm], repeatRows=1)
    items.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, 0), 0.8, navy), ('LINEBELOW', (0, 1), (-1, -1), 0.4, rule),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story += [items, Spacer(1, 4 * mm)]

    totals = [['Taxable value', _money(tax['subtotal'], currency)]]
    if tax['intra_state']:
        totals += [['CGST @ 9%', _money(tax['cgst'], currency)], ['SGST @ 9%', _money(tax['sgst'], currency)]]
    else:
        totals += [['IGST @ 18%', _money(tax['igst'], currency)]]
    totals.append([f"Total ({currency})", _money(tax['total'], currency)])
    totals_table = Table(
        [[Paragraph(esc(a), right_bold if i == len(totals) - 1 else right), Paragraph(esc(b), right_bold if i == len(totals) - 1 else right)]
         for i, (a, b) in enumerate(totals)],
        colWidths=[40 * mm, 36 * mm], hAlign='RIGHT',
    )
    totals_table.setStyle(TableStyle([('LINEABOVE', (0, -1), (-1, -1), 0.8, navy), ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))
    story += [totals_table, Spacer(1, 2 * mm)]
    story.append(Paragraph(f"<b>Amount in words:</b> {esc(amount_in_words(tax['total'], currency))}", body))
    story.append(Spacer(1, 6 * mm))

    terms = []
    if refs.get('payment_terms'):
        terms.append(('Payment terms', refs['payment_terms']))
    if is_invoice:
        shipment = document.details.get('shipment', {})
        for key, name in (('courier', 'Courier'), ('tracking_number', 'Tracking number'), ('eway_bill_number', 'E-way bill')):
            if shipment.get(key):
                terms.append((name, shipment[key]))
    else:
        if refs.get('lead_time'):
            terms.append(('Lead time', refs['lead_time']))
        if refs.get('ship_by'):
            terms.append(('Ship by', date.fromisoformat(refs['ship_by']).strftime('%d %b %Y')))
        terms.append(('Delivery to', ', '.join(filter(None, [document.buyer.get('address'), document.buyer.get('city'), document.buyer.get('state')]))))
    if terms:
        terms_table = Table([[Paragraph(esc(a).upper(), label), Paragraph(esc(b), body)] for a, b in terms], colWidths=[34 * mm, width - 34 * mm])
        terms_table.setStyle(TableStyle([('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
        story += [terms_table, Spacer(1, 8 * mm)]

    if is_invoice:
        story.append(Paragraph(f"For <b>{esc(document.seller['name'])}</b>", body))
        story.append(Spacer(1, 10 * mm))
        story.append(Paragraph('Authorised signatory', small))
        story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        'This is a computer-generated document issued through ManufactureHub. Party details are as recorded on the date of issue.',
        small,
    ))
    pdf.build(story)
    return buffer.getvalue()
