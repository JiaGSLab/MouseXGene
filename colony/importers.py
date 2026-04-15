from dataclasses import dataclass
from datetime import date
import re

import pandas as pd

from .models import Cage, Mouse


EXPECTED_COLUMNS = [
    "cage_id",
    "created_date",
    "room",
    "rack",
    "position",
    "cage_type",
    "purpose",
    "status",
    "notes",
]


@dataclass
class CageImportResult:
    rows: list[dict]
    errors: list[str]


@dataclass
class MouseImportResult:
    rows: list[dict]
    errors: list[str]


@dataclass(frozen=True)
class MouseImportOptions:
    auto_create_missing_strain_lines: bool = True
    auto_create_missing_projects: bool = True
    auto_create_missing_cages: bool = True
    resolve_pedigree_within_file: bool = True


def _to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _missing_columns_error(missing_columns: list[str], actual_columns: list[str]) -> str:
    expected_text = ", ".join(EXPECTED_COLUMNS)
    actual_text = ", ".join(actual_columns) if actual_columns else "(none)"
    missing_text = ", ".join(missing_columns)
    return (
        f"Missing required columns: {missing_text}. "
        f"Found columns: {actual_text}. "
        f"Expected schema includes: {expected_text}."
    )


def parse_cage_import(uploaded_file) -> CageImportResult:
    filename = (uploaded_file.name or "").lower()
    if filename.endswith(".csv"):
        dataframe = pd.read_csv(uploaded_file, dtype=str, keep_default_na=False)
    elif filename.endswith(".xlsx"):
        dataframe = pd.read_excel(uploaded_file, dtype=str, engine="openpyxl")
    else:
        return CageImportResult(rows=[], errors=["Unsupported file type. Please upload a .csv or .xlsx file."])

    normalized_columns = [str(col).strip() for col in dataframe.columns]
    dataframe.columns = normalized_columns
    missing_columns = [col for col in EXPECTED_COLUMNS if col not in dataframe.columns]
    if missing_columns:
        return CageImportResult(
            rows=[],
            errors=[_missing_columns_error(missing_columns, list(dataframe.columns))],
        )

    allowed_cage_types = {choice[0] for choice in Cage.CageType.choices}
    allowed_purposes = {choice[0] for choice in Cage.Purpose.choices}
    allowed_status = {choice[0] for choice in Cage.Status.choices}

    rows: list[dict] = []
    errors: list[str] = []
    seen_cage_ids: set[str] = set()
    cage_id_to_row_number: dict[str, int] = {}

    for index, record in dataframe[EXPECTED_COLUMNS].iterrows():
        row_number = index + 2
        cage_id = _to_text(record.get("cage_id"))
        room = _to_text(record.get("room"))
        rack = _to_text(record.get("rack"))
        position = _to_text(record.get("position"))
        cage_type = _to_text(record.get("cage_type")) or Cage.CageType.STANDARD
        purpose = _to_text(record.get("purpose")) or Cage.Purpose.HOLDING
        status = _to_text(record.get("status")) or Cage.Status.ACTIVE
        notes = _to_text(record.get("notes"))
        created_date = _parse_date(record.get("created_date"), row_number, "created_date", errors)

        if not cage_id:
            errors.append(f"Row {row_number}: cage_id is required.")
            continue
        if cage_id in seen_cage_ids:
            errors.append(f"Row {row_number}: duplicate cage_id '{cage_id}' in uploaded file.")
            continue
        seen_cage_ids.add(cage_id)
        cage_id_to_row_number[cage_id] = row_number

        if cage_type not in allowed_cage_types:
            errors.append(f"Row {row_number}: invalid cage_type '{cage_type}'.")
        if purpose not in allowed_purposes:
            errors.append(f"Row {row_number}: invalid purpose '{purpose}'.")
        if status not in allowed_status:
            errors.append(f"Row {row_number}: invalid status '{status}'.")

        rows.append(
            {
                "cage_id": cage_id,
                "room": room,
                "created_date": created_date,
                "rack": rack,
                "position": position,
                "cage_type": cage_type,
                "purpose": purpose,
                "status": status,
                "notes": notes,
            }
        )

    if seen_cage_ids:
        existing_cage_ids = set(
            Cage.objects.filter(cage_id__in=seen_cage_ids).values_list("cage_id", flat=True)
        )
        for cage_id in sorted(existing_cage_ids):
            row_number = cage_id_to_row_number.get(cage_id, "?")
            errors.append(f"Row {row_number}: cage_id '{cage_id}' already exists in database.")

    return CageImportResult(rows=rows, errors=errors)


