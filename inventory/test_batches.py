"""Batch and serial number tracking through /api/v1: items, opening stock,
receipts, QC, dispatch (first to expire first), production and its trace,
job work, returns, scanning a label and printing labels."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from adjustments.tests import NoteTestCase
from jobwork.tests import JobWorkTestCase
from plans import erp
from production.tests import ProductionTestCase
from purchase.tests import PurchaseTestCase
from sales.tests import SalesTestCase

from .models import Batch, Item, StockEntry

TODAY = timezone.localdate()


def item_body(item, **extra):
    return {'code': item.code, 'name': item.name, 'kind': item.kind, 'unit': item.unit, 'hsn_code': item.hsn_code,
            'gst_rate': str(item.gst_rate), **extra}


def per_batch(item, warehouse=None):
    """{batch number: quantity on hand}, leaving out empty ones."""
    entries = StockEntry.objects.filter(item=item, batch__isnull=False)
    if warehouse is not None:
        entries = entries.filter(warehouse=warehouse)
    totals = {}
    for e in entries.select_related('batch'):
        totals[e.batch.number] = totals.get(e.batch.number, Decimal('0')) + e.quantity
    return {k: v for k, v in totals.items() if v}


def track(item, how, shelf_life=None):
    Item.objects.filter(pk=item.pk).update(tracking=how, shelf_life_days=shelf_life)
    item.refresh_from_db()


class ItemSettingTests(PurchaseTestCase):
    plan = erp.GROWTH

    def test_tracking_is_set_on_the_item_and_needs_the_plan(self):
        item = self.ok(self.api('put', f"/items/{self.bar.pk}", item_body(self.bar, tracking='batch', shelf_life_days=365)))
        self.assertEqual((item['tracking'], item['shelf_life_days']), ('batch', 365))
        self.assertIn("not tracked, by batch", self.refused(self.api('put', f"/items/{self.bar.pk}", item_body(self.bar, tracking='lot'))))
        options = self.ok(self.api('get', '/inventory/options'))
        self.assertTrue(options['batches_available'])
        self.assertIn('batch_serial', self.ok(self.api('get', '/me'))['active']['features'])

        self.company.erp_plan = erp.STARTER
        self.company.save(update_fields=['erp_plan'])
        self.assertIn("Growth and Pro", self.refused(self.api('put', f"/items/{self.bolt.pk}", item_body(self.bolt, tracking='serial'))))
        # An item already tracked keeps it after a downgrade, so its stock stays right.
        self.ok(self.api('put', f"/items/{self.bar.pk}", item_body(self.bar, tracking='batch', name='EN8 bright bar')))

    def test_tracking_cant_change_while_there_is_stock(self):
        StockEntry.objects.create(company=self.company, item=self.bolt, warehouse=self.store, kind='opening', quantity=10,
                                  rate=1, entry_date=TODAY)
        self.assertIn("has stock", self.refused(self.api('put', f"/items/{self.bolt.pk}", item_body(self.bolt, tracking='batch'))))

    def test_opening_stock_per_batch_and_serial(self):
        track(self.bar, 'batch', shelf_life=30)
        body = {'item_id': self.bar.pk, 'warehouse_id': self.store.pk, 'rate': '78'}
        self.assertIn("enter the batch number", self.refused(self.api('put', '/stock/opening', {**body, 'quantity': '50'})))
        first = self.ok(self.api('put', '/stock/opening', {**body, 'quantity': '50', 'batch_number': 'H-101',
                                                           'manufactured_on': (TODAY - timedelta(days=10)).isoformat()}))
        self.assertEqual((first['batch_number'], first['expires_on']), ('H-101', (TODAY + timedelta(days=20)).isoformat()))
        self.ok(self.api('put', '/stock/opening', {**body, 'quantity': '30', 'batch_number': 'H-102'}))
        self.ok(self.api('put', '/stock/opening', {**body, 'quantity': '40', 'batch_number': 'h-101'}))  # same batch, corrected
        self.assertEqual(per_batch(self.bar), {'H-101': Decimal('40'), 'H-102': Decimal('30')})
        self.assertEqual(len(self.ok(self.api('get', '/stock/opening'))), 2)

        track(self.bolt, 'serial')
        body = {'item_id': self.bolt.pk, 'warehouse_id': self.store.pk, 'rate': '6'}
        self.ok(self.api('put', '/stock/opening', {**body, 'quantity': '3'}))
        self.assertEqual(sorted(per_batch(self.bolt)), ['BOLT-00001', 'BOLT-00002', 'BOLT-00003'])
        self.ok(self.api('put', '/stock/opening', {**body, 'quantity': '0', 'serials': ['SN-1', 'SN-2']}))
        self.assertEqual(sorted(per_batch(self.bolt)), ['SN-1', 'SN-2'])
        self.assertFalse(Batch.objects.filter(number='BOLT-00001').exists())  # replaced, so gone


class ReceiptTestCase(PurchaseTestCase):
    plan = erp.GROWTH

    def setUp(self):
        super().setUp()
        track(self.bar, 'batch', shelf_life=365)
        track(self.bolt, 'serial')
        self.order = self.issued(lines=[{'item_id': self.bolt.pk, 'quantity': '5'}, {'item_id': self.bar.pk, 'quantity': '250'}])

    def grn(self, bolts=None, bars=None, bolt_qty='0', bar_qty='0'):
        lines = [{'order_line_id': self.order['lines'][0]['id'], 'quantity': bolt_qty, 'batches': bolts or []},
                 {'order_line_id': self.order['lines'][1]['id'], 'quantity': bar_qty, 'batches': bars or []}]
        return self.api('post', f"/purchase-orders/{self.order['id']}/receipts", {'lines': lines})


class ReceiptTests(ReceiptTestCase):
    def test_batches_and_heat_numbers_received(self):
        self.assertIn("add up to 140 but 250", self.refused(self.grn(bar_qty='250', bars=[
            {'number': 'H-7', 'quantity': '100'}, {'number': 'H-8', 'quantity': '40'}])))
        grn = self.ok(self.grn(bar_qty='250', bars=[{'number': 'H-7', 'quantity': '150', 'supplier_batch': 'KC-HT-9'},
                                                     {'number': 'H-8', 'quantity': '100'}]))
        self.assertEqual([(b['number'], Decimal(b['quantity'])) for b in grn['lines'][0]['batches']],
                         [('H-7', Decimal('150')), ('H-8', Decimal('100'))])
        self.assertEqual(per_batch(self.bar, self.store), {'H-7': Decimal('150'), 'H-8': Decimal('100')})
        h7 = Batch.objects.get(number='H-7')
        self.assertEqual((h7.supplier_batch, h7.supplier, h7.origin, h7.reference, h7.expires_on),
                         ('KC-HT-9', self.supplier, 'receipt', grn['number'], TODAY + timedelta(days=365)))
        # Found by the supplier's heat number too.
        self.assertEqual([b['number'] for b in self.ok(self.api('get', '/batches/lookup?code=kc-ht-9'))], ['H-7'])

    def test_a_batch_left_blank_is_numbered_from_the_receipt(self):
        grn = self.ok(self.grn(bar_qty='50'))
        self.assertEqual(per_batch(self.bar), {grn['number']: Decimal('50')})

    def test_serial_numbers_received(self):
        self.assertIn("whole number", self.refused(self.grn(bolt_qty='1.5')))
        self.assertIn("enter 2 serial numbers", self.refused(self.grn(bolt_qty='2', bolts=[{'number': 'S1'}])))
        self.assertIn("entered twice", self.refused(self.grn(bolt_qty='2', bolts=[{'number': 'S1'}, {'number': 's1'}])))
        self.ok(self.grn(bolt_qty='2', bolts=[{'number': 'S1'}, {'number': 'S2'}]))
        self.assertIn("S2 is already used", self.refused(self.grn(bolt_qty='1', bolts=[{'number': 'S2'}])))
        self.ok(self.grn(bolt_qty='3'))  # numbered from the item code
        self.assertEqual(sorted(per_batch(self.bolt)), ['BOLT-00001', 'BOLT-00002', 'BOLT-00003', 'S1', 'S2'])

    def test_untracked_items_refuse_batches(self):
        track(self.bar, 'none')
        self.assertIn("isn't tracked", self.refused(self.grn(bar_qty='10', bars=[{'number': 'H-1'}])))


class QcTests(ReceiptTestCase):
    def setUp(self):
        super().setUp()
        Item.objects.filter(pk__in=[self.bar.pk, self.bolt.pk]).update(inspection_required=True)
        self.bar.refresh_from_db()
        self.bolt.refresh_from_db()

    def lots(self):
        return self.ok(self.api('get', '/qc/lots'))

    def test_each_batch_is_its_own_lot(self):
        self.ok(self.grn(bar_qty='250', bars=[{'number': 'H-7', 'quantity': '150'}, {'number': 'H-8', 'quantity': '100'}]))
        lots = sorted((l for l in self.lots() if l['item_code'] == 'BAR'), key=lambda l: l['batches'][0]['number'])
        self.assertEqual([(l['batches'][0]['number'], Decimal(l['quantity'])) for l in lots], [('H-7', Decimal('150')), ('H-8', Decimal('100'))])
        self.ok(self.api('post', f"/qc/lots/{lots[1]['id']}/inspections", {'accepted': '90', 'rejected': '10', 'remarks': 'Cracks'}))
        self.assertEqual(per_batch(self.bar, self.store), {'H-8': Decimal('90')})
        rejected = StockEntry.objects.filter(item=self.bar, warehouse__kind='rejected')
        self.assertEqual({e.batch.number for e in rejected}, {'H-8'})

    def test_qc_names_the_failed_serial_numbers(self):
        self.ok(self.grn(bolt_qty='3', bolts=[{'number': 'S1'}, {'number': 'S2'}, {'number': 'S3'}]))
        [lot] = [l for l in self.lots() if l['item_code'] == 'BOLT']
        self.assertEqual(sorted(b['number'] for b in lot['batches']), ['S1', 'S2', 'S3'])
        path = f"/qc/lots/{lot['id']}/inspections"
        self.assertIn("serial numbers of the 1 that failed", self.refused(self.api('post', path, {'accepted': '2', 'rejected': '1', 'remarks': 'Thread'})))
        self.ok(self.api('post', path, {'accepted': '2', 'rejected': '1', 'remarks': 'Thread', 'rejected_serials': ['S2']}))
        self.assertEqual(sorted(per_batch(self.bolt, self.store)), ['S1', 'S3'])


class DispatchTests(SalesTestCase):
    """Kaveri (Growth) sends bolts batch by batch: B-OLD expires in 10 days, B-NEW in 100, B-EXP expired yesterday."""
    supplier_plan = erp.GROWTH

    def setUp(self):
        super().setUp()
        track(self.s_bolt, 'batch')
        StockEntry.objects.filter(item=self.s_bolt).delete()
        self.as_supplier()
        for number, quantity, days in (('B-NEW', '500', 100), ('B-OLD', '300', 10), ('B-EXP', '50', -1)):
            self.ok(self.api('put', '/stock/opening', {'item_id': self.s_bolt.pk, 'warehouse_id': self.fs.pk, 'quantity': quantity,
                                                       'rate': '5', 'batch_number': number,
                                                       'expires_on': (TODAY + timedelta(days=days)).isoformat()}))
        self.login(self.admin.user)
        self.po, self.order = self.accepted()

    def test_first_to_expire_goes_first_and_expired_stock_stays(self):
        dc = self.ok(self.send(self.order, ['400', '0']))
        self.assertEqual([(b['number'], Decimal(b['quantity'])) for b in dc['lines'][0]['batches']],
                         [('B-NEW', Decimal('100')), ('B-OLD', Decimal('300'))])
        self.assertEqual(per_batch(self.s_bolt, self.fs), {'B-NEW': Decimal('400'), 'B-EXP': Decimal('50')})
        # 450 on hand, but 50 of it has expired.
        self.assertIn("only 400 NOS in Foundry store can be used (50 more is in expired batches)",
                      self.refused(self.send(self.order, ['450', '0'])))
        expiring = self.ok(self.api('get', '/batches?expiring_within=30'))
        self.assertEqual([b['number'] for b in expiring], ['B-EXP'])
        self.assertTrue(expiring[0]['expired'])

    def test_picked_batches(self):
        line = self.order['lines'][0]['id']
        picked = lambda picks, qty: self.api('post', f"/sales-orders/{self.order['id']}/dispatches",
                                             {'lines': [{'order_line_id': line, 'quantity': qty, 'batches': picks}]})
        self.assertIn("B-EXP expired on", self.refused(picked([{'number': 'B-EXP', 'quantity': '10'}], '10')))
        self.assertIn("add up to 20 but 30", self.refused(picked([{'number': 'B-NEW', 'quantity': '20'}], '30')))
        self.assertIn("B-NONE isn't in stock", self.refused(picked([{'number': 'B-NONE', 'quantity': '5'}], '5')))
        dc = self.ok(picked([{'number': 'b-new', 'quantity': '30'}], '30'))
        self.assertEqual([b['number'] for b in dc['lines'][0]['batches']], ['B-NEW'])
        self.assertEqual(per_batch(self.s_bolt, self.fs)['B-NEW'], Decimal('470'))


class ProductionTraceTests(ProductionTestCase):
    """Bar by batch (H-SOON expires first), gear shafts by serial number."""
    plan = erp.GROWTH

    def setUp(self):
        super().setUp()
        StockEntry.objects.filter(item=self.bar).delete()
        track(self.bar, 'batch')
        track(self.gear, 'serial')
        for number, days in (('H-LATE', 200), ('H-SOON', 20)):
            self.ok(self.api('put', '/stock/opening', {'item_id': self.bar.pk, 'warehouse_id': self.main.pk, 'quantity': '50',
                                                       'rate': '80', 'batch_number': number,
                                                       'expires_on': (TODAY + timedelta(days=days)).isoformat()}))

    def test_components_by_expiry_serials_out_and_the_trace(self):
        entry = self.ok(self.record(4))  # 10 kg of bar
        [bar] = [c for c in entry['consumption'] if c['item_code'] == 'BAR']
        self.assertEqual([(b['number'], Decimal(b['quantity'])) for b in bar['batches']], [('H-SOON', Decimal('10'))])
        self.assertEqual([b['number'] for b in entry['output_batches']], ['GEAR-00001', 'GEAR-00002', 'GEAR-00003', 'GEAR-00004'])
        shaft = Batch.objects.get(number='GEAR-00002')
        detail = self.ok(self.api('get', f"/batches/{shaft.pk}"))
        self.assertEqual([b['number'] for b in detail['made_from']], ['H-SOON'])
        self.assertEqual(detail['batch']['origin'], 'production')
        soon = Batch.objects.get(number='H-SOON')
        detail = self.ok(self.api('get', f"/batches/{soon.pk}"))
        self.assertEqual(len(detail['made_into']), 4)
        self.assertEqual(Decimal(detail['batch']['on_hand']), Decimal('40'))
        # Picked serial numbers for the output, and a picked batch of bar.
        entry = self.ok(self.record(2, output_batches=[{'number': 'G-A'}, {'number': 'G-B'}],
                                    consumption=[{'item_id': self.bar.pk, 'batches': [{'number': 'H-LATE', 'quantity': '5'}]}]))
        self.assertEqual(sorted(b['number'] for b in entry['output_batches']), ['G-A', 'G-B'])
        self.assertEqual(per_batch(self.bar, self.main), {'H-SOON': Decimal('40'), 'H-LATE': Decimal('45')})

    def test_cancel_puts_batches_back(self):
        entry = self.ok(self.record(4))
        self.ok(self.api('post', f"/production/entries/{entry['id']}/cancel", {'reason': 'Wrong BOM'}))
        self.assertEqual(per_batch(self.bar, self.main), {'H-SOON': Decimal('50'), 'H-LATE': Decimal('50')})
        self.assertEqual(per_batch(self.gear), {})
        # A cancelled run doesn't show in the trace.
        self.assertEqual(self.ok(self.api('get', f"/batches/{Batch.objects.get(number='H-SOON').pk}"))['made_into'], [])

    def test_lookup_and_labels(self):
        self.ok(self.record(2))
        shaft = Batch.objects.get(number='GEAR-00001')
        self.assertEqual([b['number'] for b in self.ok(self.api('get', f"/batches/lookup?code={shaft.label_code}"))], ['GEAR-00001'])
        self.assertEqual([b['number'] for b in self.ok(self.api('get', '/batches/lookup?code=gear-00002'))], ['GEAR-00002'])
        response = self.client.get(f"/api/v1/batch-labels/pdf?ids={shaft.pk},{Batch.objects.get(number='H-SOON').pk}&copies=2")
        self.assertEqual((response.status_code, response['Content-Type']), (200, 'application/pdf'))
        self.assertTrue(response.content.startswith(b'%PDF'))
        self.assertEqual(self.client.get("/api/v1/batch-labels/pdf?ids=999999").status_code, 404)


class JobWorkBatchTests(JobWorkTestCase):
    def setUp(self):
        super().setUp()
        StockEntry.objects.filter(item=self.bar).delete()
        track(self.bar, 'batch')
        track(self.plated, 'batch')
        for number, qty in (('H-1', '30'), ('H-2', '70')):
            self.ok(self.api('put', '/stock/opening', {'item_id': self.bar.pk, 'warehouse_id': self.store.pk, 'quantity': qty,
                                                       'rate': '80', 'batch_number': number}))

    def test_batches_go_out_and_come_back_with_their_numbers(self):
        challan = self.ok(self.send('40'))
        self.assertEqual([(b['number'], Decimal(b['quantity'])) for b in challan['lines'][0]['batches']],
                         [('H-1', Decimal('30')), ('H-2', Decimal('10'))])
        self.assertEqual(per_batch(self.bar, self.store), {'H-2': Decimal('60')})
        # Plated bar keeps the first batch's number.
        self.ok(self.receive(challan, input_quantity='20', output_item_id=self.plated.pk, output_quantity='20'))
        self.assertEqual(per_batch(self.plated), {'H-1': Decimal('20')})
        # The same item back: the same batches.
        self.ok(self.receive(challan, input_quantity='20'))
        self.assertEqual(per_batch(self.bar, self.store), {'H-1': Decimal('10'), 'H-2': Decimal('70')})
        detail = self.ok(self.api('get', f"/batches/{Batch.objects.get(item=self.plated, number='H-1').pk}"))
        self.assertEqual([b['item_code'] for b in detail['made_from']], ['BAR'])


class ReturnBatchTests(NoteTestCase):
    """Kaveri's bolts by batch: the 600 sent went from B-1 (400) and B-2 (200)."""
    supplier_plan = erp.GROWTH

    def accepted(self):
        track(self.s_bolt, 'batch')
        StockEntry.objects.filter(item=self.s_bolt).delete()
        for number, qty, days in (('B-1', 400, 5), ('B-2', 400, 50)):
            batch = Batch.objects.create(company=self.supplier, item=self.s_bolt, number=number, origin='opening',
                                         expires_on=TODAY + timedelta(days=days))
            StockEntry.objects.create(company=self.supplier, item=self.s_bolt, warehouse=self.fs, kind='opening', quantity=qty,
                                      rate=Decimal('5'), entry_date=TODAY, batch=batch)
        return super().accepted()

    def test_a_return_goes_back_into_the_batches_sent(self):
        self.assertEqual(per_batch(self.s_bolt, self.fs), {'B-2': Decimal('200')})
        note = self.post(self.ok(self.ret(250)))
        back = {e.batch.number: e.quantity for e in StockEntry.objects.filter(reference=note['number'])}
        self.assertEqual(sum(back.values()), Decimal('250'))
        self.assertEqual(per_batch(self.s_bolt, self.fs)['B-2'] + per_batch(self.s_bolt, self.fs).get('B-1', 0), Decimal('450'))
        self.ok(self.api('post', f"/notes/{note['id']}/cancel", {'reason': 'Entered by mistake'}))
        self.assertEqual(per_batch(self.s_bolt, self.fs), {'B-2': Decimal('200')})
