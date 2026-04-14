from dataclasses import dataclass
from datetime import date

import pandas as pd

from colony.models import Mouse


GENOTYPE_EXPECTED_COLUMNS = [
    "mouse_uid",
    "locus_name",
    "allele_1",
    "allele_2",
    "zygosity_display",
    "is_confirmed",
    "assay_date",
    "notes",
]


@dataclass
class GenotypeImportResult:
    rows: list[dict]
    errors: list[str]


def _to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _parse_date(value, row_number: int, errors: list[str]) -> date | None:
    text = _to_text(value)
    if not text:
        return None
    try:
        return pd.to_datetime(text).date()
    except Exception:
        errors.append(f"Row {row_number}: invalid assay_date '{text}'. Use YYYY-MM-DD format.")
        return None


def _parse_bool(value, row_number: int, errors: list[str]) -> bool:
    text = _to_text(value).lower()
    if text == "":
        return False
    truthy = {"true", "yes", "1", "y"}
    falsy = {"false", "no", "0", "n"}
    if text in truthy:
        return True
    if text in falsy:
        return False
    errors.append(
        f"Row {row_number}: invalid is_confirmed '{value}'. Use true/false, yes/no, 1/0, or y/n."
    )
    return False


def parse_genotype_import(uploaded_file) -> GenotypeImportResult:
    filename = (uploaded_file.name or "").lower()
    if filename.endswith(".csv"):
        dataframe = pd.read_csv(uploaded_file, dtype=str, keep_default_na=False)
    elif filename.endswith(".xlsx"):
        dataframe = pd.read_excel(uploaded_file, dtype=str, engine="openpyxl")
    else:
        return GenotypeImportResult(
            rows=[],
            errors=["Unsupported file type. Please upload a .csv or .xlsx file."],
        )

    dataframe.columns = [str(col).strip() for col in dataframe.columns]
    missing_columns = [col for col in GENOTYPE_EXPECTED_COLUMNS if col not in dataframe.columns]
    if missing_columns:
        return GenotypeImportResult(
            rows=[],
            errors=[f"Missing required columns: {', '.join(missing_columns)}"],
        )

    mouse_map = {obj.mouse_uid: obj for obj in Mouse.objects.all()}
    rows: list[dict] = []
    errors: list[str] = []

    for index, record in dataframe[GENOTYPE_EXPECTED_COLUMNS].iterrows():
        row_number = index + 2
        mouse_uid = _to_text(record.get("mouse_uid"))
        locus_name = _to_text(record.get("locus_name"))
        allele_1 = _to_text(record.get("allele_1"))
        allele_2 = _to_text(record.get("allele_2"))
        zygosity_display = _to_text(record.get("zygosity_display"))
        assay_date = _parse_date(record.get("assay_date"), row_number, errors)
        is_confirmed = _parse_bool(record.get("is_confirmed"), row_number, errors)
        notes = _to_text(record.get("notes"))

        if not mouse_uid:
            errors.append(f"Row {row_number}: mouse_uid is required.")
            continue
        mouse_obj = mouse_map.get(mouse_uid)
        if not mouse_obj:
            errors.append(f"Row {row_number}: mouse_uid '{mouse_uid}' does not exist.")
            continue

        if not locus_name:
            errors.append(f"Row {row_number}: locus_name is required.")
            continue

        rows.append(
            {
                "mouse": mouse_obj,
                "locus_name": locus_name,
                "allele_1": allele_1,
                "allele_2": allele_2,
                "zygosity_display": zygosity_display,
                "is_confirmed": is_confirmed,
                "assay_date": assay_date,
                "notes": notes,
            }
        )

    return GenotypeImportResult(rows=rows, errors=errors)
