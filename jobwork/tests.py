"""Job work through /api/v1: sending, receiving back, the s.143 clock,
ITC-04 and the challan PDF."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from accounts.models import Company
from inventory.models import Item, StockEntry
from marketplace.documents import financial_year
from organizations.models import BusinessRelationship
from organizations.tests import add_member, make_organization
from plans import erp
from printouts.tests import STORAGE, text_of
from purchase.tests import INTERSTATE_GSTIN, PurchaseTestCase

from .models import JobWorkChallan

FY = financial_year(timezone.localdate())


class JobWorkTestCase(PurchaseTestCase):
    """Vaigai (Growth) sends EN8 bar to Kaveri for zinc plating. 100 kg of bar at ₹80 in the Main store."""
    plan = erp.GROWTH

    def setUp(self):
        super().setUp()
        StockEntry.objects.create(company=self.company, item=self.bar, warehouse=self.store, kind='opening', quantity=100,
                                  rate=Decimal('80'), entry_date=timezone.localdate() - timedelta(days=1))
        self.plated = Item.objects.create(company=self.company, code="BAR-ZN", name="EN8 bar, zinc plated", kind="semi_finished",
                                          unit="KGS", hsn_code="7214")

    def send(self, qty='40', **extra):
        return self.api('post', '/job-work', {'job_worker_id': self.supplier.pk, 'process': 'Zinc plating', 'from_warehouse_id': self.store.pk,
                                              'lines': [{'item_id': self.bar.pk, 'quantity': qty}], **extra})

    def receive(self, challan, **line):
        return self.api('post', f"/job-work/{challan['id']}/receipts", {
            'job_worker_challan': 'KC-JW-11', 'lines': [{'challan_line_id': challan['lines'][0]['id'], **line}]})

    def stock(self, item, kind=None, warehouse=None):
        entries = StockEntry.objects.filter(item=item)
        if kind:
            entries = entries.filter(warehouse__kind=kind)
        if warehouse:
            entries = entries.filter(warehouse=warehouse)
        return sum((e.quantity for e in entries), Decimal('0'))


class SendAndReceiveTests(JobWorkTestCase):
    def test_goods_go_to_the_job_worker_and_come_back_processed(self):
        challan = self.ok(self.send())
        self.assertEqual((challan['number'], challan['status'], Decimal(challan['value'])), (f"JW-{FY}-0001", 'open', Decimal('3200')))
        self.assertEqual((self.stock(self.bar, warehouse=self.store), self.stock(self.bar, kind='job_work')), (Decimal('60'), Decimal('40')))
        # 25 kg plated back with 1 kg lost; ₹5/kg plating.
        challan = self.ok(self.receive(challan, input_quantity='25', waste_quantity='1', output_item_id=self.plated.pk,
                                       output_quantity='25', charge_per_unit='5'))
        self.assertEqual((challan['status'], Decimal(challan['lines'][0]['pending_quantity'])), ('partly', Decimal('14')))
        self.assertEqual(self.stock(self.bar, kind='job_work'), Decimal('14'))
        self.assertEqual(self.stock(self.plated, warehouse=self.store), Decimal('25'))
        back = StockEntry.objects.get(item=self.plated)
        self.assertEqual(back.rate, Decimal('88.20'))  # (26 kg x 80 + 25 x 5) / 25: the lost kilo's cost is carried
        self.assertIn("only 14", self.refused(self.receive(challan, input_quantity='15')))
        challan = self.ok(self.receive(challan, input_quantity='14'))  # same item back, same quantity by default
        self.assertEqual(challan['status'], 'closed')
        self.assertEqual(self.stock(self.bar, kind='job_work'), 0)
        self.assertEqual(self.stock(self.bar, warehouse=self.store), Decimal('74'))

    def test_checks_on_sending(self):
        self.assertIn("only 100", self.refused(self.send('101')))
        self.assertIn("what the job worker will do", self.refused(self.send(process=' ')))
        invited, _ = make_organization("Vega", INTERSTATE_GSTIN, "admin@vega.example")
        BusinessRelationship.objects.create(buyer=self.company, supplier=invited, initiated_by=self.company, status='invited')
        self.refused(self.send(job_worker_id=invited.pk))

    def test_cancel_only_before_anything_comes_back(self):
        challan = self.ok(self.send())
        self.assertIn("why", self.refused(self.api('post', f"/job-work/{challan['id']}/cancel", {'reason': ''})))
        self.ok(self.api('post', f"/job-work/{challan['id']}/cancel", {'reason': 'Job worker busy'}))
        self.assertEqual(self.stock(self.bar, warehouse=self.store), Decimal('100'))
        challan = self.ok(self.send())
        self.ok(self.receive(challan, input_quantity='5'))
        self.assertIn("can't be cancelled", self.refused(self.api('post', f"/job-work/{challan['id']}/cancel", {'reason': 'x'})))

    def test_the_one_year_clock(self):
        challan = self.ok(self.send())
        JobWorkChallan.objects.filter(pk=challan['id']).update(challan_date=timezone.localdate() - timedelta(days=340))
        self.assertEqual(self.ok(self.api('get', '/job-work?status=open'))[0]['age_flag'], 'due_soon')
        JobWorkChallan.objects.filter(pk=challan['id']).update(challan_date=timezone.localdate() - timedelta(days=400))
        self.assertEqual(self.ok(self.api('get', '/job-work?status=open'))[0]['age_flag'], 'overdue')


class Itc04AndPdfTests(JobWorkTestCase):
    def test_itc04_tables(self):
        challan = self.ok(self.send())
        self.ok(self.receive(challan, input_quantity='30', waste_quantity='2'))
        today = timezone.localdate().isoformat()
        sent = self.client.get(f'/api/v1/job-work/itc04?start={today}&end={today}&table=sent').content.decode()
        self.assertIn(challan['number'], sent)
        self.assertIn(self.supplier.gstin, sent)
        self.assertIn('Zinc plating', sent)
        received = self.client.get(f'/api/v1/job-work/itc04?start={today}&end={today}&table=received').content.decode()
        self.assertIn('KC-JW-11', received)
        self.assertIn(',30,2', received)

    @STORAGE
    def test_challan_pdf(self):
        challan = self.ok(self.send())
        text = text_of(self.client.get(f"/api/v1/job-work/{challan['id']}/pdf").content)
        for expected in ('DELIVERY CHALLAN', challan['number'], 'section 143', 'Zinc plating'):
            self.assertIn(expected, text, expected)


class AccessTests(JobWorkTestCase):
    def test_plan_and_roles(self):
        keeper = add_member(self.company, "keeper@vaigai.example", "store_keeper").user
        self.login(keeper)
        self.refused(self.send(), 403)
        store = add_member(self.company, "store@vaigai.example", "store").user
        self.login(store)
        self.ok(self.send())
        Company.objects.filter(pk=self.company.pk).update(erp_plan=erp.STARTER)
        self.login(self.admin.user)
        self.refused(self.send(), 403)  # job work is a Growth feature
