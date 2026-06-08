from dataclasses import dataclass
from datetime import date
import re

import pandas as pd

from users.import_prefix import apply_import_prefix_to_id

from .id_uniqueness import find_conflicting_cage, find_conflicting_mouse
from .models import Cage, Mouse, StrainLine


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


def _resolve_prefixed_cage_ref(raw: str, prefix: str, existing_cages: set[str]) -> str:
    text = _to_text(raw)
    if not text:
        return ""
    if text in existing_cages:
        return text
    return apply_import_prefix_to_id(text, prefix)


def _resolve_prefixed_pedigree_uid(raw: str, prefix: str, existing_mice: set[str]) -> str:
    text = _to_text(raw)
    if not text:
        return ""
    if text in existing_mice:
        return text
    return apply_import_prefix_to_id(text, prefix)


def _missing_columns_error(missing_columns: list[str], actual_columns: list[str]) -> str:
    expected_text = ", ".join(EXPECTED_COLUMNS)
    actual_text = ", ".join(actual_columns) if actual_columns else "(none)"
    missing_text = ", ".join(missing_columns)
    return (
        f"Missing required columns: {missing_text}. "
        f"Found columns: {actual_text}. "
        f"Expected schema includes: {expected_text}."
    )


def parse_cage_import(
    uploaded_file,
    *,
    id_prefix: str | None = None,
    update_existing: bool = True,
) -> CageImportResult:
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
        if id_prefix:
            cage_id = apply_import_prefix_to_id(cage_id, id_prefix)
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
        cage_key = cage_id.casefold()
        if cage_key in seen_cage_ids:
            errors.append(f"Row {row_number}: duplicate cage_id '{cage_id}' in uploaded file.")
            continue
        seen_cage_ids.add(cage_key)
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

    for row in rows:
        conflict = find_conflicting_cage(row["cage_id"])
        row["_update"] = conflict is not None
        if not update_existing and conflict is not None:
            row_number = cage_id_to_row_number.get(row["cage_id"], "?")
            errors.append(
                f"Row {row_number}: cage_id '{row['cage_id']}' is already used by cage #{conflict.pk} "
                f"({conflict.get_status_display()}). IDs cannot be reused, including inactive cages."
            )

    return CageImportResult(rows=rows, errors=errors)


