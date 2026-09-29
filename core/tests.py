import io
import os
import subprocess
import sys

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.fields.files import FieldFile
from django.test import TestCase

from core.models import User
from core.validators import DOCUMENT_EXTENSIONS, IMAGE_EXTENSIONS, is_new_upload, validate_upload


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


def _real_png(name="photo.png"):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color="red").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class ValidateUploadTests(TestCase):
    def test_rejects_a_disallowed_extension(self):
        upload = SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream")
        with self.assertRaises(ValidationError):
            validate_upload(upload, DOCUMENT_EXTENSIONS)

    def test_accepts_a_whitelisted_document_extension(self):
        upload = SimpleUploadedFile("drawing.pdf", b"%PDF-1.4", content_type="application/pdf")
        validate_upload(upload, DOCUMENT_EXTENSIONS)  # doesn't raise

    def test_rejects_a_file_over_the_size_cap(self):
        upload = SimpleUploadedFile("drawing.pdf", b"x" * 100, content_type="application/pdf")
        with self.assertRaises(ValidationError):
            validate_upload(upload, DOCUMENT_EXTENSIONS, max_bytes=50)

    def test_extension_alone_does_not_pass_image_verification(self):
        # An executable renamed to look like a photo: the whitelist alone
        # would let it through, which is exactly what image verification
        # is for.
        upload = SimpleUploadedFile("cover.png", b"MZ\x90\x00not-a-real-image", content_type="image/png")
        with self.assertRaises(ValidationError):
            validate_upload(upload, IMAGE_EXTENSIONS, verify_image=True)

    def test_a_genuine_image_passes_verification_and_is_left_readable(self):
        upload = _real_png()
        validate_upload(upload, IMAGE_EXTENSIONS, verify_image=True)
        # Image.verify() consumes the file object; callers (a ModelForm's
        # save(), a view that goes on to create the model) need to read it
        # again afterwards.
        self.assertTrue(upload.read())

    def test_is_new_upload_distinguishes_a_posted_file_from_a_stored_fieldfile(self):
        from marketplace.models import Requirement
        field = Requirement._meta.get_field("file")
        existing = FieldFile(None, field, "existing/path.pdf")  # what an untouched edit form holds
        self.assertTrue(is_new_upload(SimpleUploadedFile("a.pdf", b"x")))
        self.assertFalse(is_new_upload(existing))
        self.assertFalse(is_new_upload(None))
        self.assertFalse(is_new_upload(False))


class ProductionStorageGuardTests(TestCase):
    """Without a bucket configured, uploads fall back to local disk served
    unsigned from /media/ — fine for local dev, but with DEBUG=False that
    means RFQ files and guessable purchase-order/invoice filenames become
    fetchable by anyone with the path. core/settings.py now refuses to
    start rather than fall back to that silently.

    The check runs once at settings-module import time, before this test
    process's own DEBUG=True even applies, so it can't be exercised with
    override_settings (which only patches an already-loaded settings
    object) — each case boots a fresh interpreter instead."""

    def _boot(self, **env_overrides):
        env = os.environ.copy()
        env.update({k: str(v) for k, v in env_overrides.items()})
        return subprocess.run(
            [sys.executable, "-c", "import django; django.setup()"],
            env=env, capture_output=True, text=True, timeout=30,
        )

    def test_refuses_to_start_without_a_bucket_in_production(self):
        result = self._boot(DEBUG="False", AWS_STORAGE_BUCKET_NAME="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AWS_STORAGE_BUCKET_NAME is required", result.stderr)

    def test_starts_fine_in_production_with_a_bucket_configured(self):
        result = self._boot(DEBUG="False", AWS_STORAGE_BUCKET_NAME="prod-bucket")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dev_mode_is_unaffected_without_a_bucket(self):
        result = self._boot(DEBUG="True", AWS_STORAGE_BUCKET_NAME="")
        self.assertEqual(result.returncode, 0, result.stderr)
