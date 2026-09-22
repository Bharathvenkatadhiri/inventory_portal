from django.test import TestCase

from core.models import User
from accounts.models import ConsumerProfile, ManufacturerProfile


class ProfileModelTests(TestCase):
    def test_consumer_profile_creation(self):
        user = User.objects.create_user(username="c1", email="c1@example.com", password="pass12345")
        profile = ConsumerProfile.objects.create(
            user=user,
            Name="Acme Buyer",
            type_of_business="electronics",
            city="Bengaluru",
            state="KA",
            country="India",
            phone="9999999999",
            email="c1@example.com",
            EORI_number="EORI1",
            VAT_number="VAT1",
        )
        self.assertEqual(str(profile), f"#{profile.id} - Acme Buyer")

    def test_manufacturer_profile_creation(self):
        user = User.objects.create_user(
            username="m1", email="m1@example.com", password="pass12345", role="manufacturer"
        )
        profile = ManufacturerProfile.objects.create(
            user=user,
            phone="8888888888",
            address="123 Industrial Rd",
            city="Pune",
            state="MH",
            country="India",
            amount_of_employees="10-20",
            turnover_per_year="<1",
            certificates="",
            email="m1@example.com",
        )
        self.assertIn("m1", str(profile.user))
