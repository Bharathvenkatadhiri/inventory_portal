"""Credit and debit notes through /api/v1: limits, the s.34 deadline, stock
for returns, the effect on what's owed, and the printed note."""
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from finance.tests import FinanceTestCase
from inventory.models import StockEntry
from marketplace.documents import financial_year
from organizations.tests import add_member
from printouts.tests import STORAGE, text_of
from sales.models import SalesInvoice

from . import services

FY = financial_year(timezone.localdate())


class NoteTestCase(FinanceTestCase):
    """Kaveri (supplier, signed in) posted an invoice to Vaigai for 600 bolts
    at 6.25 and 250 kg of bar at 76.50; Vaigai received them into its Main
    store and posted the matching bill."""

    def setUp(self):
        super().setUp()
        self.room = self.ok(self.api('get', f"/notes/room?side=sales&document_id={self.inv['id']}"))
        self.bolt_line, self.bar_line = (l['line_id'] for l in self.room['lines'])

    def note(self, kind='credit', reason='goods_returned', lines=(), side='sales', document_id=None, **extra):
        body = {'side': side, 'kind': kind, 'reason': reason, 'document_id': document_id or self.inv['id'],
                'lines': [{'line_id': lid, **values} for lid, values in lines], **extra}
        return self.api('post', '/notes', body)

    def ret(self, qty, **extra):
        return self.note(lines=[(self.bolt_line, {'quantity': str(qty)})], warehouse_id=self.fs.pk, **extra)

    def post(self, note):
        return self.ok(self.api('post', f"/notes/{note['id']}/post"))

    def invoice_now(self):
        return self.ok(self.api('get', f"/sales-invoices/{self.inv['id']}"))

    def stock(self, item, warehouse):
        return sum(e.quantity for e in StockEntry.objects.filter(item=item, warehouse=warehouse))


