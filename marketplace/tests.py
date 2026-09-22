from django.test import TestCase

from core.models import User
from accounts.models import ConsumerProfile, ManufacturerProfile
from marketplace.models import Requirement, Quote, Order


class MarketplaceFlowTests(TestCase):
    def setUp(self):
        self.consumer_user = User.objects.create_user(
            username="buyer", email="buyer@example.com", password="pass12345"
        )
        self.manufacturer_user = User.objects.create_user(
            username="maker", email="maker@example.com", password="pass12345", role="manufacturer"
        )
        self.consumer = ConsumerProfile.objects.create(
            user=self.consumer_user,
            Name="Buyer Co",
            type_of_business="electronics",
            city="Bengaluru",
            state="KA",
            country="India",
            phone="9999999991",
            email="buyer@example.com",
            EORI_number="EORI2",
            VAT_number="VAT2",
        )
        self.manufacturer = ManufacturerProfile.objects.create(
            user=self.manufacturer_user,
            phone="8888888881",
            address="123 Industrial Rd",
            city="Pune",
            state="MH",
            country="India",
            amount_of_employees="10-20",
            turnover_per_year="<1",
            certificates="",
            email="maker@example.com",
        )

    def test_requirement_quote_order_lifecycle(self):
        requirement = Requirement.objects.create(
            user=self.consumer_user,
            title="Bracket batch",
            rfq_desc="100 aluminium brackets",
            quote_currency="USD",
            request_reason="new_product",
        )
        quote = Quote.objects.create(
            requirement=requirement,
            supplier=self.manufacturer,
            quote_price="500.00",
        )
        order = Order.objects.create(
            requirement=requirement,
            quote=quote,
            supplier=self.manufacturer,
            customer=self.consumer,
        )
        self.assertEqual(order.status, "submitted")

        order.mark_quoted()
        order.select_quote()
        order.save()

        self.assertEqual(order.status, "quote_selected")
        self.assertEqual(order.status_history.count(), 2)