MOUSE_BASE_COLUMNS = [
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
    "breeding_cage",
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
    slots: set[int] = set()
    pattern = re.compile(r"^genotype_(\d+)_")
    for col in columns:
        match = pattern.match(col)
        if match:
            slots.add(int(match.group(1)))
    return sorted(slots)


def _import_locus_name(raw_name: str) -> str:
    return _to_text(raw_name).strip()


def _build_import_strain_loci_lookup() -> dict[str, set[str]]:
    """Map strain line names/aliases to that line's template locus names."""
    lookup: dict[str, set[str]] = {}
    for line in StrainLine.objects.all():
        loci = set(line.expected_loci_list())
        for key in (line.line_name, line.key_name, line.display_name, line.name, line.short_name):
            text = _to_text(key)
            if text:
                lookup[text] = loci
    return lookup


def _empty_import_genotype_component(locus_name: str, *, slot: int = 0, chromosome_type: str = "unknown") -> dict:
    return {
        "slot": slot,
        "locus_name": locus_name,
        "allele_1": "",
        "allele_2": "",
        "zygosity_display": "",
        "zygosity_class": "unknown",
        "chromosome_type": chromosome_type,
        "is_confirmed": False,
        "assay_date": None,
        "notes": "",
    }


def _append_import_genotype_component(
    component_by_locus: dict[str, dict],
    genotype_components: list[dict],
    comp: dict,
    *,
    row_number: int,
    errors: list[str],
) -> bool:
    locus = (comp.get("locus_name") or "").strip()
    if locus in component_by_locus:
        errors.append(f"Row {row_number}: duplicate genotype locus '{locus}' in import row.")
        return False
    component_by_locus[locus] = comp
    genotype_components.append(comp)
    return True


GENOTYPE_EXPECTED_COLUMNS = [
    col
    for slot in range(1, GENOTYPE_SLOT_COUNT + 1)
    for col in _genotype_slot_columns(slot)
]

MOUSE_EXPECTED_COLUMNS = MOUSE_BASE_COLUMNS + GENOTYPE_EXPECTED_COLUMNS
MOUSE_IMPORT_TEMPLATE_COLUMNS = MOUSE_BASE_COLUMNS + [
    "Lyz2-Cre",
    "Tet2",
    "Gpr82",
    "Foxp3-Cre",
    "Rosa26-LSL-tdTomato",
    "CA/TA/RA-KI",
]

MOUSE_REQUIRED_COLUMNS = ["mouse_uid", "strain_line"]
MOUSE_OPTIONAL_COLUMNS = [col for col in MOUSE_BASE_COLUMNS if col not in MOUSE_REQUIRED_COLUMNS]


def _infer_chromosome_type(allele_1: str, allele_2: str) -> str:
    if (allele_2 or "").upper() == "Y":
        return "x_linked"
    return "unknown"


def _infer_zygosity_class(allele_1: str, allele_2: str) -> str:
    a1 = (allele_1 or "").strip()
    a2 = (allele_2 or "").strip()
    if not (a1 and a2):
        return "unknown"
    if a2.upper() == "Y":
        return "hemizygous"
    if a1 == a2:
        if a1 in {"+", "wt", "WT"}:
            return "wt"
        return "hom"
    return "het"


def _parse_genotype_display(text: str, row_number: int, locus_name: str, errors: list[str]) -> tuple[str, str, str] | None:
    raw = _to_text(text)
    if not raw:
        return None
    normalized = raw.replace(" ", "")
    alias_map = {
        "wt": ("+", "+"),
        "het": ("+", "-"),
        "hom": ("-", "-"),
        "+": ("+", "+"),
        "-": ("-", "-"),
    }
    alias = normalized.casefold()
    if alias in {"pos", "positive", "tg+", "tgpos"}:
        return "", "", "pos"
    if alias in {"neg", "negative", "tg-", "tgneg"}:
        return "", "", "neg"
    if alias in alias_map:
        allele_1, allele_2 = alias_map[alias]
        return allele_1, allele_2, f"{allele_1}/{allele_2}"
    if "/" not in normalized:
        # Custom free-text genotype (matches mouse form "Custom" entries).
        return "", "", raw
    allele_1, allele_2 = [part.strip() for part in normalized.split("/", 1)]
    if not allele_1 or not allele_2:
        errors.append(
            f"Row {row_number}: locus '{locus_name}' value '{raw}' is invalid. Both alleles are required."
        )
        return None
    return allele_1, allele_2, f"{allele_1}/{allele_2}"


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


def parse_mouse_import(
    uploaded_file,
    *,
    id_prefix: str | None = None,
    update_existing: bool = True,
) -> MouseImportResult:
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
    detected_slots = _detect_genotype_slots(list(dataframe.columns))
    slot_columns = {col for slot in detected_slots for col in _genotype_slot_columns(slot)}
    base_known_columns = set(MOUSE_BASE_COLUMNS) | slot_columns
    locus_columns = [col for col in dataframe.columns if col and col not in base_known_columns]
    strain_loci_lookup = _build_import_strain_loci_lookup()

    existing_mice: set[str] = set()
    existing_cages: set[str] = set()
    if id_prefix:
        existing_mice = set(Mouse.objects.values_list("mouse_uid", flat=True))
        existing_cages = set(Cage.objects.values_list("cage_id", flat=True))

    allowed_sex = {choice[0] for choice in Mouse.Sex.choices}
    allowed_status = {choice[0] for choice in Mouse.Status.choices}

    rows: list[dict] = []
    errors: list[str] = []
    seen_mouse_uids: set[str] = set()
    uid_to_row_number: dict[str, int] = {}

    for index, record in dataframe.iterrows():
        row_number = index + 2
        mouse_uid = _to_text(record.get("mouse_uid"))
        sex = _to_text(record.get("sex"))
        status = _to_text(record.get("status"))
        birth_date = _parse_date(record.get("birth_date"), row_number, "birth_date", errors)
        strain_line_name = _to_text(record.get("strain_line"))
        current_cage_id = _to_text(record.get("current_cage"))
        project_name = _to_text(record.get("project"))
        breeding_cage_id = _to_text(record.get("breeding_cage"))
        sire_uid = _to_text(record.get("sire"))
        dam_uid = _to_text(record.get("dam"))

        if id_prefix:
            mouse_uid = apply_import_prefix_to_id(mouse_uid, id_prefix)
            current_cage_id = _resolve_prefixed_cage_ref(current_cage_id, id_prefix, existing_cages)
            breeding_cage_id = _resolve_prefixed_cage_ref(breeding_cage_id, id_prefix, existing_cages)
            sire_uid = _resolve_prefixed_pedigree_uid(sire_uid, id_prefix, existing_mice)
            dam_uid = _resolve_prefixed_pedigree_uid(dam_uid, id_prefix, existing_mice)

        if not mouse_uid:
            errors.append(f"Row {row_number}: mouse_uid is required.")
            continue
        uid_key = mouse_uid.casefold()
        if uid_key in seen_mouse_uids:
            errors.append(f"Row {row_number}: duplicate mouse_uid '{mouse_uid}' in uploaded file.")
            continue
        seen_mouse_uids.add(uid_key)
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

        genotype_components: list[dict] = []
        component_by_locus: dict[str, dict] = {}
        for slot in detected_slots:
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
            locus = _import_locus_name(locus)
            if not locus:
                errors.append(f"Row {row_number}: {prefix}locus is required when genotype slot has data.")
                continue
            if not (allele_1 and allele_2) and zygosity:
                parsed = _parse_genotype_display(zygosity, row_number, locus, errors)
                if parsed is not None:
                    allele_1, allele_2, zygosity = parsed
            elif allele_1 and allele_2 and not zygosity:
                zygosity = f"{allele_1}/{allele_2}"

            comp = {
                "slot": slot,
                "locus_name": locus,
                "allele_1": allele_1,
                "allele_2": allele_2,
                "zygosity_display": zygosity,
                "zygosity_class": _infer_zygosity_class(allele_1, allele_2),
                "chromosome_type": _infer_chromosome_type(allele_1, allele_2),
                "is_confirmed": bool(is_confirmed) if is_confirmed is not None else False,
                "assay_date": assay_date,
                "notes": genotype_notes,
            }
            _append_import_genotype_component(
                component_by_locus,
                genotype_components,
                comp,
                row_number=row_number,
                errors=errors,
            )

        strain_template_loci = strain_loci_lookup.get(strain_line_name, set()) if strain_line_name else set()
        for locus_col in locus_columns:
            locus_name = _import_locus_name(locus_col)
            if not locus_name:
                continue
            if locus_name in component_by_locus:
                continue
            display_value = _to_text(record.get(locus_col))
            if display_value:
                parsed = _parse_genotype_display(display_value, row_number, locus_name, errors)
                if parsed is None:
                    continue
                allele_1, allele_2, zygosity = parsed
                comp = {
                    "slot": 0,
                    "locus_name": locus_name,
                    "allele_1": allele_1,
                    "allele_2": allele_2,
                    "zygosity_display": zygosity,
                    "zygosity_class": _infer_zygosity_class(allele_1, allele_2),
                    "chromosome_type": _infer_chromosome_type(allele_1, allele_2),
                    "is_confirmed": False,
                    "assay_date": None,
                    "notes": "",
                }
            elif locus_name in strain_template_loci:
                comp = _empty_import_genotype_component(locus_name)
            else:
                continue
            _append_import_genotype_component(
                component_by_locus,
                genotype_components,
                comp,
                row_number=row_number,
                errors=errors,
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
                "breeding_cage_id": breeding_cage_id,
                "sire_uid": sire_uid,
                "dam_uid": dam_uid,
                "genotype_components": genotype_components,
                "genotype_slots": genotype_components,
            }
        )

    for row in rows:
        conflict = find_conflicting_mouse(row["mouse_uid"])
        row["_update"] = conflict is not None
        if not update_existing and conflict is not None:
            row_number = uid_to_row_number.get(row["mouse_uid"], "?")
            errors.append(
                f"Row {row_number}: mouse_uid '{row['mouse_uid']}' is already used by mouse #{conflict.pk} "
                f"({conflict.get_status_display()}). IDs cannot be reused, including inactive mice."
            )

    return MouseImportResult(rows=rows, errors=errors)
