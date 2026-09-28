"""Shared upload validation for every file and image field in the app.

Before this, only message attachments were checked (see the old
MessageForm.clean_attachment, now built on top of validate_upload below).
RFQ master files, part drawings, quote files, production update photos and
documents, certifications, and manufacturer cover/shop-floor photos all
accepted anything of any size — including an executable or an HTML file
renamed with a document's extension — which other companies then opened.

Two whitelists cover the app's two kinds of upload: real documents/CAD
files (`DOCUMENT_EXTENSIONS`) and photos (`IMAGE_EXTENSIONS`). An
extension is just a client-supplied string, so image uploads are also
opened and decoded with Pillow (`verify_image=True`) — a renamed non-image
file fails that check even though its extension looks fine. Documents
aren't decoded the same way: there's no equivalent "is this really a PDF"
check that's worth the false-positive risk here, so those rely on the
extension and size limit only, same as message attachments always have.
"""
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile

DOCUMENT_EXTENSIONS = {
    '.pdf', '.png', '.jpg', '.jpeg', '.step', '.stp', '.iges', '.igs', '.stl', '.dxf', '.dwg',
    '.xlsx', '.xls', '.csv', '.docx', '.doc', '.txt', '.zip',
}
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}

# Message attachments have used this same 10 MB default for a while
# (MESSAGE_ATTACHMENT_MAX_BYTES); every other upload gets the same cap
# unless a caller asks for a different one.
DEFAULT_MAX_BYTES = 10 * 1024 * 1024


def validate_upload(upload, extensions=DOCUMENT_EXTENSIONS, max_bytes=None, verify_image=False):
    """Raises django.core.exceptions.ValidationError unless `upload` (a
    Django UploadedFile) has a whitelisted extension, is within the size
    cap, and — when `verify_image` is set — actually decodes as an image.

    Call this from a form's clean_<field>, or directly in a view that
    handles request.FILES without a form. `upload` is left positioned at
    the start of the file either way, so the caller's own save/read
    afterwards sees the whole thing.
    """
    if max_bytes is None:
        max_bytes = getattr(settings, 'MESSAGE_ATTACHMENT_MAX_BYTES', DEFAULT_MAX_BYTES)

    ext = os.path.splitext(upload.name)[1].lower()
    if ext not in extensions:
        raise ValidationError("That file type isn't supported. Allowed: " + ", ".join(sorted(extensions)))

    if upload.size > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise ValidationError(f"Files can be up to {limit_mb} MB.")

    if verify_image:
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(upload) as image:
                image.verify()
        except (UnidentifiedImageError, OSError, ValueError):
            raise ValidationError("That doesn't look like a valid image file.")
        finally:
            upload.seek(0)  # Image.open()/verify() read from and consume the file object


def is_new_upload(value):
    """True for a just-posted file, false for the existing FieldFile a
    ModelForm's cleaned_data holds when a file field wasn't touched (an
    untouched ClearableFileInput on an edit form) — validating only
    genuine new uploads avoids re-checking, and for image fields
    re-downloading from storage to run through Pillow, a file that's
    already stored and was already validated when it was first uploaded."""
    return isinstance(value, UploadedFile)
