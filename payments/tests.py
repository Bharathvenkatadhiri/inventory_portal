from django.test import TestCase

from core.models import User
from accounts.models import ConsumerProfile, ManufacturerProfile
from marketplace.models import Requirement, Quote, Order
from payments.models import Payment


class PaymentModelTests(TestCase):
    def test_payment_creation(self):
        consumer_user = User.objects.create_user(username="buyer2", email="buyer2@example.com", password="pass12345")
        manufacturer_user = User.objects.create_user(
            username="maker2", email="maker2@example.com", password="pass12345", role="manufacturer"
        )
        consumer = ConsumerProfile.objects.create(
            user=consumer_user, Name="Buyer2", type_of_business="electronics",
            city="Bengaluru", state="KA", country="India", phone="9999999992",
            email="buyer2@example.com", EORI_number="EORI3", VAT_number="VAT3",
        )
        manufacturer = ManufacturerProfile.objects.create(
            user=manufacturer_user, phone="8888888882", address="Unit 1",
            city="Pune", state="MH", country="India", amount_of_employees="10-20",
            turnover_per_year="<1", certificates="", email="maker2@example.com",
        )
        requirement = Requirement.objects.create(
            user=consumer_user, title="Panel batch", rfq_desc="20 panels",
            quote_currency="USD", request_reason="new_product",
        )
        quote = Quote.objects.create(requirement=requirement, supplier=manufacturer, quote_price="1200.00")
        order = Order.objects.create(requirement=requirement, quote=quote, supplier=manufacturer, customer=consumer)

        payment = Payment.objects.create(order=order, amount="1200.00")
        self.assertEqual(payment.status, "pending")
        self.assertEqual(payment.currency, "INR")
