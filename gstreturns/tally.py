"""A Tally Prime import file (XML) for one month: party ledgers, then a
voucher for each posted sales invoice, credit/debit note, purchase bill,
supplier note, receipt and payment.

Party ledgers are created with their GSTIN under Sundry Debtors or Sundry
Creditors. The other ledgers must already exist in Tally under these
names (or be renamed here): see LEDGERS. Tally's XML sign convention: a
debit is a negative AMOUNT with ISDEEMEDPOSITIVE Yes; a credit is
positive with ISDEEMEDPOSITIVE No.
"""
from decimal import Decimal
from xml.sax.saxutils import escape

from finance.models import Payment

LEDGERS = {
    'sales': 'Sales @{rate}%',
    'purchase': 'Purchase @{rate}%',
    'output_igst': 'Output IGST', 'output_cgst': 'Output CGST', 'output_sgst': 'Output SGST',
    'input_igst': 'Input IGST', 'input_cgst': 'Input CGST', 'input_sgst': 'Input SGST',
    'round_off': 'Round Off',
    'bank': 'Bank', 'cash': 'Cash',
}


def _x(text):
    """Escaped for XML text and attribute values (quotes too: names go in NAME="...")."""
    return escape(str(text or ''), {'"': '&quot;', "'": '&apos;'})


def _amt(value):
    return f"{Decimal(value):.2f}"


def _entry(ledger, amount, debit):
    """One ledger line: `amount` is positive; `debit` says which side."""
    signed = -Decimal(amount) if debit else Decimal(amount)
    return (f"<ALLLEDGERENTRIES.LIST><LEDGERNAME>{_x(ledger)}</LEDGERNAME>"
            f"<ISDEEMEDPOSITIVE>{'Yes' if debit else 'No'}</ISDEEMEDPOSITIVE><AMOUNT>{_amt(signed)}</AMOUNT></ALLLEDGERENTRIES.LIST>")


def _party_name(company):
    return company.name or company.trade_name or company.legal_name


def _ledger_master(company, parent):
    gstin = company.gstin or ''
    return ('<TALLYMESSAGE xmlns:UDF="TallyUDF">'
            f'<LEDGER NAME="{_x(_party_name(company))}" ACTION="Create">'
            f'<NAME>{_x(_party_name(company))}</NAME><PARENT>{parent}</PARENT><ISBILLWISEON>Yes</ISBILLWISEON>'
            + (f'<PARTYGSTIN>{_x(gstin)}</PARTYGSTIN><GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE>' if gstin
               else '<GSTREGISTRATIONTYPE>Unregistered</GSTREGISTRATIONTYPE>')
            + f'<LEDSTATENAME>{_x(company.state)}</LEDSTATENAME>'
            '</LEDGER></TALLYMESSAGE>')


def _tax_entries(doc, prefix, tax_debit):
    """CGST/SGST or IGST lines and round-off for an invoice, bill or note."""
    entries = []
    if doc.is_interstate:
        if doc.igst:
            entries.append(_entry(LEDGERS[f'{prefix}_igst'], doc.igst, tax_debit))
    else:
        if doc.cgst:
            entries.append(_entry(LEDGERS[f'{prefix}_cgst'], doc.cgst, tax_debit))
        if doc.sgst:
            entries.append(_entry(LEDGERS[f'{prefix}_sgst'], doc.sgst, tax_debit))
    if doc.round_off:
        # Round-off sits on the same side as the tax when it adds to the total.
        entries.append(_entry(LEDGERS['round_off'], abs(doc.round_off), tax_debit if doc.round_off > 0 else not tax_debit))
    return entries


def _by_rate(lines):
    totals = {}
    for l in lines:
        totals[l.gst_rate] = totals.get(l.gst_rate, Decimal('0')) + l.taxable_value
    return sorted(totals.items())


