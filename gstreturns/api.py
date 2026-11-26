"""/api/v1 endpoints for GST returns: a summary to review on screen, and
the files to download (GSTR-1 JSON, GSTR-3B JSON, Tally XML). Needs
ledger.view (the Accounts role, or the Org Admin)."""
import json
from decimal import Decimal
from typing import List

from django.http import HttpResponse
from ninja import Router, Schema
from ninja.errors import HttpError

from organizations.app_api import membership_for

from . import services, tally

router = Router(tags=['gst-returns'])


def _period(request, period):
    membership = membership_for(request, 'ledger.view')
    try:
        start, end = services.parse_period(period)
        return services.Period(membership.company, start, end)
    except services.ReturnError as exc:
        raise HttpError(400, str(exc))


class SectionOut(Schema):
    key: str
    label: str
    count: int
    taxable: Decimal
    tax: Decimal


class TaxRowOut(Schema):
    key: str
    label: str
    taxable: Decimal
    igst: Decimal
    cgst: Decimal
    sgst: Decimal


class CheckOut(Schema):
    level: str
    message: str


class ReturnSummaryOut(Schema):
    period: str
    fp: str
    gstin: str
    gstr1: List[SectionOut]
    gstr3b: List[TaxRowOut]
    checks: List[CheckOut]


def _sum_items(entries, key='itms'):
    taxable = tax = Decimal('0')
    for e in entries:
        for item in e[key]:
            d = item['itm_det']
            taxable += Decimal(str(d['txval']))
            tax += Decimal(str(d['iamt'])) + Decimal(str(d['camt'])) + Decimal(str(d['samt']))
    return taxable, tax


def _row_tax(row):
    return sum(Decimal(str(row.get(k, 0))) for k in ('iamt', 'camt', 'samt'))


@router.get('/gst-returns/summary', response=ReturnSummaryOut)
def summary(request, period: str):
    p = _period(request, period)
    g1, g3 = services.gstr1(p), services.gstr3b(p)
    b2b = [inv for party in g1.get('b2b', []) for inv in party['inv']]
    b2cl = [inv for state in g1.get('b2cl', []) for inv in state['inv']]
    cdnr = [nt for party in g1.get('cdnr', []) for nt in party['nt']]
    b2cs = g1.get('b2cs', [])
    hsn = g1.get('hsn', {})
    hsn_rows = hsn.get('hsn_b2b', []) + hsn.get('hsn_b2c', [])
    docs = g1.get('doc_issue', {}).get('doc_det', [])

    def section(key, label, count, taxable, tax):
        return {'key': key, 'label': label, 'count': count, 'taxable': taxable, 'tax': tax}

    gstr1 = [
        section('b2b', '4A · B2B invoices (registered customers)', len(b2b), *_sum_items(b2b)),
        section('b2cl', '5 · B2C large (inter-state over ₹1 lakh, unregistered)', len(b2cl), *_sum_items(b2cl)),
        section('b2cs', '7 · B2C others (rows by state and rate)', len(b2cs),
                sum((Decimal(str(r['txval'])) for r in b2cs), Decimal('0')), sum((_row_tax(r) for r in b2cs), Decimal('0'))),
        section('cdnr', '9B · Credit/debit notes (registered)', len(cdnr), *_sum_items(cdnr)),
        section('cdnur', '9B · Credit/debit notes (unregistered, B2CL)', len(g1.get('cdnur', [])), *_sum_items(g1.get('cdnur', []))),
        section('hsn', '12 · HSN summary (rows)', len(hsn_rows),
                sum((Decimal(str(r['txval'])) for r in hsn_rows), Decimal('0')), sum((_row_tax(r) for r in hsn_rows), Decimal('0'))),
        section('doc_issue', '13 · Documents issued', sum(d['docs'][0]['totnum'] for d in docs), Decimal('0'), Decimal('0')),
    ]

    def row(key, label, d):
        return {'key': key, 'label': label, 'taxable': Decimal(str(d.get('txval', 0))), 'igst': Decimal(str(d.get('iamt', 0))),
                'cgst': Decimal(str(d.get('camt', 0))), 'sgst': Decimal(str(d.get('samt', 0)))}

    unreg = g3['inter_sup']['unreg_details']
    gstr3b = [
        row('3.1a', '3.1(a) Outward taxable supplies', g3['sup_details']['osup_det']),
        row('3.1c', '3.1(c) Nil-rated and exempted supplies', g3['sup_details']['osup_nil_exmp']),
        row('3.2', '3.2 Inter-state supplies to unregistered persons (of 3.1(a))',
            {'txval': sum(r['txval'] for r in unreg), 'iamt': sum(r['iamt'] for r in unreg)}),
        row('4a5', '4(A)(5) Input tax credit: all other ITC (from your bills)', g3['itc_elg']['itc_net']),
    ]
    return {'period': period, 'fp': g1['fp'], 'gstin': g1['gstin'], 'gstr1': gstr1, 'gstr3b': gstr3b, 'checks': services.checks(p)}


def _download(content, filename, content_type):
    response = HttpResponse(content, content_type=content_type)
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'private, no-store'
    return response


@router.get('/gst-returns/gstr1')
def gstr1_file(request, period: str):
    p = _period(request, period)
    return _download(json.dumps(services.gstr1(p), indent=1), f"GSTR1_{p.company.gstin}_{services.fp(p.start)}.json",
                     'application/json')


@router.get('/gst-returns/gstr3b')
def gstr3b_file(request, period: str):
    p = _period(request, period)
    return _download(json.dumps(services.gstr3b(p), indent=1), f"GSTR3B_{p.company.gstin}_{services.fp(p.start)}.json",
                     'application/json')


@router.get('/gst-returns/tally')
def tally_file(request, period: str):
    p = _period(request, period)
    return _download(tally.export(p), f"Tally_{p.company.gstin}_{services.fp(p.start)}.xml", 'application/xml')
