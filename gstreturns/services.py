"""GSTR-1 and GSTR-3B for one tax period (a calendar month), from the
organization's posted documents dated in that month.

GSTR-1 (outward supplies) uses the field names of the GST portal's GSTR-1
JSON (b2b, b2cl, b2cs, cdnr, cdnur, hsn, doc_issue). GSTR-3B uses the
3B JSON names (sup_details, inter_sup, itc_elg). The portal revises its
formats from time to time, so check a file in the offline tool before
filing from it.

Input tax credit is computed from posted purchase bills in the month. The
law allows ITC only as shown in GSTR-2B, so the 3B figures are a starting
point to reconcile, not a figure to file blind.

Amounts are rounded to paise; within a document the CGST/SGST split
follows the document's own (CGST = half the tax, rounded; SGST the rest).
"""
import calendar
import re
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from adjustments.models import CreditDebitNote
from gst.services.providers.base import STATE_NAMES
from purchase.models import PurchaseBill
from sales.models import SalesInvoice

ZERO, CENT = Decimal('0'), Decimal('0.01')
B2CL_LIMIT = Decimal('100000')  # inter-state invoices to unregistered buyers above this are B2CL (since Aug 2024)
GSTIN_PATTERN = re.compile(r'^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$')

# Table 13 document types used here.
DOC_INVOICES, DOC_DEBIT_NOTES, DOC_CREDIT_NOTES = 1, 4, 5


class ReturnError(Exception):
    pass