MOUSE_EXPECTED_COLUMNS = [
    "mouse_uid",
    "sex",
    "birth_date",
    "status",
    "strain_line",
    "current_cage",
    "project",
    "ear_tag",
    "toe_tag",
    "origin",
    "coat_color",
    "notes",
    "sire",
    "dam",
]

GENOTYPE_SLOT_COUNT = 4
GENOTYPE_SLOT_FIELDS = (
    "locus",
    "allele_1",
    "allele_2",
    "zygosity",
    "is_confirmed",
    "assay_date",
    "notes",
)


def _genotype_slot_columns(slot: int) -> list[str]:
    return [f"genotype_{slot}_{field}" for field in GENOTYPE_SLOT_FIELDS]


def _detect_genotype_slots(columns: list[str]) -> list[int]:
    slots = set(range(1, GENOTYPE_SLOT_COUNT + 1))
    pattern = re.compile(r"^genotype_(\d+)_")
    for col in columns:
        match = pattern.match(col)
        if match:
            slots.add(int(match.group(1)))
    return sorted(slots)


GENOTYPE_EXPECTED_COLUMNS = [
    col
    for slot in range(1, GENOTYPE_SLOT_COUNT + 1)
    for col in _genotype_slot_columns(slot)
]

MOUSE_EXPECTED_COLUMNS = MOUSE_EXPECTED_COLUMNS + GENOTYPE_EXPECTED_COLUMNS

MOUSE_REQUIRED_COLUMNS = ["mouse_uid", "strain_line"]
MOUSE_OPTIONAL_COLUMNS = [col for col in MOUSE_EXPECTED_COLUMNS if col not in MOUSE_REQUIRED_COLUMNS]


def _parse_date(value, row_number: int, field_name: str, errors: list[str]) -> date | None:
    text = _to_text(value)
    if not text:
        return None
    try:
        return pd.to_datetime(text).date()
    except Exception:
        errors.append(f"Row {row_number}: invalid {field_name} '{text}'. Use YYYY-MM-DD format.")
        return None


def _parse_bool(value, row_number: int, field_name: str, errors: list[str]) -> bool | None:
    text = _to_text(value).lower()
    if text == "":
        return None
    truthy = {"true", "yes", "1", "y"}
    falsy = {"false", "no", "0", "n"}
    if text in truthy:
        return True
    if text in falsy:
        return False
    errors.append(f"Row {row_number}: invalid {field_name} '{value}'. Use true/false, yes/no, 1/0, y/n.")
    return None


