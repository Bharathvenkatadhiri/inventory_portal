"""GSTR-1, GSTR-3B and the Tally export for a month."""
import json
import xml.etree.ElementTree as ET
from decimal import Decimal

from django.utils import timezone

from accounts.models import Company
from adjustments.tests import NoteTestCase
from organizations.tests import add_member
from sales.models import SalesInvoice

PERIOD = timezone.localdate().strftime('%Y-%m')
FP = timezone.localdate().strftime('%m%Y')


class ReturnTestCase(NoteTestCase):
    """Kaveri (supplier, signed in) posted an intra-state invoice to Vaigai
    (registered): 600 NOS of HSN 7318 at 6.25 and 250 KGS of 7214 at 76.50, 18%."""

    def file(self, kind, period=PERIOD):
        response = self.client.get(f'/api/v1/gst-returns/{kind}?period={period}')
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertIn('attachment', response['Content-Disposition'])
        return response.content

    def gstr1(self):
        return json.loads(self.file('gstr1'))

    def gstr3b(self):
        return json.loads(self.file('gstr3b'))


class Gstr1Tests(ReturnTestCase):
    def test_b2b_invoice_hsn_and_documents(self):
        data = self.gstr1()
        self.assertEqual((data['gstin'], data['fp']), (self.supplier.gstin, FP))
        [party] = data['b2b']
        self.assertEqual(party['ctin'], self.company.gstin)
        [inv] = party['inv']
        self.assertEqual((inv['inum'], inv['idt'], inv['pos'], inv['rchrg'], inv['inv_typ']),
                         (self.inv['number'], timezone.localdate().strftime('%d-%m-%Y'), '33', 'N', 'R'))
        self.assertEqual(Decimal(str(inv['val'])), self.total)
        [item] = inv['itms']  # both lines are 18%: one rate entry
        d = item['itm_det']
        self.assertEqual((d['rt'], Decimal(str(d['txval'])), d['iamt']), (18, Decimal('22875'), 0))
        self.assertEqual(Decimal(str(d['camt'])) + Decimal(str(d['samt'])), Decimal('4117.50'))
        hsn = {(r['hsn_sc'], r['uqc']): r for r in data['hsn']['hsn_b2b']}
        self.assertEqual(set(hsn), {('7318', 'NOS'), ('7214', 'KGS')})
        self.assertEqual(hsn[('7318', 'NOS')]['qty'], 600)
        self.assertNotIn('hsn_b2c', data['hsn'])
        [docs] = data['doc_issue']['doc_det']
        self.assertEqual((docs['doc_num'], docs['docs'][0]['from'], docs['docs'][0]['totnum']), (1, self.inv['number'], 1))

    def test_credit_note_goes_to_cdnr_and_nets_hsn(self):
        self.post(self.ok(self.ret(100)))
        data = self.gstr1()
        [party] = data['cdnr']
        [note] = party['nt']
        self.assertEqual((note['ntty'], note['nt_num'][:3]), ('C', 'CN-'))
        hsn = {r['hsn_sc']: r for r in data['hsn']['hsn_b2b']}
        self.assertEqual(hsn['7318']['qty'], 500)
        self.assertEqual(Decimal(str(hsn['7318']['txval'])), Decimal('3125'))  # 3750 - 625
        self.assertIn(5, [d['doc_num'] for d in data['doc_issue']['doc_det']])

    def test_unregistered_customers_are_b2c(self):
        Company.objects.filter(pk=self.company.pk).update(gstin=None)
        data = self.gstr1()
        self.assertNotIn('b2b', data)
        [row] = data['b2cs']
        self.assertEqual((row['sply_ty'], row['pos'], row['typ'], row['rt']), ('INTRA', '33', 'OE', 18))
        self.assertIn('hsn_b2c', data['hsn'])
        # Inter-state and over ₹1 lakh: B2CL, invoice by invoice.
        SalesInvoice.objects.filter(pk=self.inv['id']).update(is_interstate=True, place_of_supply='29', total=Decimal('150000'))
        data = self.gstr1()
        self.assertEqual(data['b2cl'][0]['pos'], '29')
        self.assertNotIn('b2cs', data)

    def test_cancelled_invoices_count_in_table_13_only(self):
        self.ok(self.api('post', f"/sales-invoices/{self.inv['id']}/cancel", {'reason': 'Duplicate'}))
        data = self.gstr1()
        self.assertNotIn('b2b', data)
        docs = data['doc_issue']['doc_det'][0]['docs'][0]
        self.assertEqual((docs['totnum'], docs['cancel'], docs['net_issue']), (1, 1, 0))

    def test_other_months_are_left_out(self):
        self.assertEqual(json.loads(self.file('gstr1', period='2020-01')).get('b2b'), None)