def _voucher(kind, number, day, party, party_debit, total, lines, doc, ledger_key, narration):
    """A Sales/Purchase/Credit Note/Debit Note voucher. The party is on one
    side for the total; item values (by rate) and tax on the other."""
    body = [_entry(_party_name(party), total, party_debit)]
    for rate, value in _by_rate(lines):
        body.append(_entry(LEDGERS[ledger_key].format(rate=Decimal(rate).normalize()), value, not party_debit))
    body += _tax_entries(doc, 'output' if ledger_key == 'sales' else 'input', not party_debit)
    return ('<TALLYMESSAGE xmlns:UDF="TallyUDF">'
            f'<VOUCHER VCHTYPE="{kind}" ACTION="Create"><DATE>{day:%Y%m%d}</DATE>'
            f'<VOUCHERTYPENAME>{kind}</VOUCHERTYPENAME><VOUCHERNUMBER>{_x(number)}</VOUCHERNUMBER>'
            f'<PARTYLEDGERNAME>{_x(_party_name(party))}</PARTYLEDGERNAME><NARRATION>{_x(narration)}</NARRATION>'
            + ''.join(body) + '</VOUCHER></TALLYMESSAGE>')


def export(period):
    """The XML for `period` (gstreturns.services.Period)."""
    from adjustments.models import CreditDebitNote

    p = period
    messages = []
    customers = {i.customer_id: i.customer for i in p.posted_invoices} | {n.party_id: n.party for n in p.posted_notes}
    suppliers = {b.supplier_id: b.supplier for b in p.bills} | {n.party_id: n.party for n in p.supplier_notes}
    payments = list(Payment.objects.filter(company=p.company, status=Payment.Status.POSTED,
                                           payment_date__gte=p.start, payment_date__lte=p.end).select_related('party'))
    for pay in payments:
        (customers if pay.direction == Payment.Direction.RECEIVED else suppliers).setdefault(pay.party_id, pay.party)
    messages += [_ledger_master(c, 'Sundry Debtors') for c in customers.values()]
    messages += [_ledger_master(s, 'Sundry Creditors') for s in suppliers.values() if s.pk not in customers]

    for inv in p.posted_invoices:
        messages.append(_voucher('Sales', inv.number, inv.invoice_date, inv.customer, True, inv.total, inv.lines.all(), inv,
                                 'sales', f"Against order {inv.order.number}"))
    for n in p.posted_notes:
        credit = n.kind == CreditDebitNote.Kind.CREDIT
        messages.append(_voucher('Credit Note' if credit else 'Debit Note', n.number, n.note_date, n.party, not credit, n.total,
                                 n.lines.all(), n, 'sales', f"{n.get_reason_display()} against {n.sales_invoice.number}"))
    for b in p.bills:
        messages.append(_voucher('Purchase', b.supplier_invoice_number, b.invoice_date, b.supplier, False, b.total, b.lines.all(), b,
                                 'purchase', f"Our ref {b.number}, against {b.order.number}"))
    for n in p.supplier_notes:
        credit = n.kind == CreditDebitNote.Kind.CREDIT
        # A supplier's credit note is a debit note in our books (and the other way round).
        messages.append(_voucher('Debit Note' if credit else 'Credit Note', n.party_note_number, n.note_date, n.party, credit, n.total,
                                 n.lines.all(), n, 'purchase', f"{n.get_reason_display()} (their {n.party_note_number}, our {n.number})"))
    for pay in payments:
        cash = pay.mode == Payment.Mode.CASH
        account = LEDGERS['cash'] if cash else LEDGERS['bank']
        received = pay.direction == Payment.Direction.RECEIVED
        body = [_entry(account, pay.amount, received), _entry(_party_name(pay.party), pay.amount, not received)]
        kind = 'Receipt' if received else 'Payment'
        messages.append('<TALLYMESSAGE xmlns:UDF="TallyUDF">'
                        f'<VOUCHER VCHTYPE="{kind}" ACTION="Create"><DATE>{pay.payment_date:%Y%m%d}</DATE>'
                        f'<VOUCHERTYPENAME>{kind}</VOUCHERTYPENAME><VOUCHERNUMBER>{_x(pay.number)}</VOUCHERNUMBER>'
                        f'<PARTYLEDGERNAME>{_x(_party_name(pay.party))}</PARTYLEDGERNAME>'
                        f'<NARRATION>{_x(" · ".join(x for x in (pay.get_mode_display(), pay.reference) if x))}</NARRATION>'
                        + ''.join(body) + '</VOUCHER></TALLYMESSAGE>')

    return ('<?xml version="1.0" encoding="UTF-8"?>\n<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>'
            '<BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>All Masters</REPORTNAME>'
            f'<STATICVARIABLES><SVCURRENTCOMPANY>{_x(p.company.legal_name or p.company.name)}</SVCURRENTCOMPANY></STATICVARIABLES>'
            '</REQUESTDESC><REQUESTDATA>' + ''.join(messages) + '</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>')