class SalesCreditNoteTests(NoteTestCase):
    def test_return_brings_stock_back_and_reduces_what_is_owed(self):
        before = self.stock(self.s_bolt, self.fs)
        draft = self.ok(self.ret(100))
        self.assertEqual((draft['number'], draft['status']), ('', 'draft'))
        self.assertEqual(Decimal(draft['taxable_value']), Decimal('625'))  # 100 x 6.25
        note = self.post(draft)
        self.assertEqual(note['number'], f"CN-{FY}-0001")
        self.assertEqual(self.stock(self.s_bolt, self.fs), before + 100)
        self.assertTrue(StockEntry.objects.filter(kind='sales_return', reference=note['number']).exists())
        inv = self.invoice_now()
        self.assertEqual(Decimal(inv['adjusted_total']), self.total - Decimal(note['total']))
        self.assertEqual(Decimal(inv['outstanding']), self.total - Decimal(note['total']))

    def test_cant_return_more_than_billed_even_across_drafts(self):
        self.assertIn("only 600", self.refused(self.ret(601)))
        self.ok(self.ret(400))
        self.assertIn("only 200", self.refused(self.ret(201)))
        self.assertEqual(Decimal(self.ok(self.api('get', f"/notes/room?side=sales&document_id={self.inv['id']}"))['lines'][0]['returnable']), 200)

    def test_value_only_discount_is_capped_by_the_line(self):
        self.assertIn("can still be credited", self.refused(self.note(reason='discount', lines=[(self.bar_line, {'taxable_value': '19125.01'})])))
        note = self.post(self.ok(self.note(reason='discount', lines=[(self.bar_line, {'taxable_value': '1000'})])))
        self.assertEqual((Decimal(note['taxable_value']), Decimal(note['lines'][0]['quantity'])), (Decimal('1000'), Decimal('0')))
        self.assertEqual(Decimal(note['cgst']) + Decimal(note['sgst']), Decimal('180'))
        self.assertFalse(StockEntry.objects.filter(kind='sales_return').exists())

    def test_reasons_must_fit_and_a_return_needs_a_warehouse(self):
        self.assertIn("reason", self.refused(self.note(kind='debit', reason='goods_returned', lines=[(self.bolt_line, {'quantity': '1'})])))
        self.assertIn("warehouse", self.refused(self.note(lines=[(self.bolt_line, {'quantity': '1'})])))

    def test_credit_note_deadline(self):
        self.assertEqual(services.credit_note_deadline(date(2025, 4, 10)), date(2026, 11, 30))
        self.assertEqual(services.credit_note_deadline(date(2026, 3, 31)), date(2026, 11, 30))
        old = timezone.localdate().replace(year=timezone.localdate().year - 2)
        SalesInvoice.objects.filter(pk=self.inv['id']).update(invoice_date=old)
        self.assertIn("s.34", self.refused(self.ret(1)))
        # A debit note has no such deadline.
        self.ok(self.note(kind='debit', reason='rate_change', lines=[(self.bolt_line, {'taxable_value': '50'})]))

    def test_dates(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertIn("future", self.refused(self.ret(1, note_date=tomorrow)))

    def test_credit_after_full_payment_shows_money_due_back(self):
        self.ok(self.pay('received', self.company, self.total, [(self.inv['id'], self.total)]))
        self.post(self.ok(self.ret(100)))
        inv = self.invoice_now()
        self.assertEqual(inv['payment_status'], 'credit')
        self.assertIn("due back", inv['payment_status_label'])
        self.assertLess(Decimal(inv['outstanding']), 0)

    def test_invoice_with_notes_wont_cancel_and_cancelling_a_return_reverses_stock(self):
        note = self.post(self.ok(self.ret(50)))
        self.assertIn("notes against this invoice", self.refused(self.api('post', f"/sales-invoices/{self.inv['id']}/cancel", {'reason': 'x'})))
        before = self.stock(self.s_bolt, self.fs)
        self.assertIn("why", self.refused(self.api('post', f"/notes/{note['id']}/cancel", {'reason': ''})))
        cancelled = self.ok(self.api('post', f"/notes/{note['id']}/cancel", {'reason': 'Return rejected on inspection'}))
        self.assertEqual((cancelled['status'], cancelled['number']), ('cancelled', note['number']))
        self.assertEqual(self.stock(self.s_bolt, self.fs), before - 50)
        self.assertEqual(Decimal(self.invoice_now()['adjusted_total']), self.total)


class DebitNoteTests(NoteTestCase):
    def test_debit_note_adds_to_what_is_owed(self):
        note = self.post(self.ok(self.note(kind='debit', reason='additional_charges', lines=[(self.bar_line, {'taxable_value': '500'})])))
        self.assertEqual(note['number'], f"DN-{FY}-0001")
        self.assertEqual(Decimal(self.invoice_now()['adjusted_total']), self.total + Decimal(note['total']))
        # A later credit can use the debited room too.
        self.ok(self.note(reason='discount', lines=[(self.bar_line, {'taxable_value': '19625'})]))


class SupplierNoteTests(NoteTestCase):
    """The buyer records Kaveri's credit note against Kaveri's bill."""

    def setUp(self):
        super().setUp()
        self.login(self.admin.user)
        room = self.ok(self.api('get', f"/notes/room?side=purchase&document_id={self.bill['id']}"))
        self.bill_bolt = room['lines'][0]['line_id']

    def supplier_note(self, qty, number='KC-CN-7', **extra):
        return self.note(side='purchase', document_id=self.bill['id'], party_note_number=number,
                         lines=[(self.bill_bolt, {'quantity': str(qty)})], warehouse_id=self.store.pk, **extra)

    def test_purchase_return_takes_stock_out_and_reduces_the_bill(self):
        before = self.stock(self.bolt, self.store)
        note = self.post(self.ok(self.supplier_note(100)))
        self.assertEqual(note['number'], f"SN-{FY}-0001")
        self.assertEqual(self.stock(self.bolt, self.store), before - 100)
        bill = self.ok(self.api('get', f"/purchase-bills/{self.bill['id']}"))
        self.assertEqual(Decimal(bill['adjusted_total']), Decimal(self.bill['total']) - Decimal(note['total']))
        payables = self.ok(self.api('get', '/accounts/outstanding?direction=made'))
        self.assertEqual(Decimal(payables['total']), Decimal(bill['adjusted_total']))

    def test_supplier_number_required_and_entered_once(self):
        self.assertIn("supplier's", self.refused(self.supplier_note(1, number='')))
        self.ok(self.supplier_note(1))
        self.assertIn("already recorded", self.refused(self.supplier_note(1, number='kc-cn-7')))

    def test_cant_send_back_more_than_is_in_stock(self):
        StockEntry.objects.create(company=self.company, item=self.bolt, warehouse=self.store, kind='dispatch', quantity=-550,
                                  rate=Decimal('6.25'), entry_date=timezone.localdate())
        draft = self.ok(self.supplier_note(100))
        self.assertIn("only 50", self.refused(self.api('post', f"/notes/{draft['id']}/post")))


class AccessAndPdfTests(NoteTestCase):
    def test_roles(self):
        executive = add_member(self.supplier, "exec@kaveri.example", "sales_executive").user
        self.as_supplier(executive)
        self.ok(self.api('get', '/notes?side=sales'))  # can see
        self.refused(self.ret(1), 403)  # can't issue (needs invoice.create)
        operator = add_member(self.supplier, "op@kaveri.example", "machine_operator").user
        self.as_supplier(operator)
        self.refused(self.api('get', '/notes?side=sales'), 403)

    @STORAGE
    def test_pdf_for_issuer_and_for_the_customer_once_posted(self):
        draft = self.ok(self.ret(10))
        response = self.client.get(f"/api/v1/notes/{draft['id']}/pdf")
        self.assertIn('DRAFT', text_of(response.content))
        self.login(self.admin.user)
        self.assertEqual(self.client.get(f"/api/v1/notes/{draft['id']}/pdf").status_code, 404)
        self.as_supplier()
        note = self.post(draft)
        self.login(self.admin.user)
        text = text_of(self.client.get(f"/api/v1/notes/{note['id']}/pdf").content)
        for expected in ('CREDIT NOTE', note['number'], self.inv['number'], 'Goods returned'):
            self.assertIn(expected, text, expected)
