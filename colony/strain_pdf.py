"""Validation helpers for strain line PDF attachments."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile

MAX_STRAIN_LINE_PDF_COUNT = 10
MAX_STRAIN_LINE_PDF_BYTES = 10 * 1024 * 1024  # 10 MiB


def validate_strain_line_pdf_file(uploaded: UploadedFile) -> None:
    name = (uploaded.name or "").strip()
    if not name.lower().endswith(".pdf"):
        raise ValidationError(f"{name or 'File'} must be a PDF (.pdf).")
    size = uploaded.size or 0
    if size <= 0:
        raise ValidationError(f"{name or 'File'} is empty.")
    if size > MAX_STRAIN_LINE_PDF_BYTES:
        raise ValidationError(
            f"{name} is too large ({size // (1024 * 1024)} MB). Maximum is 10 MB per file."
        )
    content_type = (getattr(uploaded, "content_type", "") or "").lower()
    if content_type and content_type not in ("application/pdf", "application/x-pdf"):
        raise ValidationError(f"{name} must have PDF content type (got {content_type}).")
