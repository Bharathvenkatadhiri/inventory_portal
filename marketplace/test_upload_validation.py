"""Every file/image field validated by core.validators, exercised through
the actual forms and views (not just the validator unit tests in
core/tests.py) — RFQ files, quote files, production update photos and
documents, and the two accounts-app uploads (cover image, certification).
"""
import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from marketplace.models import Requirement
from marketplace.test_search_reports import Fixtures

MEDIA = tempfile.mkdtemp(prefix="test-upload-media-")


def real_png(name="photo.png"):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color="blue").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def fake_png(name="photo.png"):
    # A right extension on the wrong bytes — exactly what extension-only
    # checking would have let through.
    return SimpleUploadedFile(name, b"MZ\x90\x00not-a-real-image", content_type="image/png")


@override_settings(
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    },
    MEDIA_ROOT=MEDIA,
)
class RfqAndQuoteUploadTests(Fixtures, TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.buyer = self.make_buyer("uploadbuyer")
        self.supplier = self.make_supplier("uploadmaker")

    def rfq_post_data(self, **file_fields):
        data = {
            "title": "Bracket", "rfq_desc": "d", "quote_currency": "INR", "request_reason": "other",
            "nda_required": "False",
            "requirement_parts-TOTAL_FORMS": "1", "requirement_parts-INITIAL_FORMS": "0",
            "requirement_parts-0-part_name": "Bracket", "requirement_parts-0-technology": "Milling",
            "requirement_parts-0-Material": "Aluminium", "requirement_parts-0-quantity": "40",
        }
        data.update(file_fields)
        return data

    def test_rfq_master_file_rejects_a_disallowed_extension(self):
        self.login(self.buyer)
        self.client.post(reverse("new-requirement"), self.rfq_post_data(
            file=SimpleUploadedFile("payload.exe", b"MZ", content_type="application/octet-stream"),
        ))
        self.assertFalse(Requirement.objects.filter(title="Bracket").exists())

    def test_rfq_master_file_accepts_a_whitelisted_extension(self):
        self.login(self.buyer)
        self.client.post(reverse("new-requirement"), self.rfq_post_data(
            file=SimpleUploadedFile("drawing.pdf", b"%PDF-1.4", content_type="application/pdf"),
        ))
        self.assertTrue(Requirement.objects.filter(title="Bracket").exists())

    def test_rfq_part_drawing_rejects_a_disallowed_extension(self):
        self.login(self.buyer)
        data = self.rfq_post_data()
        data["requirement_parts-0-file"] = SimpleUploadedFile("payload.html", b"<script>", content_type="text/html")
        self.client.post(reverse("new-requirement"), data)
        self.assertFalse(Requirement.objects.filter(title="Bracket").exists())

    def test_editing_an_rfq_without_touching_the_file_field_still_saves(self):
        # A regression check for the fix itself: an edit form always
        # resubmits the existing file's name, which must not be treated as
        # a fresh upload and re-validated (or, worse, rejected).
        self.login(self.buyer)
        self.client.post(reverse("new-requirement"), self.rfq_post_data(
            file=SimpleUploadedFile("drawing.pdf", b"%PDF-1.4", content_type="application/pdf"),
        ))
        rfq = Requirement.objects.get(title="Bracket")
        data = self.rfq_post_data()
        data["title"] = "Bracket v2"
        response = self.client.post(reverse("edit-requirement", kwargs={"pk": rfq.pk}), data)
        self.assertEqual(response.status_code, 302)
        rfq.refresh_from_db()
        self.assertEqual(rfq.title, "Bracket v2")
        self.assertTrue(rfq.file)

    def test_quote_file_rejects_a_disallowed_extension_and_accepts_a_whitelisted_one(self):
        rfq = self.make_rfq(self.buyer, "Housing")
        self.login(self.supplier.user)
        url = reverse("new-quote", kwargs={"pk": rfq.pk})
        self.client.post(url, {
            "quote_price": "10", "tooling_cost": "0", "lead_time_unit": "days",
            "quote_file": SimpleUploadedFile("bid.exe", b"MZ", content_type="application/octet-stream"),
        })
        self.assertFalse(rfq.quote.exists())
        self.client.post(url, {
            "quote_price": "10", "tooling_cost": "0", "lead_time_unit": "days",
            "quote_file": SimpleUploadedFile("bid.pdf", b"%PDF-1.4", content_type="application/pdf"),
        })
        self.assertTrue(rfq.quote.exists())


@override_settings(
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    },
    MEDIA_ROOT=MEDIA,
)
class ProductionUpdateUploadTests(Fixtures, TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.buyer = self.make_buyer("produploadbuyer")
        self.supplier = self.make_supplier("produploadmaker")
        self.order = self.make_order(self.make_rfq(self.buyer, "Bracket"), self.supplier, complete=False)
        self.url = reverse("order-post-update", kwargs={"billno": self.order.billno})

    def test_photo_with_a_real_image_extension_but_fake_bytes_is_rejected(self):
        self.login(self.supplier.user)
        self.client.post(self.url, {"body": "Update", "photo": fake_png()})
        self.assertEqual(self.order.updates.count(), 0)

    def test_a_genuine_photo_is_accepted(self):
        self.login(self.supplier.user)
        self.client.post(self.url, {"body": "Update", "photo": real_png()})
        self.assertEqual(self.order.updates.count(), 1)

    def test_document_rejects_a_disallowed_extension(self):
        self.login(self.supplier.user)
        self.client.post(self.url, {
            "body": "Update", "document": SimpleUploadedFile("cert.exe", b"MZ", content_type="application/octet-stream"),
        })
        self.assertEqual(self.order.updates.count(), 0)


@override_settings(
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    },
    MEDIA_ROOT=MEDIA,
)
class CompanyUploadTests(Fixtures, TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.supplier = self.make_supplier("companyuploadmaker")

    def test_cover_image_rejects_fake_bytes_and_accepts_a_real_image(self):
        self.login(self.supplier.user)
        self.client.post(reverse("company-about-update"), {"about": "We machine things.", "cover_image": fake_png()})
        self.supplier.refresh_from_db()
        self.assertFalse(self.supplier.cover_image)
        self.client.post(reverse("company-about-update"), {"about": "We machine things.", "cover_image": real_png()})
        self.supplier.refresh_from_db()
        self.assertTrue(self.supplier.cover_image)

    def test_shop_floor_photo_upload_rejects_fake_bytes_and_accepts_a_real_image(self):
        self.login(self.supplier.user)
        self.client.post(reverse("company-photo-upload"), {"image": fake_png(), "caption": "Floor"})
        self.assertEqual(self.supplier.photos.count(), 0)
        self.client.post(reverse("company-photo-upload"), {"image": real_png(), "caption": "Floor"})
        self.assertEqual(self.supplier.photos.count(), 1)

    def test_certification_document_rejects_a_disallowed_extension(self):
        self.login(self.supplier.user)
        self.client.post(reverse("company-certification-upload"), {
            "name": "ISO 9001:2015",
            "document": SimpleUploadedFile("cert.exe", b"MZ", content_type="application/octet-stream"),
        })
        self.assertEqual(self.supplier.certifications.count(), 0)
        self.client.post(reverse("company-certification-upload"), {
            "name": "ISO 9001:2015",
            "document": SimpleUploadedFile("cert.pdf", b"%PDF-1.4", content_type="application/pdf"),
        })
        self.assertEqual(self.supplier.certifications.count(), 1)
