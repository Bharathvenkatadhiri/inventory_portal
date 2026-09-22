from django.test import TestCase

from core.models import User


class UserModelTests(TestCase):
    def test_default_role_is_consumer(self):
        user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pass12345"
        )
        self.assertEqual(user.role, "consumer")

    def test_email_is_unique(self):
        User.objects.create_user(username="a", email="dup@example.com", password="pass12345")
        with self.assertRaises(Exception):
            User.objects.create_user(username="b", email="dup@example.com", password="pass12345")
