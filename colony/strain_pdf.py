"""Validation helpers for strain line PDF attachments."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils.text import get_valid_filename

MAX_STRAIN_LINE_PDF_COUNT = 10
MAX_STRAIN_LINE_PDF_BYTES = 10 * 1024 * 1024  # 10 MiB

PDF_DESCRIPTION_LABELS = {
    "strain_line_info": "Strain line info",
    "genotype_info": "Genotype info",
    "husbandry": "Husbandry",
    "genetics": "Genetics",
    "colony_notes": "Colony notes",
    "protocol": "Protocol",
    "other": "Other",
}


def resolve_pdf_description(*, kind: str, custom: str = "") -> str:
    kind = (kind or "").strip()
    if kind in PDF_DESCRIPTION_LABELS:
        return PDF_DESCRIPTION_LABELS[kind]
    label = (custom or "").strip()
    if not label:
        raise ValidationError("Enter a custom PDF description.")
    return label


def storage_filename_for_description(description: str, *, fallback: str = "") -> str:
    base = get_valid_filename((description or "").strip()) or get_valid_filename(fallback) or "document"
    if not base.lower().endswith(".pdf"):
        base = f"{base}.pdf"
    return base


def unique_pdf_description(strain_line_id: int, description: str) -> str:
    from colony.models import StrainLineDocument

    label = (description or "").strip()
    if not StrainLineDocument.objects.filter(strain_line_id=strain_line_id, description=label).exists():
        return label
    n = 2
    while StrainLineDocument.objects.filter(strain_line_id=strain_line_id, description=f"{label} ({n})").exists():
        n += 1
    return f"{label} ({n})"


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
