import re

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, RegexValidator

phone_validator = RegexValidator(r"^[6-9]\d{9}$", "Enter a 10-digit Indian mobile number.")

# Allowed uploads: photos, X-rays (RVG software often exports DICOM) and PDFs.
upload_extensions = FileExtensionValidator(["jpg", "jpeg", "png", "webp", "dcm", "pdf"])

MAX_UPLOAD_MB = 15


def validate_upload_size(file):
    if file.size > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"File is larger than {MAX_UPLOAD_MB} MB.")


UPLOAD_VALIDATORS = [upload_extensions, validate_upload_size]


def normalize_phone(value):
    """'+91 98765-43210' -> '9876543210'."""
    digits = re.sub(r"\D", "", str(value))
    return digits[-10:] if len(digits) > 10 else digits