def _m(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _f(value):
    """JSON number for money: two decimals."""
    return float(_m(value))


def _rate(value):
    value = Decimal(value).normalize()
    return int(value) if value == value.to_integral() else float(value)


def _gst_date(day):
    return day.strftime('%d-%m-%Y')


def _split(tax, interstate):
    """(igst, cgst, sgst) for a tax amount."""
    if interstate:
        return tax, ZERO, ZERO
    cgst = _m(tax / 2)
    return ZERO, cgst, tax - cgst


def registered(company):
    return bool(company.gstin and GSTIN_PATTERN.match(company.gstin))


def parse_period(value):
    """'2026-11' -> (date(2026,11,1), date(2026,11,30))."""
    match = re.fullmatch(r'(\d{4})-(\d{2})', value or '')
    if not match:
        raise ReturnError("Give the period as YYYY-MM, e.g. 2026-11.")
    year, month = int(match[1]), int(match[2])
    if not 1 <= month <= 12:
        raise ReturnError("That isn't a month.")
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def fp(start):
    """The return period as the portal writes it: MMYYYY."""
    return start.strftime('%m%Y')


class Period:
    """Everything one month's returns are built from, loaded once."""

    def __init__(self, company, start, end):
        if not registered(company):
            raise ReturnError("Your company needs a valid GSTIN to prepare GST returns.")
        self.company, self.start, self.end = company, start, end
        in_month = {'invoice_date__gte': start, 'invoice_date__lte': end}
        self.invoices = list(SalesInvoice.objects.filter(company=company, **in_month)
                             .exclude(status=SalesInvoice.Status.DRAFT).select_related('customer').prefetch_related('lines__item')
                             .order_by('invoice_date', 'number'))
        self.notes = list(CreditDebitNote.objects.filter(company=company, side=CreditDebitNote.Side.SALES,
                                                         note_date__gte=start, note_date__lte=end)
                          .exclude(status=CreditDebitNote.Status.DRAFT).select_related('party', 'sales_invoice')
                          .prefetch_related('lines__item').order_by('note_date', 'number'))
        self.bills = list(PurchaseBill.objects.filter(company=company, status=PurchaseBill.Status.POSTED, **in_month)
                          .select_related('supplier').prefetch_related('lines'))
        self.supplier_notes = list(CreditDebitNote.objects.filter(company=company, side=CreditDebitNote.Side.PURCHASE,
                                                                  status=CreditDebitNote.Status.POSTED,
                                                                  note_date__gte=start, note_date__lte=end)
                                   .select_related('party').prefetch_related('lines'))

    @property
    def posted_invoices(self):
        return [i for i in self.invoices if i.status == SalesInvoice.Status.POSTED]

    @property
    def posted_notes(self):
        return [n for n in self.notes if n.status == CreditDebitNote.Status.POSTED]


# --- GSTR-1 ------------------------------------------------------------------------------

def _items_by_rate(lines, interstate):
    """GSTR-1 'itms': one entry per tax rate."""
    by_rate = defaultdict(lambda: [ZERO, ZERO])
    for l in lines:
        by_rate[l.gst_rate][0] += l.taxable_value
        by_rate[l.gst_rate][1] += l.tax_amount
    out = []
    for num, (rate, (taxable, tax)) in enumerate(sorted(by_rate.items()), start=1):
        igst, cgst, sgst = _split(tax, interstate)
        out.append({'num': num, 'itm_det': {'txval': _f(taxable), 'rt': _rate(rate), 'iamt': _f(igst), 'camt': _f(cgst),
                                            'samt': _f(sgst), 'csamt': 0}})
    return out


def _hsn_rows(lines_with_interstate):
    """Table 12 rows, one per HSN, unit and rate."""
    rows = defaultdict(lambda: {'qty': ZERO, 'txval': ZERO, 'iamt': ZERO, 'camt': ZERO, 'samt': ZERO, 'desc': ''})
    for line, interstate, sign in lines_with_interstate:
        key = (line.hsn_code or line.item.hsn_code or '', line.unit, line.gst_rate)
        row = rows[key]
        igst, cgst, sgst = _split(line.tax_amount, interstate)
        row['qty'] += sign * line.quantity
        row['txval'] += sign * line.taxable_value
        row['iamt'] += sign * igst
        row['camt'] += sign * cgst
        row['samt'] += sign * sgst
        row['desc'] = row['desc'] or line.item.name[:30]
    out = []
    for num, ((hsn, unit, rate), r) in enumerate(sorted(rows.items(), key=lambda kv: (kv[0][0], kv[0][2])), start=1):
        out.append({'num': num, 'hsn_sc': hsn, 'desc': r['desc'], 'uqc': unit, 'qty': float(r['qty']), 'rt': _rate(rate),
                    'txval': _f(r['txval']), 'iamt': _f(r['iamt']), 'camt': _f(r['camt']), 'samt': _f(r['samt']), 'csamt': 0})
    return out


def _doc_range(numbers, cancelled, doc_type):
    if not numbers:
        return None
    ordered = sorted(numbers)
    return {'doc_num': doc_type, 'docs': [{'num': 1, 'from': ordered[0], 'to': ordered[-1], 'totnum': len(ordered),
                                           'cancel': cancelled, 'net_issue': len(ordered) - cancelled}]}


def gstr1(period):
    p = period
    b2b, b2cl = defaultdict(list), defaultdict(list)
    b2cs = defaultdict(lambda: [ZERO, ZERO])
    hsn_b2b, hsn_b2c = [], []
    for inv in p.posted_invoices:
        lines = list(inv.lines.all())
        customer = inv.customer
        entry = {'inum': inv.number, 'idt': _gst_date(inv.invoice_date), 'val': _f(inv.total), 'pos': inv.place_of_supply,
                 'rchrg': 'N', 'itms': _items_by_rate(lines, inv.is_interstate)}
        if registered(customer):
            b2b[customer.gstin].append({**entry, 'inv_typ': 'R'})
            hsn_b2b += [(l, inv.is_interstate, 1) for l in lines]
            continue
        hsn_b2c += [(l, inv.is_interstate, 1) for l in lines]
        if inv.is_interstate and inv.total > B2CL_LIMIT:
            b2cl[inv.place_of_supply].append(entry)
        else:
            for l in lines:
                key = ('INTER' if inv.is_interstate else 'INTRA', inv.place_of_supply, l.gst_rate)
                b2cs[key][0] += l.taxable_value
                b2cs[key][1] += l.tax_amount

    cdnr, cdnur = defaultdict(list), []
    for note in p.posted_notes:
        lines = list(note.lines.all())
        sign = 1 if note.kind == CreditDebitNote.Kind.DEBIT else -1
        entry = {'ntty': 'D' if note.kind == CreditDebitNote.Kind.DEBIT else 'C', 'nt_num': note.number,
                 'nt_dt': _gst_date(note.note_date), 'val': _f(note.total), 'pos': note.place_of_supply, 'rchrg': 'N',
                 'itms': _items_by_rate(lines, note.is_interstate)}
        if registered(note.party):
            cdnr[note.party.gstin].append({**entry, 'inv_typ': 'R'})
            hsn_b2b += [(l, note.is_interstate, sign) for l in lines]
        else:
            # Unregistered: only notes on B2CL invoices are reported separately.
            if note.is_interstate and note.sales_invoice.total > B2CL_LIMIT:
                cdnur.append({**entry, 'typ': 'B2CL'})
            else:
                for l in lines:
                    key = ('INTER' if note.is_interstate else 'INTRA', note.place_of_supply, l.gst_rate)
                    b2cs[key][0] += sign * l.taxable_value
                    b2cs[key][1] += sign * l.tax_amount
            hsn_b2c += [(l, note.is_interstate, sign) for l in lines]

    data = {'gstin': p.company.gstin, 'fp': fp(p.start)}
    if b2b:
        data['b2b'] = [{'ctin': gstin, 'inv': invs} for gstin, invs in sorted(b2b.items())]
    if b2cl:
        data['b2cl'] = [{'pos': pos, 'inv': invs} for pos, invs in sorted(b2cl.items())]
    if b2cs:
        data['b2cs'] = []
        for (supply, pos, rate), (taxable, tax) in sorted(b2cs.items()):
            igst, cgst, sgst = _split(tax, supply == 'INTER')
            data['b2cs'].append({'sply_ty': supply, 'pos': pos, 'typ': 'OE', 'rt': _rate(rate), 'txval': _f(taxable),
                                 'iamt': _f(igst), 'camt': _f(cgst), 'samt': _f(sgst), 'csamt': 0})
    if cdnr:
        data['cdnr'] = [{'ctin': gstin, 'nt': notes} for gstin, notes in sorted(cdnr.items())]
    if cdnur:
        data['cdnur'] = cdnur
    if hsn_b2b or hsn_b2c:
        data['hsn'] = {}
        if hsn_b2b:
            data['hsn']['hsn_b2b'] = _hsn_rows(hsn_b2b)
        if hsn_b2c:
            data['hsn']['hsn_b2c'] = _hsn_rows(hsn_b2c)
    docs = [d for d in (
        _doc_range([i.number for i in p.invoices], sum(1 for i in p.invoices if i.status == SalesInvoice.Status.CANCELLED), DOC_INVOICES),
        _doc_range([n.number for n in p.notes if n.kind == CreditDebitNote.Kind.DEBIT],
                   sum(1 for n in p.notes if n.kind == CreditDebitNote.Kind.DEBIT and n.status == CreditDebitNote.Status.CANCELLED), DOC_DEBIT_NOTES),
        _doc_range([n.number for n in p.notes if n.kind == CreditDebitNote.Kind.CREDIT],
                   sum(1 for n in p.notes if n.kind == CreditDebitNote.Kind.CREDIT and n.status == CreditDebitNote.Status.CANCELLED), DOC_CREDIT_NOTES),
    ) if d]
    if docs:
        data['doc_issue'] = {'doc_det': docs}
    return data


# --- GSTR-3B -------------------------------------------------------------------------------

def _tax_totals(lines_with_sign):
    t = {'txval': ZERO, 'iamt': ZERO, 'camt': ZERO, 'samt': ZERO}
    for line, interstate, sign in lines_with_sign:
        igst, cgst, sgst = _split(line.tax_amount, interstate)
        t['txval'] += sign * line.taxable_value
        t['iamt'] += sign * igst
        t['camt'] += sign * cgst
        t['samt'] += sign * sgst
    return t


def _num(t, keys=('txval', 'iamt', 'camt', 'samt')):
    out = {k: _f(t[k]) for k in keys if k in t}
    if 'iamt' in out:
        out['csamt'] = 0
    return out


def gstr3b(period):
    p = period
    taxable_out, nil_out, unregistered_inter = [], [], defaultdict(lambda: [ZERO, ZERO])
    sales_docs = [(inv, 1) for inv in p.posted_invoices] + [
        (n, 1 if n.kind == CreditDebitNote.Kind.DEBIT else -1) for n in p.posted_notes]
    for doc, sign in sales_docs:
        party = doc.customer if isinstance(doc, SalesInvoice) else doc.party
        for l in doc.lines.all():
            (nil_out if l.gst_rate == 0 else taxable_out).append((l, doc.is_interstate, sign))
            if doc.is_interstate and not registered(party) and l.gst_rate:
                unregistered_inter[doc.place_of_supply][0] += sign * l.taxable_value
                unregistered_inter[doc.place_of_supply][1] += sign * l.tax_amount
    out_t = _tax_totals(taxable_out)
    nil_t = _tax_totals(nil_out)

    itc_lines = [(l, b.is_interstate, 1) for b in p.bills for l in b.lines.all()]
    itc_lines += [(l, n.is_interstate, 1 if n.kind == CreditDebitNote.Kind.DEBIT else -1) for n in p.supplier_notes for l in n.lines.all()]
    itc = _tax_totals(itc_lines)
    zero = {'iamt': 0, 'camt': 0, 'samt': 0, 'csamt': 0}
    itc_net = {k: _f(itc[k]) for k in ('iamt', 'camt', 'samt')}
    itc_net['csamt'] = 0
    return {
        'gstin': p.company.gstin, 'ret_period': fp(p.start),
        'sup_details': {
            'osup_det': _num(out_t),
            'osup_zero': {'txval': 0, 'iamt': 0, 'csamt': 0},
            'osup_nil_exmp': {'txval': _f(nil_t['txval'])},
            'isup_rev': {'txval': 0, **zero},
            'osup_nongst': {'txval': 0},
        },
        'inter_sup': {
            'unreg_details': [{'pos': pos, 'txval': _f(t), 'iamt': _f(tax)} for pos, (t, tax) in sorted(unregistered_inter.items())],
            'comp_details': [], 'uin_details': [],
        },
        'itc_elg': {
            'itc_avl': [{'ty': ty, **zero} for ty in ('IMPG', 'IMPS', 'ISRC', 'ISD')] + [{'ty': 'OTH', **itc_net}],
            'itc_rev': [{'ty': 'RUL', **zero}, {'ty': 'OTH', **zero}],
            'itc_net': itc_net,
            'itc_inelg': [{'ty': 'RUL', **zero}, {'ty': 'OTH', **zero}],
        },
    }


# --- Checks shown before downloading ---------------------------------------------------------

def checks(period):
    """Things to look at before filing, as (level, message)."""
    p, out = period, []
    for inv in p.posted_invoices:
        missing = [l.item.code for l in inv.lines.all() if not (l.hsn_code or l.item.hsn_code)]
        if missing:
            out.append(('error', f"{inv.number}: no HSN/SAC for {', '.join(missing)}. Add it to the item; GSTR-1 Table 12 needs it."))
        if not registered(inv.customer):
            out.append(('info', f"{inv.number}: {inv.customer.name} has no valid GSTIN, so it's reported as B2C."))
    short = sorted({l.hsn_code for inv in p.posted_invoices for l in inv.lines.all() if l.hsn_code and len(l.hsn_code) < 6})
    if short:
        out.append(('warning', f"HSN codes with fewer than 6 digits ({', '.join(short)}): above ₹5 crore turnover, "
                               "GSTR-1 needs at least 6 digits."))
    cancelled = [i.number for i in p.invoices if i.status == SalesInvoice.Status.CANCELLED]
    if cancelled:
        out.append(('info', f"Cancelled invoices left out of the return (still counted in Table 13): {', '.join(cancelled)}."))
    drafts = SalesInvoice.objects.filter(company=p.company, status=SalesInvoice.Status.DRAFT,
                                         invoice_date__gte=p.start, invoice_date__lte=p.end).count()
    if drafts:
        out.append(('warning', f"{drafts} draft invoice{'s' if drafts != 1 else ''} dated this month aren't in the return until posted."))
    if p.bills:
        out.append(('warning', "Input tax credit is taken from your posted bills. File 3B with the ITC shown in your GSTR-2B: "
                               "check that each supplier has filed their GSTR-1 before claiming."))
    return [{'level': level, 'message': message} for level, message in out]


def state_name(code):
    return STATE_NAMES.get(code, code)