def parse_mouse_import(uploaded_file) -> MouseImportResult:
    filename = (uploaded_file.name or "").lower()
    if filename.endswith(".csv"):
        dataframe = pd.read_csv(uploaded_file, dtype=str, keep_default_na=False)
    elif filename.endswith(".xlsx"):
        dataframe = pd.read_excel(uploaded_file, dtype=str, engine="openpyxl")
    else:
        return MouseImportResult(rows=[], errors=["Unsupported file type. Please upload a .csv or .xlsx file."])

    dataframe.columns = [str(col).strip() for col in dataframe.columns]
    missing_columns = [col for col in MOUSE_REQUIRED_COLUMNS if col not in dataframe.columns]
    if missing_columns:
        return MouseImportResult(
            rows=[],
            errors=[
                (
                    f"Missing required columns: {', '.join(missing_columns)}. "
                    f"Found columns: {', '.join(dataframe.columns) if len(dataframe.columns) else '(none)'}. "
                    f"Expected columns: {', '.join(MOUSE_EXPECTED_COLUMNS)}."
                )
            ],
        )

    for optional_col in MOUSE_OPTIONAL_COLUMNS:
        if optional_col not in dataframe.columns:
            dataframe[optional_col] = ""
    genotype_slots = _detect_genotype_slots(list(dataframe.columns))

    allowed_sex = {choice[0] for choice in Mouse.Sex.choices}
    allowed_status = {choice[0] for choice in Mouse.Status.choices}

    rows: list[dict] = []
    errors: list[str] = []
    seen_mouse_uids: set[str] = set()
    uid_to_row_number: dict[str, int] = {}

    for index, record in dataframe[MOUSE_EXPECTED_COLUMNS].iterrows():
        row_number = index + 2
        mouse_uid = _to_text(record.get("mouse_uid"))
        sex = _to_text(record.get("sex"))
        status = _to_text(record.get("status"))
        birth_date = _parse_date(record.get("birth_date"), row_number, "birth_date", errors)
        strain_line_name = _to_text(record.get("strain_line"))
        current_cage_id = _to_text(record.get("current_cage"))
        project_name = _to_text(record.get("project"))
        sire_uid = _to_text(record.get("sire"))
        dam_uid = _to_text(record.get("dam"))

        if not mouse_uid:
            errors.append(f"Row {row_number}: mouse_uid is required.")
            continue
        if mouse_uid in seen_mouse_uids:
            errors.append(f"Row {row_number}: duplicate mouse_uid '{mouse_uid}' in uploaded file.")
            continue
        seen_mouse_uids.add(mouse_uid)
        uid_to_row_number[mouse_uid] = row_number

        if not sex:
            sex = Mouse.Sex.UNKNOWN
        elif sex not in allowed_sex:
            errors.append(f"Row {row_number}: invalid sex '{sex}'.")

        if not status:
            status = Mouse.Status.ACTIVE
        elif status not in allowed_status:
            errors.append(f"Row {row_number}: invalid status '{status}'.")

        if not strain_line_name:
            errors.append(f"Row {row_number}: strain_line is required.")

        genotype_slots: list[dict] = []
        for slot in genotype_slots:
            prefix = f"genotype_{slot}_"
            locus = _to_text(record.get(f"{prefix}locus"))
            allele_1 = _to_text(record.get(f"{prefix}allele_1"))
            allele_2 = _to_text(record.get(f"{prefix}allele_2"))
            zygosity = _to_text(record.get(f"{prefix}zygosity"))
            is_confirmed = _parse_bool(
                record.get(f"{prefix}is_confirmed"),
                row_number,
                f"{prefix}is_confirmed",
                errors,
            )
            assay_date = _parse_date(
                record.get(f"{prefix}assay_date"),
                row_number,
                f"{prefix}assay_date",
                errors,
            )
            genotype_notes = _to_text(record.get(f"{prefix}notes"))
            if not any([locus, allele_1, allele_2, zygosity, genotype_notes, is_confirmed is not None, assay_date]):
                continue
            if not locus:
                errors.append(f"Row {row_number}: {prefix}locus is required when genotype slot has data.")
                continue
            genotype_slots.append(
                {
                    "slot": slot,
                    "locus_name": locus,
                    "allele_1": allele_1,
                    "allele_2": allele_2,
                    "zygosity_display": zygosity,
                    "is_confirmed": bool(is_confirmed) if is_confirmed is not None else False,
                    "assay_date": assay_date,
                    "notes": genotype_notes,
                }
            )

        rows.append(
            {
                "row_number": row_number,
                "mouse_uid": mouse_uid,
                "sex": sex,
                "birth_date": birth_date,
                "status": status,
                "strain_line_name": strain_line_name,
                "current_cage_id": current_cage_id,
                "project_name": project_name,
                "ear_tag": _to_text(record.get("ear_tag")),
                "toe_tag": _to_text(record.get("toe_tag")),
                "origin": _to_text(record.get("origin")),
                "coat_color": _to_text(record.get("coat_color")),
                "notes": _to_text(record.get("notes")),
                "sire_uid": sire_uid,
                "dam_uid": dam_uid,
                "genotype_slots": genotype_slots,
            }
        )

    if seen_mouse_uids:
        existing_uids = set(Mouse.objects.filter(mouse_uid__in=seen_mouse_uids).values_list("mouse_uid", flat=True))
        for mouse_uid in sorted(existing_uids):
            row_number = uid_to_row_number.get(mouse_uid, "?")
            errors.append(f"Row {row_number}: mouse_uid '{mouse_uid}' already exists in database.")

    return MouseImportResult(rows=rows, errors=errors)
