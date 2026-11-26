"""Payments against invoices and bills, outstanding and ageing, through /api/v1."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from organizations.models import Membership, Permission, Role
from organizations.tests import add_member, make_organization
from plans import erp
from purchase.models import PurchaseBill
from purchase.tests import INTERSTATE_GSTIN
from sales.models import SalesInvoice
from sales.tests import SalesTestCase

from .models import Payment


class FinanceTestCase(SalesTestCase):
    """Kaveri (supplier) has posted an invoice to Vaigai (buyer) for 600 bolts
    and 250 kg of bar; Vaigai has received them and posted Kaveri's bill.
    Signed in as the supplier."""

    def setUp(self):
        super().setUp()
        self.po, order = self.accepted()
        self.ok(self.send(order, ['600', '250']))
        invoice = self.ok(self.invoice(order, ['600', '250']))
        self.inv = self.ok(self.api('post', f"/sales-invoices/{invoice['id']}/post"))
        self.total = Decimal(self.inv['total'])
        # The buyer receives and enters the bill.
        self.login(self.admin.user)
        po = self.ok(self.api('get', f"/purchase-orders/{self.po['id']}"))
        self.ok(self.receive(po, ['600', '250']))
        bill = self.ok(self.api('post', '/purchase-bills', self.bill_body(po, ['600', '250'], invoice=self.inv['number'])))
        self.bill = self.ok(self.api('post', f"/purchase-bills/{bill['id']}/submit"))
        self.as_supplier()

    def pay(self, direction, party, amount, allocations=(), **extra):
        body = {'direction': direction, 'party_id': party.pk, 'amount': str(amount),
                'allocations': [{'document_id': doc_id, 'amount': str(a)} for doc_id, a in allocations], **extra}
        return self.api('post', '/payments', body)

    def receivables(self):
        return self.ok(self.api('get', '/accounts/outstanding?direction=received'))


class ReceiptTests(FinanceTestCase):
    def test_part_then_full_receipt(self):
        start = self.receivables()
        self.assertEqual(Decimal(start['total']), self.total)
        self.assertEqual(start['documents'][0]['status'], 'unpaid')
        receipt = self.ok(self.pay('received', self.company, 10000, [(self.inv['id'], 10000)], reference='UTR123', mode='bank'))
        self.assertEqual((receipt['status'], receipt['number'][:5]), ('posted', 'RCPT-'))
        invoice = self.ok(self.api('get', f"/sales-invoices/{self.inv['id']}"))
        self.assertEqual((invoice['payment_status'], Decimal(invoice['paid'])), ('partly_paid', Decimal('10000')))
        self.assertEqual(Decimal(invoice['outstanding']), self.total - 10000)
        rest = self.total - 10000
        self.ok(self.pay('received', self.company, rest, [(self.inv['id'], rest)]))
        self.assertEqual(self.ok(self.api('get', f"/sales-invoices/{self.inv['id']}"))['payment_status'], 'paid')
        self.assertEqual(self.receivables()['documents'], [])

    def test_cant_settle_more_than_outstanding_or_than_was_received(self):
        self.assertIn("left to settle", self.refused(self.pay('received', self.company, self.total + 5, [(self.inv['id'], self.total + 5)])))
        self.assertIn("more than", self.refused(self.pay('received', self.company, 100, [(self.inv['id'], 200)])))
        self.refused(self.pay('received', self.company, 0))
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertIn("future", self.refused(self.pay('received', self.company, 100, payment_date=tomorrow)))

    def test_unallocated_money_is_an_advance(self):
        self.ok(self.pay('received', self.company, 5000))
        party = self.receivables()['parties'][0]
        self.assertEqual((Decimal(party['advance']), Decimal(party['outstanding'])), (Decimal('5000'), self.total))

    def test_cash_receipts_of_two_lakh_or_more_are_refused(self):
        self.assertIn("269ST", self.refused(self.pay('received', self.company, 200000, mode='cash')))
        self.ok(self.pay('received', self.company, 199999, mode='cash'))

    def test_only_this_partys_documents_and_known_parties(self):
        stranger, _ = make_organization("Vega", INTERSTATE_GSTIN, "admin@vega.example")
        self.assertIn("customers", self.refused(self.pay('received', stranger, 100)))
        # A bill id isn't an invoice of this customer.
        self.assertIn("posted invoices", self.refused(self.pay('received', self.company, 100, [(self.bill['id'] + 999, 100)])))

    def test_cancel_reopens_and_a_paid_invoice_wont_cancel(self):
        receipt = self.ok(self.pay('received', self.company, 1000, [(self.inv['id'], 1000)]))
        self.assertIn("receipt first", self.refused(self.api('post', f"/sales-invoices/{self.inv['id']}/cancel", {'reason': 'x'})))
        self.assertIn("bounced", self.refused(self.api('post', f"/payments/{receipt['id']}/cancel", {'reason': ''})))
        self.ok(self.api('post', f"/payments/{receipt['id']}/cancel", {'reason': 'Cheque bounced'}))
        self.assertEqual(self.ok(self.api('get', f"/sales-invoices/{self.inv['id']}"))['payment_status'], 'unpaid')
        self.ok(self.api('post', f"/sales-invoices/{self.inv['id']}/cancel", {'reason': 'Wrong rate'}))

    def test_overdue_invoices_age(self):
        SalesInvoice.objects.filter(pk=self.inv['id']).update(due_date=timezone.localdate() - timedelta(days=40))
        out = self.receivables()
        self.assertEqual(out['documents'][0]['status'], 'overdue')
        self.assertEqual(out['documents'][0]['status_label'], 'Overdue 40 days')
        self.assertEqual(out['documents'][0]['bucket'], '31_60')
        self.assertEqual(Decimal(out['overdue']), self.total)
        self.assertEqual(Decimal(out['parties'][0]['d31_60']), self.total)


class SupplierPaymentTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.admin.user)

    def payables(self):
        return self.ok(self.api('get', '/accounts/outstanding?direction=made'))

    def test_pay_a_bill_on_starter_without_approval(self):
        docs = self.payables()['documents']
        self.assertEqual(docs[0]['number'], self.inv['number'])  # the supplier's invoice number
        payment = self.ok(self.pay('made', self.supplier, self.bill['total'], [(self.bill['id'], self.bill['total'])]))
        self.assertEqual((payment['status'], payment['number'][:4]), ('posted', 'PAY-'))
        self.assertEqual(self.ok(self.api('get', f"/purchase-bills/{self.bill['id']}"))['payment_status'], 'paid')
        self.assertIn("payment first", self.refused(self.api('post', f"/purchase-bills/{self.bill['id']}/cancel")))

    def test_cash_payment_over_ten_thousand_warns(self):
        payment = self.ok(self.pay('made', self.supplier, 15000, mode='cash'))
        self.assertTrue(any("40A(3)" in w for w in payment['warnings']))
        self.assertEqual(self.ok(self.pay('made', self.supplier, 9000, mode='cash'))['warnings'], [])


class ApprovalTests(FinanceTestCase):
    plan = erp.GROWTH

    def setUp(self):
        super().setUp()
        # A clerk who records payments but can't approve them.
        clerk_role = Role.objects.create(company=self.company, key='clerk', name='Accounts clerk')
        clerk_role.permissions.set(Permission.objects.filter(code__in=['payment.record', 'ledger.view']))
        self.clerk = add_member(self.company, "clerk@vaigai.example", "store").user
        Membership.objects.filter(user=self.clerk).update(role=clerk_role)
        self.login(self.clerk)

    def test_outgoing_payments_wait_for_approval_and_reserve_the_amount(self):
        options = self.ok(self.api('get', '/accounts/payment-options?direction=made'))
        self.assertTrue(options['needs_approval'])
        amount = Decimal(self.bill['total'])
        payment = self.ok(self.pay('made', self.supplier, amount, [(self.bill['id'], amount)]))
        self.assertEqual(payment['status'], 'pending_approval')
        bill = self.ok(self.api('get', f"/purchase-bills/{self.bill['id']}"))
        self.assertEqual((bill['payment_status'], Decimal(bill['payment_pending'])), ('unpaid', amount))
        # The amount is reserved: it can't be paid twice while waiting.
        self.assertIn("left to settle", self.refused(self.pay('made', self.supplier, 1, [(self.bill['id'], 1)])))
        self.refused(self.api('post', f"/payments/{payment['id']}/approve"), 403)
        accountant = add_member(self.company, "accounts@vaigai.example", "accounts").user
        self.login(accountant)
        self.assertEqual(self.ok(self.api('post', f"/payments/{payment['id']}/approve"))['status'], 'posted')
        self.assertEqual(self.ok(self.api('get', f"/purchase-bills/{self.bill['id']}"))['payment_status'], 'paid')

    def test_rejected_payment_is_cancelled_and_frees_the_bill(self):
        payment = self.ok(self.pay('made', self.supplier, 100, [(self.bill['id'], 100)]))
        self.login(self.admin.user)
        rejected = self.ok(self.api('post', f"/payments/{payment['id']}/reject", {'reason': 'Wrong account'}))
        self.assertEqual((rejected['status'], rejected['rejection_reason']), ('cancelled', 'Wrong account'))
        self.assertEqual(Decimal(self.ok(self.api('get', f"/purchase-bills/{self.bill['id']}"))['payment_pending']), 0)

    def test_receipts_never_need_approval(self):
        self.as_supplier()
        self.assertFalse(self.ok(self.api('get', '/accounts/payment-options?direction=received'))['needs_approval'])


class AccessTests(FinanceTestCase):
    def test_roles_without_accounts_and_other_companies(self):
        receipt = self.ok(self.pay('received', self.company, 100))
        sales = add_member(self.supplier, "sales@kaveri.example", "sales_executive").user
        self.as_supplier(sales)
        self.refused(self.api('get', '/payments'), 403)
        self.refused(self.api('get', '/accounts/outstanding?direction=received'), 403)
        other, other_admin = make_organization("Vega", INTERSTATE_GSTIN, "admin@vega.example")
        self.login(other_admin.user)
        self.assertEqual(self.ok(self.api('get', '/payments')), [])
        self.refused(self.api('post', f"/payments/{receipt['id']}/cancel", {'reason': 'x'}), 404)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertTrue(PurchaseBill.objects.exists())