class Gstr3bTests(ReturnTestCase):
    def test_outward_supplies_net_of_notes(self):
        note = self.post(self.ok(self.ret(100)))
        data = self.gstr3b()
        self.assertEqual(data['ret_period'], FP)
        out = data['sup_details']['osup_det']
        self.assertEqual(Decimal(str(out['txval'])), Decimal('22875') - Decimal(note['taxable_value']))
        self.assertEqual(out['iamt'], 0)
        self.assertEqual(data['inter_sup']['unreg_details'], [])

    def test_itc_from_the_buyers_bills(self):
        self.login(self.admin.user)  # Vaigai, which posted Kaveri's bill
        itc = self.gstr3b()['itc_elg']['itc_net']
        self.assertEqual(Decimal(str(itc['camt'])) + Decimal(str(itc['samt'])), Decimal(self.bill['cgst']) + Decimal(self.bill['sgst']))
        summary = self.ok(self.api('get', f'/gst-returns/summary?period={PERIOD}'))
        self.assertTrue(any('GSTR-2B' in c['message'] for c in summary['checks']))


class SummaryAndTallyTests(ReturnTestCase):
    def test_summary(self):
        summary = self.ok(self.api('get', f'/gst-returns/summary?period={PERIOD}'))
        b2b = next(s for s in summary['gstr1'] if s['key'] == 'b2b')
        self.assertEqual((b2b['count'], Decimal(b2b['taxable'])), (1, Decimal('22875')))
        row = next(r for r in summary['gstr3b'] if r['key'] == '3.1a')
        self.assertEqual(Decimal(row['taxable']), Decimal('22875'))

    def test_tally_vouchers_balance_and_ledgers_carry_gstin(self):
        self.post(self.ok(self.ret(100)))
        self.ok(self.pay('received', self.company, 5000, [(self.inv['id'], 5000)]))
        root = ET.fromstring(self.file('tally'))
        vouchers = root.findall('.//VOUCHER')
        self.assertEqual(sorted(v.get('VCHTYPE') for v in vouchers), ['Credit Note', 'Receipt', 'Sales'])
        for v in vouchers:
            total = sum(Decimal(a.text) for a in v.findall('.//AMOUNT'))
            self.assertEqual(total, 0, v.findtext('VOUCHERNUMBER'))
        ledger = root.find('.//LEDGER')
        self.assertEqual((ledger.findtext('PARENT'), ledger.findtext('PARTYGSTIN')), ('Sundry Debtors', self.company.gstin))

    def test_buyer_side_tally_has_the_purchase(self):
        self.login(self.admin.user)
        root = ET.fromstring(self.file('tally'))
        [purchase] = [v for v in root.findall('.//VOUCHER') if v.get('VCHTYPE') == 'Purchase']
        self.assertEqual(purchase.findtext('VOUCHERNUMBER'), self.inv['number'])  # the supplier's invoice number
        self.assertEqual(sum(Decimal(a.text) for a in purchase.findall('.//AMOUNT')), 0)


class AccessTests(ReturnTestCase):
    def test_needs_ledger_view_a_gstin_and_a_valid_period(self):
        sales = add_member(self.supplier, "sales@kaveri.example", "sales_manager").user
        self.as_supplier(sales)
        self.refused(self.api('get', f'/gst-returns/summary?period={PERIOD}'), 403)
        self.as_supplier()
        self.assertIn("YYYY-MM", self.refused(self.api('get', '/gst-returns/summary?period=Nov-2026')))
        Company.objects.filter(pk=self.supplier.pk).update(gstin=None)
        self.assertIn("GSTIN", self.refused(self.api('get', f'/gst-returns/summary?period={PERIOD}')))
