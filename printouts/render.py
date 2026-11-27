"""PDFs of a tax invoice, a delivery challan and a purchase order.

What a GST tax invoice has to show (CGST Rules, rule 46) and a delivery
challan (rule 55) is all here: both parties' names, addresses and GSTINs,
a number and date, HSN codes, quantities and units, taxable value, the
rate and amount of CGST/SGST or IGST separately, the place of supply with
its state, whether tax is on reverse charge, and a signature. Invoices add
an HSN-wise tax summary, the total in words and bank details.

The built-in PDF fonts have no rupee sign, so amounts are headed "INR".
"""
import io
from decimal import Decimal

from django.utils import timezone

from gst.services.providers.base import STATE_NAMES
from marketplace.documents import _money, amount_in_words

COPIES = ['Original for recipient', 'Duplicate for transporter', 'Triplicate for supplier']


def _esc(text):
    return str(text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br/>')


def _qty(value):
    value = Decimal(value)
    return f"{value.normalize():f}" if value == value.to_integral() else f"{value:.3f}".rstrip('0').rstrip('.')


def _state(code):
    return f"{STATE_NAMES.get(code, '')} ({code})" if code else ''


def _state_code(company):
    """The saved GST state code, else the GSTIN's first two digits (which are it)."""
    return (company.state_code or (company.gstin or '')[:2])[:2]


class _Kit:
    """Styles and building blocks shared by every document."""

    def __init__(self):
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm

        self.colors, self.mm, self.A4 = colors, mm, A4
        self.navy = colors.HexColor('#11355c')
        self.grey = colors.HexColor('#6b7280')
        self.rule = colors.HexColor('#d1d5db')
        base = getSampleStyleSheet()['Normal']
        self.body = ParagraphStyle('body', parent=base, fontName='Helvetica', fontSize=8.5, leading=11)
        self.bold = ParagraphStyle('bold', parent=self.body, fontName='Helvetica-Bold')
        self.small = ParagraphStyle('small', parent=self.body, fontSize=7.5, leading=9.5, textColor=self.grey)
        self.label = ParagraphStyle('label', parent=self.body, fontName='Helvetica-Bold', fontSize=7, leading=9, textColor=self.grey)
        self.title = ParagraphStyle('title', parent=self.body, fontName='Helvetica-Bold', fontSize=15, leading=18, textColor=self.navy, alignment=TA_RIGHT)
        self.company = ParagraphStyle('company', parent=self.body, fontName='Helvetica-Bold', fontSize=12, leading=15, textColor=self.navy)
        self.right = ParagraphStyle('right', parent=self.body, alignment=TA_RIGHT)
        self.right_bold = ParagraphStyle('right_bold', parent=self.right, fontName='Helvetica-Bold')
        self.label_right = ParagraphStyle('label_right', parent=self.label, alignment=TA_RIGHT)
        self.center_small = ParagraphStyle('center_small', parent=self.small, alignment=TA_CENTER)
        self.width = A4[0] - 28 * mm

    def P(self, text, style=None):
        from reportlab.platypus import Paragraph
        return Paragraph(text, style or self.body)

    def table(self, rows, widths, style=(), repeat=0):
        from reportlab.platypus import Table, TableStyle
        t = Table(rows, colWidths=widths, repeatRows=repeat)
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 4),
                               ('RIGHTPADDING', (0, 0), (-1, -1), 4), ('TOPPADDING', (0, 0), (-1, -1), 3),
                               ('BOTTOMPADDING', (0, 0), (-1, -1), 3), *style]))
        return t

    def image(self, field, max_w, max_h):
        """A logo or signature scaled to fit, or None if missing or unreadable."""
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import Image
        if not field:
            return None
        try:
            with field.open('rb') as f:
                data = io.BytesIO(f.read())
            w, h = ImageReader(data).getSize()
            data.seek(0)
            scale = min(max_w / w, max_h / h, 1)
            return Image(data, width=w * scale, height=h * scale)
        except Exception:
            return None

    def header(self, company, title, subtitle, meta):
        """Logo and company on the left; the document's title and numbers on the right."""
        mm = self.mm
        logo = self.image(company.logo, 34 * mm, 18 * mm)
        who = [self.P(_esc(company.legal_name or company.name), self.company)]
        address = ', '.join(p for p in (company.principal_address,) if p)
        if address:
            who.append(self.P(_esc(address), self.small))
        state = _state(_state_code(company))
        who.append(self.P(f"GSTIN <b>{_esc(company.gstin or 'Unregistered')}</b>" + (f" · {_esc(state)}" if state else ''), self.small))
        left = self.table([[logo, who]], [36 * mm, self.width - 36 * mm - 78 * mm]) if logo else who
        meta_rows = [[self.P(k.upper(), self.label), self.P(_esc(v), self.right_bold if i == 0 else self.right)]
                     for i, (k, v) in enumerate(meta)]
        right = [self.P(title, self.title), self.P(_esc(subtitle), ParagraphStyle_right(self.small)),
                 self.table(meta_rows, [30 * mm, 48 * mm])]
        return self.table([[left, right]], [self.width - 78 * mm, 78 * mm],
                          [('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                           ('LINEBELOW', (0, 0), (-1, 0), 1, self.navy), ('BOTTOMPADDING', (0, 0), (-1, 0), 6)])

    def party(self, heading, name, address, gstin, state_code, extra=()):
        lines = [self.P(heading.upper(), self.label), self.P(f"<b>{_esc(name)}</b>")]
        if address:
            lines.append(self.P(_esc(address)))
        if state_code:
            lines.append(self.P(f"State: {_esc(_state(state_code))}"))
        lines.append(self.P(f"GSTIN: <b>{_esc(gstin or 'Unregistered')}</b>"))
        lines += [self.P(_esc(e), self.small) for e in extra if e]
        return lines

    def two_boxes(self, left, right):
        return self.table([[left, right]], [self.width / 2, self.width / 2],
                          [('BOX', (0, 0), (-1, -1), 0.6, self.rule), ('LINEAFTER', (0, 0), (0, 0), 0.6, self.rule),
                           ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)])

    def totals(self, doc, show_round_off=True):
        rows = [('Taxable value', doc.taxable_value)]
        if doc.is_interstate:
            rows.append(('IGST', doc.igst))
        else:
            rows += [('CGST', doc.cgst), ('SGST', doc.sgst)]
        if show_round_off and doc.round_off:
            rows.append(('Round off', doc.round_off))
        rows.append(('Total (INR)', doc.total))
        last = len(rows) - 1
        t = self.table([[self.P(_esc(a), self.right_bold if i == last else self.right),
                         self.P(_money(b, 'INR'), self.right_bold if i == last else self.right)] for i, (a, b) in enumerate(rows)],
                       [50 * self.mm, 34 * self.mm], [('LINEABOVE', (0, last), (-1, last), 0.8, self.navy)])
        t.hAlign = 'RIGHT'
        return t

    def signature(self, company, for_label):
        mm = self.mm
        sign = self.image(company.signature, 45 * mm, 16 * mm)
        cell = [self.P(f"For <b>{_esc(company.legal_name or company.name)}</b>", self.right)]
        from reportlab.platypus import Spacer
        cell.append(sign if sign else Spacer(1, 14 * mm))
        if sign:
            sign.hAlign = 'RIGHT'
        cell.append(self.P(_esc(company.signatory_name or for_label), self.right))
        cell.append(self.P('Authorised signatory', self.label_right))
        return cell


def ParagraphStyle_right(style):
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    return ParagraphStyle(style.name + '_r', parent=style, alignment=TA_RIGHT)


def _build(kit, title, pages, watermark=''):
    """Renders one or more page sets (one per copy) into a PDF."""
    from reportlab.platypus import PageBreak, SimpleDocTemplate

    buffer = io.BytesIO()
    mm = kit.mm

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(kit.grey)
        canvas.drawString(14 * mm, 9 * mm, f"{title} · page {doc.page}")
        canvas.drawRightString(kit.A4[0] - 14 * mm, 9 * mm, "Generated with MakeSetu")
        if watermark:
            canvas.setFont('Helvetica-Bold', 70)
            canvas.setFillColorRGB(0.85, 0.2, 0.2, alpha=0.12)
            canvas.translate(kit.A4[0] / 2, kit.A4[1] / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, watermark)
        canvas.restoreState()

    pdf = SimpleDocTemplate(buffer, pagesize=kit.A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm,
                            bottomMargin=16 * mm, title=title, author='MakeSetu')
    story = []
    for i, page in enumerate(pages):
        if i:
            story.append(PageBreak())
        story += page
    pdf.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buffer.getvalue()


# --- Tax invoice ---------------------------------------------------------------------------

def tax_invoice(invoice, copies=1):
    """A GST tax invoice. `copies` 3 adds the duplicate and triplicate."""
    from reportlab.platypus import Spacer

    kit = _Kit()
    mm = kit.mm
    seller, customer, order = invoice.company, invoice.customer, invoice.order
    lines = list(invoice.lines.select_related('item'))
    draft = invoice.status == 'draft'
    number = invoice.number or 'DRAFT'
    bank = seller.bank_accounts.filter(is_default=True).first() or seller.bank_accounts.first()
    from einvoice.services import active_irn
    irn = active_irn(invoice) if invoice.pk else None

    def page(copy_label):
        story = [kit.header(seller, 'TAX INVOICE', copy_label, [
            ('Invoice no.', number), ('Invoice date', invoice.invoice_date.strftime('%d %b %Y')),
            ('Place of supply', _state(invoice.place_of_supply)), ('Reverse charge', 'No'),
            ('Due date', invoice.due_date.strftime('%d %b %Y') if invoice.due_date else '—'),
        ]), Spacer(1, 4 * mm)]
        if irn is not None:
            story += [_irn_block(kit, irn), Spacer(1, 3 * mm)]
        bill_to = kit.party('Bill to', customer.legal_name or customer.name, customer.principal_address, customer.gstin,
                            _state_code(customer),
                            [f"Your order: {order.customer_po_number}" if order.customer_po_number else '', f"Our order: {order.number}"])
        ship_to = kit.party('Ship to', customer.legal_name or customer.name, order.ship_to or customer.principal_address,
                            customer.gstin, invoice.place_of_supply)
        story += [kit.two_boxes(bill_to, ship_to), Spacer(1, 4 * mm)]

        tax_heads = ['IGST'] if invoice.is_interstate else ['CGST', 'SGST']
        head = [kit.P(h, kit.label if i < 2 else kit.label_right) for i, h in enumerate(
            ['#', 'DESCRIPTION', 'HSN/SAC', 'QTY', 'RATE', 'TAXABLE', 'GST %', 'TAX'])]
        rows = [head]
        for i, l in enumerate(lines, start=1):
            rows.append([kit.P(str(i)), [kit.P(_esc(l.item.name)), kit.P(_esc(l.item.code), kit.small)],
                         kit.P(_esc(l.hsn_code), kit.right), kit.P(f"{_qty(l.quantity)} {l.unit}", kit.right),
                         kit.P(_money(l.rate, 'INR'), kit.right), kit.P(_money(l.taxable_value, 'INR'), kit.right),
                         kit.P(f"{l.gst_rate.normalize():f}%", kit.right), kit.P(_money(l.tax_amount, 'INR'), kit.right)])
        story += [kit.table(rows, [7 * mm, kit.width - 133 * mm, 17 * mm, 22 * mm, 22 * mm, 26 * mm, 14 * mm, 25 * mm],
                            [('LINEBELOW', (0, 0), (-1, 0), 0.8, kit.navy), ('LINEBELOW', (0, 1), (-1, -1), 0.3, kit.rule)], repeat=1),
                  Spacer(1, 3 * mm), kit.totals(invoice), Spacer(1, 2 * mm),
                  kit.P(f"<b>Amount in words:</b> {_esc(amount_in_words(invoice.total))}"), Spacer(1, 4 * mm)]

        # HSN-wise summary, as GST returns report it.
        summary = {}
        for l in lines:
            key = (l.hsn_code, l.gst_rate)
            taxable, tax = summary.get(key, (Decimal('0'), Decimal('0')))
            summary[key] = (taxable + l.taxable_value, tax + l.tax_amount)
        s_head = ['HSN/SAC', 'TAXABLE VALUE'] + [f'{h} RATE' for h in tax_heads] + [f'{h} AMOUNT' for h in tax_heads] + ['TOTAL TAX']
        s_rows = [[kit.P(h, kit.label if i == 0 else kit.label_right) for i, h in enumerate(s_head)]]
        for (hsn, rate), (taxable, tax) in sorted(summary.items()):
            if invoice.is_interstate:
                split, rates = [tax], [rate]
            else:
                half = (tax / 2).quantize(Decimal('0.01'))
                split, rates = [half, tax - half], [rate / 2, rate / 2]
            s_rows.append([kit.P(_esc(hsn or '—')), kit.P(_money(taxable, 'INR'), kit.right)]
                          + [kit.P(f"{r.normalize():f}%", kit.right) for r in rates]
                          + [kit.P(_money(a, 'INR'), kit.right) for a in split] + [kit.P(_money(tax, 'INR'), kit.right)])
        n = len(s_head)
        story += [kit.P('HSN-WISE TAX SUMMARY', kit.label),
                  kit.table(s_rows, [kit.width / n] * n, [('LINEBELOW', (0, 0), (-1, 0), 0.6, kit.rule), ('BOX', (0, 0), (-1, -1), 0.4, kit.rule)]),
                  Spacer(1, 5 * mm)]

        left = []
        if bank:
            left += [kit.P('BANK DETAILS', kit.label),
                     kit.P(f"{_esc(bank.account_name)}<br/>{_esc(bank.bank_name)}{', ' + _esc(bank.branch) if bank.branch else ''}<br/>"
                           f"A/c no. <b>{_esc(bank.account_number)}</b> · IFSC <b>{_esc(bank.ifsc)}</b>"), Spacer(1, 3 * mm)]
        if seller.invoice_terms:
            left += [kit.P('TERMS', kit.label), kit.P(_esc(seller.invoice_terms), kit.small)]
        left += [Spacer(1, 2 * mm), kit.P("Certified that the particulars given above are true and correct.", kit.small)]
        story.append(kit.table([[left, kit.signature(seller, 'Authorised signatory')]], [kit.width * 0.6, kit.width * 0.4]))
        if invoice.status == 'cancelled':
            story += [Spacer(1, 3 * mm), kit.P(f"<b>CANCELLED</b> on {timezone.localtime(invoice.cancelled_at):%d %b %Y}: {_esc(invoice.cancel_reason)}")]
        return story

    labels = COPIES[:max(1, min(copies, 3))] if not draft else ['Draft — not a valid tax invoice']
    watermark = 'DRAFT' if draft else ('CANCELLED' if invoice.status == 'cancelled' else '')
    return _build(kit, f"Tax invoice {number}", [page(label) for label in labels], watermark)


# --- Delivery challan ---------------------------------------------------------------------

def delivery_challan(dispatch):
    from reportlab.platypus import Spacer

    kit = _Kit()
    mm = kit.mm
    order, seller = dispatch.order, dispatch.company
    customer = order.customer
    warehouse = dispatch.warehouse
    address = warehouse.address
    from einvoice.services import active_ewb
    ewb = active_ewb(dispatch)
    story = [kit.header(seller, 'DELIVERY CHALLAN', 'For supply of goods', [
        ('Challan no.', dispatch.number), ('Date', dispatch.dispatch_date.strftime('%d %b %Y')),
        ('Place of supply', _state(order.place_of_supply)), ('Against order', order.number),
        ('Vehicle', (ewb.vehicle_number if ewb and ewb.vehicle_number else dispatch.vehicle_number) or '—'),
    ] + ([('E-way bill', ewb.number)] + ([('Valid until', timezone.localtime(ewb.valid_until).strftime('%d %b %Y %H:%M'))] if ewb.valid_until else [])
         if ewb else [])), Spacer(1, 4 * mm)]
    consignor = kit.party('Consignor (dispatched from)', seller.legal_name or seller.name,
                          ', '.join(p for p in (warehouse.name, address.line1 if address else '', address.city if address else '',
                                                address.pincode if address else '') if p),
                          seller.gstin, (address.state_code if address else '') or _state_code(seller))
    consignee = kit.party('Consignee (deliver to)', customer.legal_name or customer.name, order.ship_to or customer.principal_address,
                          customer.gstin, order.place_of_supply,
                          [f"Their order: {order.customer_po_number}" if order.customer_po_number else '',
                           f"Transporter: {dispatch.transporter}" if dispatch.transporter else ''])
    story += [kit.two_boxes(consignor, consignee), Spacer(1, 4 * mm)]
    from inventory import batches

    sent = batches.by_document(dispatch.company, [dispatch.number], -1)
    head = [kit.P(h, kit.label if i < 2 else kit.label_right) for i, h in enumerate(['#', 'DESCRIPTION', 'HSN', 'QTY', 'RATE', 'VALUE', 'GST %'])]
    rows, value = [head], Decimal('0')
    for i, dl in enumerate(dispatch.lines.select_related('order_line__item'), start=1):
        l = dl.order_line
        amount = (dl.quantity * l.rate).quantize(Decimal('0.01'))
        value += amount
        description = [kit.P(_esc(l.item.name)), kit.P(_esc(l.item.code), kit.small)]
        used = sent.get((dispatch.number, l.item_id))
        if used:
            what = 'Serial no.' if l.item.by_serial else 'Batch'
            description.append(kit.P(f"{what}: {_esc(batches.numbers(used))}", kit.small))
        rows.append([kit.P(str(i)), description, kit.P(_esc(l.item.hsn_code), kit.right),
                     kit.P(f"{_qty(dl.quantity)} {l.unit}", kit.right), kit.P(_money(l.rate, 'INR'), kit.right),
                     kit.P(_money(amount, 'INR'), kit.right), kit.P(f"{l.gst_rate.normalize():f}%", kit.right)])
    rows.append(['', kit.P('<b>Value of goods (before GST)</b>'), '', '', '', kit.P(_money(value, 'INR'), kit.right_bold), ''])
    story += [kit.table(rows, [7 * mm, kit.width - 113 * mm, 18 * mm, 24 * mm, 24 * mm, 26 * mm, 14 * mm],
                        [('LINEBELOW', (0, 0), (-1, 0), 0.8, kit.navy), ('LINEBELOW', (0, 1), (-1, -2), 0.3, kit.rule),
                         ('LINEABOVE', (0, -1), (-1, -1), 0.8, kit.navy)], repeat=1),
              Spacer(1, 3 * mm),
              kit.P("Goods sent against the order above. The tax invoice is issued separately.", kit.small)]
    if dispatch.notes:
        story += [Spacer(1, 2 * mm), kit.P(_esc(dispatch.notes))]
    received = [kit.P("Received in good condition", kit.label), Spacer(1, 14 * mm), kit.P("Receiver's signature and stamp", kit.small)]
    story += [Spacer(1, 8 * mm), kit.table([[received, kit.signature(seller, 'Authorised signatory')]], [kit.width / 2, kit.width / 2])]
    return _build(kit, f"Delivery challan {dispatch.number}", [story])


# --- Purchase order ---------------------------------------------------------------------------

def purchase_order(order):
    from reportlab.platypus import Spacer

    kit = _Kit()
    mm = kit.mm
    buyer, supplier = order.company, order.supplier
    address = order.ship_to.address
    draft = order.status in ('draft', 'pending_approval')
    story = [kit.header(buyer, 'PURCHASE ORDER', order.get_status_display(), [
        ('PO no.', order.number), ('PO date', order.order_date.strftime('%d %b %Y')),
        ('Deliver by', order.expected_date.strftime('%d %b %Y') if order.expected_date else '—'),
        ('Payment terms', f"{order.payment_terms_days} days" if order.payment_terms_days is not None else '—'),
        ('Your reference', order.supplier_reference or '—'),
    ]), Spacer(1, 4 * mm)]
    to = kit.party('Supplier', supplier.legal_name or supplier.name, supplier.principal_address, supplier.gstin,
                   _state_code(supplier))
    deliver = kit.party('Deliver to', buyer.legal_name or buyer.name,
                        ', '.join(p for p in (order.ship_to.name, address.line1 if address else '', address.city if address else '',
                                              address.pincode if address else '') if p),
                        buyer.gstin, order.place_of_supply)
    story += [kit.two_boxes(to, deliver), Spacer(1, 4 * mm)]
    head = [kit.P(h, kit.label if i < 2 else kit.label_right) for i, h in enumerate(['#', 'DESCRIPTION', 'QTY', 'RATE', 'GST %', 'AMOUNT'])]
    rows = [head]
    for i, l in enumerate(order.lines.select_related('item'), start=1):
        desc = [kit.P(_esc(l.item.name)), kit.P(_esc(' · '.join(p for p in (l.item.code, f"HSN {l.item.hsn_code}" if l.item.hsn_code else '') if p)), kit.small)]
        if l.description:
            desc.append(kit.P(_esc(l.description), kit.small))
        rows.append([kit.P(str(i)), desc, kit.P(f"{_qty(l.quantity)} {l.unit}", kit.right), kit.P(_money(l.rate, 'INR'), kit.right),
                     kit.P(f"{l.gst_rate.normalize():f}%", kit.right), kit.P(_money(l.taxable_value, 'INR'), kit.right)])
    story += [kit.table(rows, [7 * mm, kit.width - 100 * mm, 25 * mm, 25 * mm, 15 * mm, 28 * mm],
                        [('LINEBELOW', (0, 0), (-1, 0), 0.8, kit.navy), ('LINEBELOW', (0, 1), (-1, -1), 0.3, kit.rule)], repeat=1),
              Spacer(1, 3 * mm), kit.totals(order), Spacer(1, 2 * mm),
              kit.P(f"<b>Amount in words:</b> {_esc(amount_in_words(order.total))}"), Spacer(1, 4 * mm)]
    terms = [kit.P('TERMS', kit.label)]
    if order.notes:
        terms.append(kit.P(_esc(order.notes)))
    terms.append(kit.P("Please quote our PO number on your invoice and delivery challan. Goods are received subject to inspection.", kit.small))
    sign = kit.signature(buyer, (order.approved_by.get_full_name() or order.approved_by.email) if order.approved_by else 'Authorised signatory')
    story.append(kit.table([[terms, sign]], [kit.width * 0.6, kit.width * 0.4]))
    return _build(kit, f"Purchase order {order.number}", [story], 'DRAFT' if draft else ('CANCELLED' if order.status == 'cancelled' else ''))


# --- Credit / debit note --------------------------------------------------------------------

def credit_debit_note(note):
    """A credit or debit note issued to a customer (CGST Act s.34; Rule 53):
    it names the original invoice it adjusts."""
    from reportlab.platypus import Spacer

    kit = _Kit()
    mm = kit.mm
    seller, customer, invoice = note.company, note.party, note.sales_invoice
    draft = note.status == 'draft'
    title = 'CREDIT NOTE' if note.kind == 'credit' else 'DEBIT NOTE'
    number = note.number or 'DRAFT'
    story = [kit.header(seller, title, note.get_reason_display(), [
        ('Note no.', number), ('Note date', note.note_date.strftime('%d %b %Y')),
        ('Against invoice', invoice.number), ('Invoice date', invoice.invoice_date.strftime('%d %b %Y')),
        ('Place of supply', _state(note.place_of_supply)),
    ]), Spacer(1, 4 * mm)]
    to = kit.party('Issued to', customer.legal_name or customer.name, customer.principal_address, customer.gstin, _state_code(customer))
    details = [kit.P('REASON', kit.label), kit.P(_esc(note.get_reason_display()))]
    if note.remarks:
        details.append(kit.P(_esc(note.remarks), kit.small))
    if note.warehouse_id and note.reason == 'goods_returned':
        details.append(kit.P(f"Goods received back into {_esc(note.warehouse.name)}", kit.small))
    story += [kit.two_boxes(to, details), Spacer(1, 4 * mm)]
    head = [kit.P(h, kit.label if i < 2 else kit.label_right) for i, h in enumerate(['#', 'DESCRIPTION', 'HSN/SAC', 'QTY', 'RATE', 'TAXABLE', 'GST %', 'TAX'])]
    rows = [head]
    for i, l in enumerate(note.lines.select_related('item'), start=1):
        rows.append([kit.P(str(i)), [kit.P(_esc(l.item.name)), kit.P(_esc(l.item.code), kit.small)], kit.P(_esc(l.hsn_code), kit.right),
                     kit.P(f"{_qty(l.quantity)} {l.unit}" if l.quantity else '—', kit.right),
                     kit.P(_money(l.rate, 'INR') if l.quantity else '—', kit.right), kit.P(_money(l.taxable_value, 'INR'), kit.right),
                     kit.P(f"{l.gst_rate.normalize():f}%", kit.right), kit.P(_money(l.tax_amount, 'INR'), kit.right)])
    story += [kit.table(rows, [7 * mm, kit.width - 133 * mm, 17 * mm, 22 * mm, 22 * mm, 26 * mm, 14 * mm, 25 * mm],
                        [('LINEBELOW', (0, 0), (-1, 0), 0.8, kit.navy), ('LINEBELOW', (0, 1), (-1, -1), 0.3, kit.rule)], repeat=1),
              Spacer(1, 3 * mm), kit.totals(note), Spacer(1, 2 * mm),
              kit.P(f"<b>Amount in words:</b> {_esc(amount_in_words(note.total))}"), Spacer(1, 2 * mm),
              kit.P(("This credit note reduces" if note.kind == 'credit' else "This debit note adds to")
                    + f" the value of invoice {_esc(invoice.number)} and the GST charged on it.", kit.small), Spacer(1, 6 * mm),
              kit.table([['', kit.signature(seller, 'Authorised signatory')]], [kit.width * 0.6, kit.width * 0.4])]
    if note.status == 'cancelled':
        story += [Spacer(1, 3 * mm), kit.P(f"<b>CANCELLED</b>: {_esc(note.cancel_reason)}")]
    watermark = 'DRAFT' if draft else ('CANCELLED' if note.status == 'cancelled' else '')
    return _build(kit, f"{title.title()} {number}", [story], watermark)


# --- Job work challan --------------------------------------------------------------------------

def job_work_challan(challan):
    """A delivery challan for goods sent for job work (rule 55): not a supply,
    no tax charged, to be returned within the time s.143 allows."""
    from reportlab.platypus import Spacer

    kit = _Kit()
    mm = kit.mm
    company, worker = challan.company, challan.job_worker
    source = challan.from_warehouse
    address = source.address
    from einvoice.services import active_ewb
    ewb = active_ewb(challan)
    story = [kit.header(company, 'DELIVERY CHALLAN', 'For job work (s.143) · not a supply', [
        ('Challan no.', challan.number), ('Date', challan.challan_date.strftime('%d %b %Y')),
        ('Nature of job work', challan.process),
        ('Return by', challan.expected_return.strftime('%d %b %Y') if challan.expected_return else '—'),
        ('Vehicle', (ewb.vehicle_number if ewb and ewb.vehicle_number else challan.vehicle_number) or '—'),
        *([('E-way bill', ewb.number)] if ewb else []),
    ]), Spacer(1, 4 * mm)]
    consignor = kit.party('Principal (sent from)', company.legal_name or company.name,
                          ', '.join(p for p in (source.name, address.line1 if address else '', address.city if address else '') if p),
                          company.gstin, (address.state_code if address else '') or _state_code(company))
    consignee = kit.party('Job worker', worker.legal_name or worker.name, worker.principal_address, worker.gstin, _state_code(worker))
    story += [kit.two_boxes(consignor, consignee), Spacer(1, 4 * mm)]
    head = [kit.P(h, kit.label if i < 2 else kit.label_right) for i, h in enumerate(['#', 'DESCRIPTION', 'HSN', 'QTY', 'RATE', 'VALUE'])]
    rows, total = [head], Decimal('0')
    for i, l in enumerate(challan.lines.select_related('item'), start=1):
        value = (l.quantity * l.rate).quantize(Decimal('0.01'))
        total += value
        rows.append([kit.P(str(i)), [kit.P(_esc(l.item.name)), kit.P(_esc(l.item.code), kit.small)], kit.P(_esc(l.item.hsn_code), kit.right),
                     kit.P(f"{_qty(l.quantity)} {l.item.unit}", kit.right), kit.P(_money(l.rate, 'INR'), kit.right),
                     kit.P(_money(value, 'INR'), kit.right)])
    rows.append(['', kit.P('<b>Value of goods (for the record; no tax charged)</b>'), '', '', '', kit.P(_money(total, 'INR'), kit.right_bold)])
    story += [kit.table(rows, [7 * mm, kit.width - 101 * mm, 18 * mm, 24 * mm, 24 * mm, 28 * mm],
                        [('LINEBELOW', (0, 0), (-1, 0), 0.8, kit.navy), ('LINEBELOW', (0, 1), (-1, -2), 0.3, kit.rule),
                         ('LINEABOVE', (0, -1), (-1, -1), 0.8, kit.navy)], repeat=1),
              Spacer(1, 3 * mm),
              kit.P("Goods sent for job work under section 143 of the CGST Act, 2017. Not a supply: no GST is charged. "
                    "Inputs are to be returned within one year of this challan.", kit.small)]
    if challan.notes:
        story += [Spacer(1, 2 * mm), kit.P(_esc(challan.notes))]
    received = [kit.P("Received by the job worker", kit.label), Spacer(1, 14 * mm), kit.P("Signature and stamp", kit.small)]
    story += [Spacer(1, 8 * mm), kit.table([[received, kit.signature(company, 'Authorised signatory')]], [kit.width / 2, kit.width / 2])]
    watermark = 'CANCELLED' if challan.status == 'cancelled' else ''
    return _build(kit, f"Job work challan {challan.number}", [story], watermark)


# --- Batch and serial number labels -----------------------------------------------------------

def batch_labels(company, batch_list, copies=1):
    """An A4 sheet of 24 labels (3 x 8, 70 x 37 mm) per page: item, batch
    or serial number, expiry, and a Code 128 barcode a scanner reads back
    (inventory.api batch lookup)."""
    from reportlab.graphics.barcode.code128 import Code128
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(f"Labels - {company.name or company.legal_name}")
    width, height = A4
    columns, rows_per_page, label_w, label_h = 3, 8, 70 * mm, 37 * mm
    left, top = (width - columns * label_w) / 2, height - (height - rows_per_page * label_h) / 2
    labels = [b for b in batch_list for _ in range(max(1, min(copies, 24)))]
    for index, batch in enumerate(labels):
        slot = index % (columns * rows_per_page)
        if index and slot == 0:
            pdf.showPage()
        x = left + (slot % columns) * label_w + 4 * mm
        y = top - (slot // columns) * label_h
        pdf.setFont('Helvetica', 6.5)
        pdf.setFillColorRGB(0.42, 0.45, 0.5)
        pdf.drawString(x, y - 5 * mm, (company.name or company.legal_name)[:48])
        pdf.setFillColorRGB(0, 0, 0)
        pdf.setFont('Helvetica-Bold', 8)
        pdf.drawString(x, y - 9 * mm, f"{batch.item.code}  {batch.item.name}"[:44])
        pdf.setFont('Helvetica-Bold', 11)
        pdf.drawString(x, y - 14 * mm, ('S/N ' if batch.item.by_serial else 'Batch ') + batch.number[:28])
        pdf.setFont('Helvetica', 6.5)
        details = [f"Exp {batch.expires_on:%d %b %Y}" if batch.expires_on else '',
                   f"Mfg {batch.manufactured_on:%d %b %Y}" if batch.manufactured_on else '',
                   f"Heat {batch.supplier_batch}" if batch.supplier_batch else '']
        pdf.drawString(x, y - 17.5 * mm, '   '.join(d for d in details if d)[:60])
        code = Code128(batch.label_code, barHeight=11 * mm, barWidth=0.32 * mm, humanReadable=True, fontSize=6)
        code.drawOn(pdf, x - 6 * mm, y - 33 * mm)
    if not labels:
        pdf.drawString(20 * mm, height - 20 * mm, 'No labels.')
    pdf.save()
    return buffer.getvalue()


# --- Quotation and request for quotation ------------------------------------------------------

def quotation(q):
    from reportlab.platypus import Spacer

    kit = _Kit()
    mm = kit.mm
    seller, customer = q.company, q.customer
    story = [kit.header(seller, 'QUOTATION', q.get_status_display() if q.status == 'draft' else 'Our offer', [
        ('Quotation no.', q.display_number), ('Date', q.quote_date.strftime('%d %b %Y')),
        ('Valid until', q.valid_until.strftime('%d %b %Y')),
        ('Delivery', f"{q.delivery_days} days from order" if q.delivery_days is not None else '—'),
        ('Payment terms', f"{q.payment_terms_days} days" if q.payment_terms_days is not None else '—'),
        ('Your enquiry', q.enquiry_reference or '—'),
    ]), Spacer(1, 4 * mm)]
    to = kit.party('To', customer.legal_name or customer.name, customer.principal_address, customer.gstin, _state_code(customer))
    supply = [kit.P('PLACE OF SUPPLY', kit.label), kit.P(f"<b>{_esc(_state(q.place_of_supply))}</b>"),
              kit.P('IGST applies (inter-state)' if q.is_interstate else 'CGST + SGST apply (within the state)', kit.small)]
    story += [kit.two_boxes(to, supply), Spacer(1, 4 * mm)]
    head = [kit.P(h, kit.label if i < 2 else kit.label_right) for i, h in enumerate(['#', 'DESCRIPTION', 'QTY', 'RATE', 'GST %', 'AMOUNT'])]
    rows = [head]
    for i, l in enumerate(q.lines.select_related('item'), start=1):
        desc = [kit.P(_esc(l.item.name)), kit.P(_esc(' · '.join(p for p in (l.item.code, f"HSN {l.item.hsn_code}" if l.item.hsn_code else '') if p)), kit.small)]
        if l.description:
            desc.append(kit.P(_esc(l.description), kit.small))
        rows.append([kit.P(str(i)), desc, kit.P(f"{_qty(l.quantity)} {l.unit}", kit.right), kit.P(_money(l.rate, 'INR'), kit.right),
                     kit.P(f"{l.gst_rate.normalize():f}%", kit.right), kit.P(_money(l.taxable_value, 'INR'), kit.right)])
    story += [kit.table(rows, [7 * mm, kit.width - 100 * mm, 25 * mm, 25 * mm, 15 * mm, 28 * mm],
                        [('LINEBELOW', (0, 0), (-1, 0), 0.8, kit.navy), ('LINEBELOW', (0, 1), (-1, -1), 0.3, kit.rule)], repeat=1),
              Spacer(1, 3 * mm), kit.totals(q), Spacer(1, 2 * mm),
              kit.P(f"<b>Amount in words:</b> {_esc(amount_in_words(q.total))}"), Spacer(1, 4 * mm)]
    terms = [kit.P('TERMS', kit.label)]
    if q.terms:
        terms.append(kit.P(_esc(q.terms).replace('\n', '<br/>')))
    terms.append(kit.P(f"Prices are valid until {q.valid_until:%d %b %Y}. GST as applicable at the time of supply. "
                       "Please quote our quotation number on your purchase order.", kit.small))
    story += [kit.table([[terms, kit.signature(seller, 'Authorised signatory')]], [kit.width * 0.6, kit.width * 0.4])]
    return _build(kit, f"Quotation {q.display_number}", [story], watermark='DRAFT' if q.status == 'draft' else '')


def request_for_quotation(rfq):
    from reportlab.platypus import Spacer

    kit = _Kit()
    mm = kit.mm
    buyer = rfq.company
    address = rfq.ship_to.address
    story = [kit.header(buyer, 'REQUEST FOR QUOTATION', rfq.title, [
        ('RFQ no.', rfq.number), ('Date', rfq.rfq_date.strftime('%d %b %Y')),
        ('Quote by', rfq.respond_by.strftime('%d %b %Y') if rfq.respond_by else '—'),
        ('Needed by', rfq.required_by.strftime('%d %b %Y') if rfq.required_by else '—'),
    ]), Spacer(1, 4 * mm)]
    deliver = kit.party('Deliver to', buyer.legal_name or buyer.name,
                        ', '.join(p for p in (rfq.ship_to.name, address.line1 if address else '', address.city if address else '',
                                              address.pincode if address else '') if p),
                        buyer.gstin, (address.state_code if address else '') or _state_code(buyer))
    story += deliver + [Spacer(1, 4 * mm)]
    head = [kit.P(h, kit.label if i < 2 else kit.label_right) for i, h in enumerate(['#', 'ITEM AND SPECIFICATION', 'QTY', 'YOUR RATE', 'GST %'])]
    rows = [head]
    for i, l in enumerate(rfq.lines.select_related('item'), start=1):
        desc = [kit.P(_esc(l.item.name)), kit.P(_esc(' · '.join(p for p in (l.item.code, f"HSN {l.item.hsn_code}" if l.item.hsn_code else '') if p)), kit.small)]
        if l.description:
            desc.append(kit.P(_esc(l.description), kit.small))
        rows.append([kit.P(str(i)), desc, kit.P(f"{_qty(l.quantity)} {l.unit}", kit.right), '', ''])
    story += [kit.table(rows, [7 * mm, kit.width - 99 * mm, 28 * mm, 40 * mm, 24 * mm],
                        [('LINEBELOW', (0, 0), (-1, 0), 0.8, kit.navy), ('LINEBELOW', (0, 1), (-1, -1), 0.3, kit.rule),
                         ('BOTTOMPADDING', (0, 1), (-1, -1), 8)], repeat=1),
              Spacer(1, 4 * mm)]
    if rfq.notes:
        story += [kit.P('NOTES', kit.label), kit.P(_esc(rfq.notes).replace('\n', '<br/>')), Spacer(1, 3 * mm)]
    story += [kit.P("Please quote your rate per unit before GST, the GST rate, delivery time, payment terms and how long "
                    "your quote is valid, quoting our RFQ number.", kit.small),
              Spacer(1, 8 * mm), kit.table([['', kit.signature(buyer, 'Purchase')]], [kit.width * 0.6, kit.width * 0.4])]
    return _build(kit, f"RFQ {rfq.number}", [story])


def _irn_block(kit, e):
    """The IRN, acknowledgement and the IRP's signed QR code, as rule 48(4) requires on the invoice."""
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing

    mm = kit.mm
    widget = QrCodeWidget(e.signed_qr)
    x1, y1, x2, y2 = widget.getBounds()
    size = 30 * mm
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    drawing.add(widget)
    text = [kit.P('E-INVOICE', kit.label), kit.P(f"IRN: {e.irn}", kit.small),
            kit.P(f"Ack no. {e.ack_number} · Ack date {timezone.localtime(e.ack_date):%d %b %Y %H:%M}", kit.small)]
    if e.provider == 'mock':
        text.append(kit.P("Registered on the MakeSetu test portal: not a valid e-invoice.", kit.small))
    return kit.table([[drawing, text]], [33 * mm, kit.width - 33 * mm])
