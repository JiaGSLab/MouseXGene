from dataclasses import dataclass
from datetime import date

import pandas as pd

from core.models import Project
from .models import Cage, Mouse, StrainLine


EXPECTED_COLUMNS = [
    "cage_id",
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


def _to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


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
            errors=[f"Missing required columns: {', '.join(missing_columns)}"],
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
    "coat_color",
    "notes",
    "sire",
    "dam",
]


def _parse_date(value, row_number: int, field_name: str, errors: list[str]) -> date | None:
    text = _to_text(value)
    if not text:
        return None
    try:
        return pd.to_datetime(text).date()
    except Exception:
        errors.append(f"Row {row_number}: invalid {field_name} '{text}'. Use YYYY-MM-DD format.")
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
    missing_columns = [col for col in MOUSE_EXPECTED_COLUMNS if col not in dataframe.columns]
    if missing_columns:
        return MouseImportResult(
            rows=[],
            errors=[f"Missing required columns: {', '.join(missing_columns)}"],
        )

    allowed_sex = {choice[0] for choice in Mouse.Sex.choices}
    allowed_status = {choice[0] for choice in Mouse.Status.choices}

    strain_map = {obj.line_name: obj for obj in StrainLine.objects.all()}
    cage_map = {obj.cage_id: obj for obj in Cage.objects.all()}
    project_map = {obj.name: obj for obj in Project.objects.all()}
    mouse_map = {obj.mouse_uid: obj for obj in Mouse.objects.all()}

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
            errors.append(f"Row {row_number}: sex is required.")
        elif sex not in allowed_sex:
            errors.append(f"Row {row_number}: invalid sex '{sex}'.")

        if not status:
            errors.append(f"Row {row_number}: status is required.")
        elif status not in allowed_status:
            errors.append(f"Row {row_number}: invalid status '{status}'.")

        strain_line_obj = None
        if strain_line_name:
            strain_line_obj = strain_map.get(strain_line_name)
            if not strain_line_obj:
                errors.append(f"Row {row_number}: strain_line '{strain_line_name}' does not exist.")

        current_cage_obj = None
        if current_cage_id:
            current_cage_obj = cage_map.get(current_cage_id)
            if not current_cage_obj:
                errors.append(f"Row {row_number}: current_cage '{current_cage_id}' does not exist.")

        project_obj = None
        if project_name:
            project_obj = project_map.get(project_name)
            if not project_obj:
                errors.append(f"Row {row_number}: project '{project_name}' does not exist.")

        sire_obj = None
        if sire_uid:
            sire_obj = mouse_map.get(sire_uid)
            if not sire_obj:
                errors.append(f"Row {row_number}: sire '{sire_uid}' does not exist in database.")

        dam_obj = None
        if dam_uid:
            dam_obj = mouse_map.get(dam_uid)
            if not dam_obj:
                errors.append(f"Row {row_number}: dam '{dam_uid}' does not exist in database.")

        rows.append(
            {
                "mouse_uid": mouse_uid,
                "sex": sex,
                "birth_date": birth_date,
                "status": status,
                "strain_line": strain_line_obj,
                "current_cage": current_cage_obj,
                "project": project_obj,
                "ear_tag": _to_text(record.get("ear_tag")),
                "coat_color": _to_text(record.get("coat_color")),
                "notes": _to_text(record.get("notes")),
                "sire": sire_obj,
                "dam": dam_obj,
            }
        )

    if seen_mouse_uids:
        existing_uids = set(Mouse.objects.filter(mouse_uid__in=seen_mouse_uids).values_list("mouse_uid", flat=True))
        for mouse_uid in sorted(existing_uids):
            row_number = uid_to_row_number.get(mouse_uid, "?")
            errors.append(f"Row {row_number}: mouse_uid '{mouse_uid}' already exists in database.")

    return MouseImportResult(rows=rows, errors=errors)
