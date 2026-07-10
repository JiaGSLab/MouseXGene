import re
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import models
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.text import get_valid_filename

from django.conf import settings

from core.models import ActorStampedModel, TimeStampedModel, format_project_owner_label


_genotype_summary_sync_suppressed = ContextVar("genotype_summary_sync_suppressed", default=False)


@contextmanager
def suppress_genotype_summary_signal():
    token = _genotype_summary_sync_suppressed.set(True)
    try:
        yield
    finally:
        _genotype_summary_sync_suppressed.reset(token)


class StrainLine(ActorStampedModel):
    class LocusType(models.TextChoices):
        KO_NULL = "ko_null", "KO / null allele"
        FLOXED_ALLELE = "floxed_allele", "Floxed allele"
        KNOCK_IN = "knock_in", "Knock-in"
        CRE_KI = "cre_ki", "Cre knock-in"
        CRE_ERT2_KI = "cre_ert2_ki", "CreERT2 knock-in"
        TRANSGENE = "transgene", "Transgene (Cre/CreERT2)"
        REPORTER_KI = "reporter_knock_in", "Reporter knock-in"
        REPORTER_TG = "reporter_transgene", "Reporter transgene"
        POINT_VARIANT = "point_variant", "Point mutation / variant"
        OTHER_CUSTOM = "other_custom", "Other / custom"

    class ChromosomeType(models.TextChoices):
        AUTOSOMAL = "autosomal", "Autosomal"
        X_LINKED = "x_linked", "X-linked"
        Y_LINKED = "y_linked", "Y-linked"

    class Category(models.TextChoices):
        WILD_TYPE = "wild_type", "Wild type"
        INBRED_STRAIN = "inbred_strain", "Inbred strain"
        CRE_DRIVER = "cre_driver", "Cre driver"
        REPORTER = "reporter", "Reporter"
        FLOXED_ALLELE = "floxed_allele", "Floxed allele"
        KNOCKOUT = "knockout", "Knockout"
        KNOCK_IN = "knock_in", "Knock-in"
        COMPOUND_STRAIN = "compound_strain", "Compound strain"

    class BackgroundPreset(models.TextChoices):
        C57BL_6J = "c57bl_6j", "C57BL/6J"
        BALB_C = "balb_c", "BALB/c"
        BALB_CJ = "balb_cj", "BALB/cJ"
        NOD_SCID = "nod_scid", "NOD-SCID"
        NSG = "nsg", "NSG"

    class Species(models.TextChoices):
        MOUSE = "mouse", "Mouse"
        RAT = "rat", "Rat"
        OTHER = "other", "Other"

    line_name = models.CharField(max_length=128, unique=True)
    key_name = models.CharField(max_length=64, unique=True, null=True, blank=True)
    display_name = models.CharField(max_length=255, blank=True)
    name = models.CharField(max_length=255, blank=True)
    short_name = models.CharField(max_length=128, blank=True)
    category = models.CharField(max_length=48, choices=Category.choices, default=Category.COMPOUND_STRAIN)
    gene_or_locus = models.CharField(max_length=255, blank=True)
    species = models.CharField(max_length=20, choices=Species.choices, default=Species.MOUSE)
    background = models.CharField(
        max_length=128,
        blank=True,
        default=BackgroundPreset.C57BL_6J,
    )
    source = models.CharField(max_length=128, blank=True)
    expected_loci_template = models.TextField(blank=True)
    expected_loci_config = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="owned_strain_lines",
    )
    projects = models.ManyToManyField(
        "core.Project",
        blank=True,
        related_name="strain_lines",
        help_text="Projects this strain line belongs to or can be used in.",
    )

    class Meta:
        ordering = ("line_name",)

    def _canonical_primary_name(self) -> str:
        return (self.name or self.display_name or self.line_name or self.short_name or "").strip()

    def _sync_naming_fields(self, *, previous_line_name: str | None = None) -> None:
        primary = self._canonical_primary_name()
        if not primary:
            return
        canonical_line = primary[: self._meta.get_field("line_name").max_length]
        self.name = primary
        self.display_name = primary
        self.line_name = canonical_line
        self.short_name = canonical_line
        prev_line = (previous_line_name or "").strip()
        if not self.key_name or (prev_line and self.key_name == prev_line):
            self.key_name = canonical_line

    def save(self, *args, **kwargs):
        previous_line_name = None
        if self.pk:
            previous_line_name = (
                type(self).objects.filter(pk=self.pk).values_list("line_name", flat=True).first()
            )
        self._sync_naming_fields(previous_line_name=previous_line_name)

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            fields = set(update_fields)
            fields.add("updated_at")
            naming = {"name", "display_name", "line_name", "short_name", "key_name"}
            if fields & naming or (previous_line_name or "") != (self.line_name or ""):
                fields |= naming
            kwargs["update_fields"] = list(fields)
        super().save(*args, **kwargs)

    @property
    def label(self) -> str:
        return (self.name or self.display_name or self.line_name or self.short_name or "").strip() or "—"

    def __str__(self) -> str:
        return self.label

    @property
    def owner_display(self) -> str:
        if self.owner_id:
            return format_project_owner_label(self.owner) or "—"
        if getattr(self, "created_by_id", None):
            return format_project_owner_label(self.created_by) or "—"
        return "—"

    @classmethod
    def normalize_locus_name(cls, raw_name: str) -> str:
        text = (raw_name or "").strip()
        if not text:
            return ""
        # Keep one stable logical locus name; construct/locus-type metadata should be separate.
        # Examples:
        # - "Tet2 flox" -> "Tet2"
        # - "Gpr82 KO" -> "Gpr82"
        # - "Lyz2-Cre" stays unchanged
        text = re.sub(r"\s+", " ", text)
        suffix_pattern = r"(?:\s+(?:flox|fl|ko|ki|reporter|transgene|knockout|knock-in))+$"
        cleaned = re.sub(suffix_pattern, "", text, flags=re.IGNORECASE).strip()
        return cleaned or text

    @classmethod
    def normalize_locus_type(cls, raw_type: str, *, locus_name: str = "", line_name: str = "") -> str:
        value = (raw_type or "").strip()
        if value in cls.LocusType.values:
            return value

        context = f"{locus_name or ''} {line_name or ''}".strip()
        context_lower = context.lower()
        context_compact = re.sub(r"[^a-z0-9]+", "", context_lower)

        if value == "x_linked":
            return cls.LocusType.OTHER_CUSTOM
        if value == "flox":
            return cls.LocusType.FLOXED_ALLELE
        if value == "reporter_ki":
            return cls.LocusType.REPORTER_KI
        if value == "tg_pos_neg":
            return cls.LocusType.TRANSGENE
        if value == "standard_autosomal":
            if re.search(r"(^|[^a-z0-9])(ko|null|knockout)([^a-z0-9]|$)", context_lower):
                return cls.LocusType.KO_NULL
            return cls.LocusType.OTHER_CUSTOM
        if value == "cre_transgene":
            if "creert2" in context_compact:
                return cls.LocusType.CRE_ERT2_KI
            if re.search(r"(^|[^a-z0-9])(tg|transgene)([^a-z0-9]|$)", context_lower):
                return cls.LocusType.TRANSGENE
            return cls.LocusType.CRE_KI

        aliases = {
            "ko": cls.LocusType.KO_NULL,
            "knockout": cls.LocusType.KO_NULL,
            "null": cls.LocusType.KO_NULL,
            "ki": cls.LocusType.KNOCK_IN,
            "knock-in": cls.LocusType.KNOCK_IN,
            "knock_in": cls.LocusType.KNOCK_IN,
            "cre": cls.LocusType.CRE_KI,
            "cre_ki": cls.LocusType.CRE_KI,
            "creert2": cls.LocusType.CRE_ERT2_KI,
            "creert2_ki": cls.LocusType.CRE_ERT2_KI,
            "tg": cls.LocusType.TRANSGENE,
            "transgene": cls.LocusType.TRANSGENE,
            "reporter": cls.LocusType.REPORTER_KI,
            "reporter_tg": cls.LocusType.REPORTER_TG,
            "variant": cls.LocusType.POINT_VARIANT,
            "point_mutation": cls.LocusType.POINT_VARIANT,
            "custom": cls.LocusType.OTHER_CUSTOM,
            "other": cls.LocusType.OTHER_CUSTOM,
        }
        return aliases.get(value, cls.LocusType.OTHER_CUSTOM)

    def expected_loci_entries(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        seen: set[str] = set()

        if isinstance(self.expected_loci_config, list) and self.expected_loci_config:
            for raw in self.expected_loci_config:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("locus_name", "")).strip()
                if not name:
                    continue
                if name in seen:
                    continue
                seen.add(name)
                raw_locus_type = str(raw.get("locus_type", self.LocusType.OTHER_CUSTOM)).strip()
                chromosome_type = str(raw.get("chromosome_type", self.ChromosomeType.AUTOSOMAL)).strip()

                # Backward-compat: old config may have locus_type=x_linked.
                if raw_locus_type == "x_linked":
                    raw_locus_type = self.LocusType.OTHER_CUSTOM
                    chromosome_type = self.ChromosomeType.X_LINKED

                raw_locus_type = self.normalize_locus_type(
                    raw_locus_type,
                    locus_name=name,
                    line_name=self.label,
                )
                if chromosome_type not in self.ChromosomeType.values:
                    chromosome_type = self.ChromosomeType.AUTOSOMAL
                out.append(
                    {
                        "locus_name": name,
                        "locus_type": raw_locus_type,
                        "chromosome_type": chromosome_type,
                    }
                )
            if out:
                return out

        raw = (self.expected_loci_template or "").strip()
        if not raw:
            return out
        tokens = [t.strip() for t in re.split(r"[,\n;]+", raw)]
        for token in tokens:
            if not token:
                continue
            normalized = token.strip()
            if normalized in seen:
                continue
            seen.add(normalized)
            out.append(
                {
                    "locus_name": normalized,
                    "locus_type": self.LocusType.OTHER_CUSTOM,
                    "chromosome_type": self.ChromosomeType.AUTOSOMAL,
                }
            )
        return out

    def expected_loci_list(self) -> list[str]:
        return [entry["locus_name"] for entry in self.expected_loci_entries()]

    def observed_loci_entries(self) -> list[dict[str, str]]:
        """Loci present on mice of this strain but not already in the template."""
        template_names = {entry["locus_name"] for entry in self.expected_loci_entries()}
        seen = set(template_names)
        out: list[dict[str, str]] = []
        locus_names = (
            MouseGenotypeComponent.objects.filter(mouse__strain_line=self)
            .exclude(locus_name="")
            .values_list("locus_name", flat=True)
            .distinct()
            .order_by("locus_name")
        )
        for name in locus_names:
            locus = str(name).strip()
            if not locus or locus in seen:
                continue
            seen.add(locus)
            out.append(
                {
                    "locus_name": locus,
                    "locus_type": self.LocusType.OTHER_CUSTOM,
                    "chromosome_type": self.ChromosomeType.AUTOSOMAL,
                }
            )
        return out

    def editable_loci_entries(self) -> list[dict[str, str]]:
        """Template loci plus any extra loci observed on mice (for edit form)."""
        entries = list(self.expected_loci_entries())
        seen = {entry["locus_name"] for entry in entries}
        for row in self.observed_loci_entries():
            if row["locus_name"] not in seen:
                entries.append(row)
                seen.add(row["locus_name"])
        return entries

    @property
    def category_display_label(self) -> str:
        value = (self.category or "").strip()
        if not value:
            return "—"
        try:
            return self.Category(value).label
        except ValueError:
            return value

    @property
    def background_display_label(self) -> str:
        value = (self.background or "").strip()
        if not value:
            return "—"
        try:
            return self.BackgroundPreset(value).label
        except ValueError:
            return value


def strain_line_document_upload_to(instance: "StrainLineDocument", filename: str) -> str:
    from colony.strain_pdf import storage_filename_for_description

    safe = storage_filename_for_description(instance.description, fallback=filename)
    line_id = instance.strain_line_id or "pending"
    return f"strain_lines/{line_id}/{safe}"


class StrainLineDocument(TimeStampedModel):
    """PDF introductions / protocols attached to a strain line (max 10 per line, 10 MB each)."""

    class DescriptionKind(models.TextChoices):
        STRAIN_LINE_INFO = "strain_line_info", "Strain line info"
        GENOTYPE_INFO = "genotype_info", "Genotype info"
        HUSBANDRY = "husbandry", "Husbandry"
        GENETICS = "genetics", "Genetics"
        COLONY_NOTES = "colony_notes", "Colony notes"
        PROTOCOL = "protocol", "Protocol"
        OTHER = "other", "Other"
        CUSTOM = "custom", "Custom"

    strain_line = models.ForeignKey(StrainLine, on_delete=models.CASCADE, related_name="documents")
    file = models.FileField(upload_to=strain_line_document_upload_to)
    description = models.CharField(max_length=255, blank=True)
    description_kind = models.CharField(
        max_length=32,
        choices=DescriptionKind.choices,
        default=DescriptionKind.CUSTOM,
    )
    original_filename = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="strain_line_documents",
    )

    class Meta:
        ordering = ("created_at", "id")

    def save(self, *args, **kwargs):
        if self.description:
            self.original_filename = get_valid_filename(self.description) or self.description
        elif self.file and not self.original_filename:
            self.original_filename = get_valid_filename(self.file.name) or self.file.name
        if self.file:
            try:
                self.file_size = self.file.size
            except Exception:
                pass
        super().save(*args, **kwargs)

    @property
    def display_name(self) -> str:
        if self.description:
            return self.description
        return self.original_filename or (self.file.name.split("/")[-1] if self.file else "PDF")

    def __str__(self) -> str:
        return f"{self.strain_line_id}: {self.display_name}"


class Colony(ActorStampedModel):
    """Actual maintained animal group for one project and one strain line."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="colonies",
    )
    strain_line = models.ForeignKey(
        StrainLine,
        on_delete=models.PROTECT,
        related_name="colonies",
    )
    name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("project__name", "strain_line__line_name", "name")
        constraints = [
            models.UniqueConstraint(fields=["project", "strain_line"], name="colony_unique_project_strain"),
        ]
        indexes = [
            models.Index(fields=["status", "project"], name="colony_colony_status_proj"),
            models.Index(fields=["status", "strain_line"], name="colony_colony_status_strain"),
        ]

    @property
    def owner(self):
        return self.project.owner if self.project_id else None

    @property
    def owner_display(self) -> str:
        return self.project.owner_display if self.project_id else "—"

    def default_name(self) -> str:
        project_name = self.project.name if self.project_id else "Project"
        strain_name = self.strain_line.label if self.strain_line_id else "Strain line"
        return f"{project_name} / {strain_name}"

    def save(self, *args, **kwargs):
        if not (self.name or "").strip():
            self.name = self.default_name()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "name" not in update_fields:
                kwargs["update_fields"] = list(update_fields) + ["name"]
        super().save(*args, **kwargs)

    @classmethod
    def get_or_create_for(cls, *, project_id: int, strain_line_id: int) -> "Colony":
        colony, _created = cls.objects.get_or_create(
            project_id=project_id,
            strain_line_id=strain_line_id,
            defaults={"name": ""},
        )
        return colony

    def __str__(self) -> str:
        return self.name or self.default_name()


class Cage(ActorStampedModel):
    class CageType(models.TextChoices):
        STANDARD = "standard", "Standard"
        BREEDING = "breeding", "Breeding"
        WEANING = "weaning", "Weaning"
        QUARANTINE = "quarantine", "Quarantine"

    class Purpose(models.TextChoices):
        HOLDING = "holding", "Holding"
        BREEDING = "breeding", "Breeding"
        EXPERIMENT = "experiment", "Experiment"
        RETIRED = "retired", "Retired"

    class CageUse(models.TextChoices):
        HOLDING = "holding", "Holding"
        BREEDING = "breeding", "Breeding"
        WEANING = "weaning", "Weaning"
        EXPERIMENT = "experiment", "Experiment"
        QUARANTINE = "quarantine", "Quarantine"
        RETIRED = "retired", "Retired"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"
        SANITIZING = "sanitizing", "Sanitizing"
        RETIRED = "retired", "Retired"
        ARCHIVED = "archived", "Archived"

    cage_id = models.CharField(max_length=64, unique=True)
    created_date = models.DateField(null=True, blank=True)
    room = models.CharField(max_length=64, blank=True)
    rack = models.CharField(max_length=64, blank=True)
    position = models.CharField(max_length=64, blank=True)
    cage_type = models.CharField(max_length=20, choices=CageType.choices, default=CageType.STANDARD)
    purpose = models.CharField(max_length=20, choices=Purpose.choices, default=Purpose.HOLDING)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="cages",
        null=True,
        blank=True,
        help_text="Home project for this cage. Empty only when ownership cannot be inferred yet.",
    )
    colony = models.ForeignKey(
        Colony,
        on_delete=models.SET_NULL,
        related_name="cages",
        null=True,
        blank=True,
        help_text="Optional default colony for this cage.",
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("cage_id",)
        indexes = [
            models.Index(fields=["status", "cage_id"], name="colony_cage_status_id"),
            models.Index(fields=["status", "project"], name="colony_cage_status_proj"),
            models.Index(fields=["status", "colony"], name="colony_cage_status_colony"),
        ]

    @property
    def owner_display(self) -> str:
        return self.project.owner_display if self.project_id else "—"

    @classmethod
    def cage_use_choices(cls, *, include_retired: bool = True) -> list[tuple[str, str]]:
        choices = list(cls.CageUse.choices)
        if not include_retired:
            choices = [choice for choice in choices if choice[0] != cls.CageUse.RETIRED]
        return choices

    @classmethod
    def cage_use_from_parts(cls, *, cage_type: str = "", purpose: str = "") -> str:
        if purpose == cls.Purpose.RETIRED:
            return cls.CageUse.RETIRED
        if purpose == cls.Purpose.BREEDING or cage_type == cls.CageType.BREEDING:
            return cls.CageUse.BREEDING
        if cage_type == cls.CageType.WEANING:
            return cls.CageUse.WEANING
        if purpose == cls.Purpose.EXPERIMENT:
            return cls.CageUse.EXPERIMENT
        if cage_type == cls.CageType.QUARANTINE:
            return cls.CageUse.QUARANTINE
        return cls.CageUse.HOLDING

    @property
    def cage_use(self) -> str:
        return self.cage_use_from_parts(cage_type=self.cage_type, purpose=self.purpose)

    def get_cage_use_display(self) -> str:
        return self.CageUse(self.cage_use).label

    def set_cage_use(self, cage_use: str) -> None:
        if cage_use == self.CageUse.BREEDING:
            self.cage_type = self.CageType.BREEDING
            self.purpose = self.Purpose.BREEDING
        elif cage_use == self.CageUse.WEANING:
            self.cage_type = self.CageType.WEANING
            self.purpose = self.Purpose.HOLDING
        elif cage_use == self.CageUse.EXPERIMENT:
            self.cage_type = self.CageType.STANDARD
            self.purpose = self.Purpose.EXPERIMENT
        elif cage_use == self.CageUse.QUARANTINE:
            self.cage_type = self.CageType.QUARANTINE
            self.purpose = self.Purpose.HOLDING
        elif cage_use == self.CageUse.RETIRED:
            self.cage_type = self.CageType.STANDARD
            self.purpose = self.Purpose.RETIRED
        else:
            self.cage_type = self.CageType.STANDARD
            self.purpose = self.Purpose.HOLDING

    def save(self, *args, **kwargs):
        if self.colony_id and not self.project_id:
            self.project_id = self.colony.project_id
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "project" not in update_fields:
                kwargs["update_fields"] = list(update_fields) + ["project"]
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.cage_id


GENOTYPE_SUMMARY_UNCHARACTERIZED = "ND"
GENOTYPE_SUMMARY_MAX_LENGTH = 512


class Mouse(ActorStampedModel):
    class Sex(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"
        UNKNOWN = "U", "Unknown"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DEAD = "dead", "Dead"
        CULLED = "culled", "Culled"
        TRANSFERRED = "transferred", "Transferred"
        EUTHANIZED = "euthanized", "Euthanized"
        ARCHIVED = "archived", "Archived"

    mouse_uid = models.CharField(max_length=64, unique=True)
    sex = models.CharField(max_length=1, choices=Sex.choices, default=Sex.UNKNOWN)
    birth_date = models.DateField(null=True, blank=True)
    death_date = models.DateField(null=True, blank=True)
    euthanasia_date = models.DateField(null=True, blank=True)
    death_reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    strain_line = models.ForeignKey(StrainLine, on_delete=models.PROTECT, related_name="mice")
    colony = models.ForeignKey(
        Colony,
        on_delete=models.PROTECT,
        related_name="mice",
        null=True,
        blank=True,
        help_text="Actual project-specific colony/stock this mouse belongs to.",
    )
    current_cage = models.ForeignKey(
        Cage,
        on_delete=models.SET_NULL,
        related_name="current_mice",
        null=True,
        blank=True,
    )
    sire = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="offspring_from_sire",
        null=True,
        blank=True,
    )
    dam = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="offspring_from_dam",
        null=True,
        blank=True,
    )
    possible_dams = models.ManyToManyField(
        "self",
        symmetrical=False,
        related_name="possible_offspring_from_dam",
        blank=True,
        help_text="Candidate dams when the exact mother is unknown, for example trio breeding litters.",
    )
    source_breeding = models.ForeignKey(
        "breeding.Breeding",
        on_delete=models.SET_NULL,
        related_name="offspring_mice",
        null=True,
        blank=True,
        help_text="Breeding cage / mating this mouse was born from (when specific dam is unknown).",
    )
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="mice",
    )
    ear_tag = models.CharField(max_length=64, blank=True)
    toe_tag = models.CharField(max_length=64, blank=True)
    origin = models.CharField(max_length=255, blank=True)
    coat_color = models.CharField(max_length=64, blank=True)
    genotype_summary = models.CharField(max_length=512, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-birth_date", "mouse_uid")
        indexes = [
            models.Index(fields=["status", "birth_date"], name="colony_mouse_status_birth"),
            models.Index(fields=["status", "mouse_uid"], name="colony_mouse_status_uid"),
            models.Index(fields=["status", "project"], name="colony_mouse_status_proj"),
            models.Index(fields=["status", "strain_line"], name="colony_mouse_status_strain"),
            models.Index(fields=["status", "colony"], name="colony_mouse_status_colony"),
            models.Index(fields=["status", "current_cage"], name="colony_mouse_status_cage"),
        ]

    def save(self, *args, **kwargs):
        if self.project_id and self.strain_line_id:
            if (
                not self.colony_id
                or self.colony.project_id != self.project_id
                or self.colony.strain_line_id != self.strain_line_id
            ):
                self.colony = Colony.get_or_create_for(
                    project_id=self.project_id,
                    strain_line_id=self.strain_line_id,
                )
                update_fields = kwargs.get("update_fields")
                if update_fields is not None and "colony" not in update_fields:
                    kwargs["update_fields"] = list(update_fields) + ["colony"]
        super().save(*args, **kwargs)

    def clean(self) -> None:
        if self.death_date and self.birth_date and self.death_date < self.birth_date:
            raise ValidationError("death_date cannot be earlier than birth_date.")
        if self.euthanasia_date and self.birth_date and self.euthanasia_date < self.birth_date:
            raise ValidationError("euthanasia_date cannot be earlier than birth_date.")
        if self.sire_id and self.sire_id == self.id:
            raise ValidationError("A mouse cannot be its own sire.")
        if self.dam_id and self.dam_id == self.id:
            raise ValidationError("A mouse cannot be its own dam.")

    def __str__(self) -> str:
        return self.mouse_uid

    @staticmethod
    def _genotype_component_display(component: "MouseGenotypeComponent | None") -> str:
        if component is None:
            return ""
        allele_1 = (component.allele_display_1 or "").strip()
        allele_2 = (component.allele_display_2 or "").strip()
        zygosity = (component.zygosity or "").strip()
        if allele_1 == "-":
            allele_1 = ""
        if allele_2 == "-":
            allele_2 = ""
        if zygosity == "-":
            zygosity = ""
        if allele_1 and allele_2:
            return f"{allele_1}/{allele_2}"
        if zygosity:
            return zygosity
        return ""

    def _genotype_summary_part(self, label: str, component: "MouseGenotypeComponent | None") -> str:
        display = self._genotype_component_display(component)
        if display:
            return f"{label}:{display}"
        return f"{label}:{GENOTYPE_SUMMARY_UNCHARACTERIZED}"

    def compute_genotype_summary(self) -> str:
        """List every template locus; use ND when genotype is blank or missing."""
        components = list(
            self.genotype_components.select_related("strain_line").order_by("sort_order", "id")
        )
        comp_by_key: dict[str, MouseGenotypeComponent] = {}
        for component in components:
            locus = StrainLine.normalize_locus_name((component.locus_name or "").strip())
            if not locus:
                continue
            comp_by_key[locus.casefold()] = component

        parts: list[str] = []
        seen: set[str] = set()

        if self.strain_line_id:
            for entry in self.strain_line.expected_loci_entries():
                label = entry["locus_name"]
                key = (StrainLine.normalize_locus_name(label) or label).casefold()
                seen.add(key)
                parts.append(self._genotype_summary_part(label, comp_by_key.get(key)))

        for component in components:
            label = StrainLine.normalize_locus_name((component.locus_name or "").strip())
            if not label:
                continue
            key = label.casefold()
            if key in seen:
                continue
            seen.add(key)
            parts.append(self._genotype_summary_part(label, component))

        if not parts and not self.strain_line_id:
            for component in components:
                fallback_label = (
                    component.strain_line.short_name
                    or component.strain_line.display_name
                    or component.strain_line.name
                    or component.strain_line.line_name
                )
                label = (component.locus_name or "").strip() or fallback_label
                parts.append(self._genotype_summary_part(label, component))

        summary = "; ".join(parts)
        if len(summary) <= GENOTYPE_SUMMARY_MAX_LENGTH:
            return summary
        return summary[: GENOTYPE_SUMMARY_MAX_LENGTH - 3].rstrip() + "..."

    def rebuild_genotype_summary(self, *, save: bool = True) -> str:
        summary = self.compute_genotype_summary()
        self.genotype_summary = summary
        if save and self.pk:
            now = timezone.now()
            Mouse.objects.filter(pk=self.pk).update(genotype_summary=summary, updated_at=now)
            self.updated_at = now
        return summary

    def ensure_template_genotype_components(
        self,
        *,
        extra_loci: list[str] | None = None,
        include_strain_template: bool = True,
    ) -> int:
        template_entries = self.strain_line.expected_loci_entries() if self.strain_line_id else []
        raw_loci = self.strain_line.expected_loci_list() if (include_strain_template and self.strain_line_id) else []
        raw_loci.extend(extra_loci or [])
        loci: list[tuple[str, str]] = []
        requested_keys: set[str] = set()
        for raw_locus in raw_loci:
            display_locus = " ".join((raw_locus or "").strip().split())
            locus_key = StrainLine.normalize_locus_name(display_locus).casefold()
            if not display_locus or not locus_key or locus_key in requested_keys:
                continue
            requested_keys.add(locus_key)
            loci.append((display_locus, locus_key))
        if not loci:
            return 0
        entry_by_key = {
            StrainLine.normalize_locus_name(e["locus_name"]).casefold(): e
            for e in template_entries
        }
        existing: set[str] = set()
        for c in self.genotype_components.all():
            locus_key = c.locus_key or StrainLine.normalize_locus_name(c.locus_name).casefold()
            if locus_key:
                existing.add(locus_key)
        current_max_sort = self.genotype_components.aggregate(models.Max("sort_order")).get("sort_order__max") or 0
        to_create: list["MouseGenotypeComponent"] = []
        next_sort = current_max_sort + 1
        for locus, locus_key in loci:
            if locus_key in existing:
                continue
            entry = entry_by_key.get(locus_key) or {}
            chromosome_type = str(entry.get("chromosome_type") or "").strip()
            if chromosome_type not in MouseGenotypeComponent.ChromosomeType.values:
                chromosome_type = MouseGenotypeComponent.ChromosomeType.UNKNOWN
            to_create.append(
                MouseGenotypeComponent(
                    mouse=self,
                    strain_line=self.strain_line,
                    locus_name=locus,
                    locus_key=locus_key,
                    chromosome_type=chromosome_type,
                    zygosity_class=MouseGenotypeComponent.ZygosityClass.UNKNOWN,
                    sort_order=next_sort,
                )
            )
            next_sort += 1
        if not to_create:
            return 0
        MouseGenotypeComponent.objects.bulk_create(to_create)
        if hasattr(self, "_prefetched_objects_cache"):
            self._prefetched_objects_cache.pop("genotype_components", None)
        self.rebuild_genotype_summary(save=True)
        return len(to_create)


class MouseExperimentAssignment(ActorStampedModel):
    """History row for mice currently assigned to an experiment."""

    mouse = models.ForeignKey(Mouse, on_delete=models.CASCADE, related_name="experiment_assignments")
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    note = models.TextField(blank=True)

    class Meta:
        ordering = ("-started_at", "-created_at")
        indexes = [
            models.Index(fields=["mouse", "ended_at"], name="colony_mouse_exp_active"),
            models.Index(fields=["started_at"], name="colony_mouse_exp_started"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["mouse"],
                condition=models.Q(ended_at__isnull=True),
                name="colony_mouse_one_active_exp",
            )
        ]

    @property
    def is_active(self) -> bool:
        return self.ended_at is None

    def __str__(self) -> str:
        state = "active" if self.is_active else "ended"
        return f"{self.mouse.mouse_uid} experiment assignment ({state})"


class MouseGenotypeComponent(TimeStampedModel):
    class ChromosomeType(models.TextChoices):
        AUTOSOMAL = "autosomal", "Autosomal"
        X_LINKED = "x_linked", "X-linked"
        Y_LINKED = "y_linked", "Y-linked"
        UNKNOWN = "unknown", "Unknown"

    class ZygosityClass(models.TextChoices):
        WT = "wt", "WT"
        HET = "het", "Heterozygous"
        HOM = "hom", "Homozygous"
        HEMIZYGOUS = "hemizygous", "Hemizygous"
        UNKNOWN = "unknown", "Unknown"

    mouse = models.ForeignKey(Mouse, on_delete=models.CASCADE, related_name="genotype_components")
    strain_line = models.ForeignKey(StrainLine, on_delete=models.PROTECT, related_name="mouse_components")
    locus_name = models.CharField(max_length=128, blank=True)
    locus_key = models.CharField(max_length=128, blank=True, editable=False)
    chromosome_type = models.CharField(
        max_length=16,
        choices=ChromosomeType.choices,
        default=ChromosomeType.UNKNOWN,
    )
    zygosity = models.CharField(max_length=32, blank=True)
    zygosity_class = models.CharField(
        max_length=16,
        choices=ZygosityClass.choices,
        default=ZygosityClass.UNKNOWN,
    )
    allele_display_1 = models.CharField(max_length=64, blank=True)
    allele_display_2 = models.CharField(max_length=64, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("sort_order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["mouse", "locus_key"],
                condition=~models.Q(locus_key=""),
                name="colony_mouse_locus_unique_ci",
            ),
        ]

    def save(self, *args, **kwargs):
        self.locus_name = (self.locus_name or "").strip()
        self.locus_key = StrainLine.normalize_locus_name(self.locus_name).casefold()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        allele_1 = (self.allele_display_1 or "").strip()
        allele_2 = (self.allele_display_2 or "").strip()
        sex = self.mouse.sex

        # Keep explicit zygosity display synchronized with allele fields when both are present.
        if allele_1 and allele_2:
            self.zygosity = f"{allele_1}/{allele_2}"

        if self.chromosome_type == self.ChromosomeType.AUTOSOMAL:
            if (allele_1 and not allele_2) or (allele_2 and not allele_1):
                raise ValidationError("Autosomal loci require two alleles or both left blank.")
            if allele_2.upper() == "Y":
                raise ValidationError("Autosomal loci cannot use Y as allele_2.")
            return

        if self.chromosome_type == self.ChromosomeType.X_LINKED:
            if sex == Mouse.Sex.MALE:
                if allele_1 and not allele_2:
                    self.allele_display_2 = "Y"
                elif allele_2 and not allele_1:
                    raise ValidationError("For X-linked male records, allele_1 is required.")
                elif allele_2 and allele_2.upper() != "Y":
                    raise ValidationError("For X-linked male records, allele_2 should be 'Y'.")
                if self.allele_display_1 and self.allele_display_2:
                    self.zygosity = f"{self.allele_display_1}/{self.allele_display_2}"
            elif sex == Mouse.Sex.FEMALE:
                if (allele_1 and not allele_2) or (allele_2 and not allele_1):
                    raise ValidationError("For X-linked female records, provide both alleles.")
                if allele_2.upper() == "Y":
                    raise ValidationError("Female X-linked records cannot use Y as allele_2.")
            return

        if self.chromosome_type == self.ChromosomeType.Y_LINKED:
            if sex == Mouse.Sex.FEMALE:
                raise ValidationError("Female mice cannot carry Y-linked loci.")
            if allele_1 and not allele_2:
                self.allele_display_2 = "Y"
            elif allele_2 and not allele_1:
                raise ValidationError("For Y-linked records, allele_1 is required.")
            elif allele_2 and allele_2.upper() != "Y":
                raise ValidationError("For Y-linked records, allele_2 should be 'Y'.")
            if self.allele_display_1 and self.allele_display_2:
                self.zygosity = f"{self.allele_display_1}/{self.allele_display_2}"

    def __str__(self) -> str:
        return f"{self.mouse.mouse_uid} - {self.strain_line} {self.zygosity}".strip()


class CageMembership(TimeStampedModel):
    mouse = models.ForeignKey(Mouse, on_delete=models.CASCADE, related_name="cage_memberships")
    cage = models.ForeignKey(Cage, on_delete=models.CASCADE, related_name="memberships")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=128, blank=True)
    is_current = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-start_date", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["mouse"],
                condition=models.Q(is_current=True),
                name="colony_mouse_one_current_cage",
            ),
        ]

    def clean(self) -> None:
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError("end_date cannot be earlier than start_date.")
        if self.is_current and self.end_date:
            raise ValidationError("Current cage membership cannot have an end_date.")

    def __str__(self) -> str:
        return f"{self.mouse} in {self.cage}"


@receiver(post_save, sender=MouseGenotypeComponent)
def _sync_mouse_genotype_summary_on_save(sender, instance: MouseGenotypeComponent, **kwargs) -> None:
    if _genotype_summary_sync_suppressed.get():
        return
    instance.mouse.rebuild_genotype_summary(save=True)


@receiver(post_delete, sender=MouseGenotypeComponent)
def _sync_mouse_genotype_summary_on_delete(sender, instance: MouseGenotypeComponent, **kwargs) -> None:
    if _genotype_summary_sync_suppressed.get():
        return
    instance.mouse.rebuild_genotype_summary(save=True)


@receiver(pre_save, sender=Mouse)
def _remember_mouse_previous_cage(sender, instance: Mouse, **kwargs) -> None:
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and not {"current_cage", "status"}.intersection(update_fields):
        instance._mxg_previous_cage_id = instance.current_cage_id
        instance._mxg_previous_status = instance.status
        return
    if instance.pk:
        previous = Mouse.objects.filter(pk=instance.pk).values("current_cage_id", "status").first() or {}
        instance._mxg_previous_cage_id = previous.get("current_cage_id")
        instance._mxg_previous_status = previous.get("status")
    else:
        instance._mxg_previous_cage_id = None
        instance._mxg_previous_status = None


@receiver(post_save, sender=Mouse)
def _sync_cages_after_mouse_save(sender, instance: Mouse, **kwargs) -> None:
    if kwargs.get("raw"):
        return
    previous_cage_id = getattr(instance, "_mxg_previous_cage_id", instance.current_cage_id)
    previous_status = getattr(instance, "_mxg_previous_status", instance.status)
    if previous_cage_id == instance.current_cage_id and previous_status == instance.status:
        return
    from colony.cage_lifecycle import sync_cages_after_mouse_change

    sync_cages_after_mouse_change(
        current_cage_id=instance.current_cage_id,
        previous_cage_id=previous_cage_id,
    )


@receiver(post_save, sender=Mouse)
def _sync_strain_line_projects_after_mouse_save(sender, instance: Mouse, **kwargs) -> None:
    if kwargs.get("raw"):
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and not {"strain_line", "project"}.intersection(update_fields):
        return
    if instance.strain_line_id and instance.project_id:
        instance.strain_line.projects.add(instance.project_id)


@receiver(post_save, sender=Mouse)
def _sync_cage_home_from_mouse_save(sender, instance: Mouse, **kwargs) -> None:
    if kwargs.get("raw") or not instance.current_cage_id:
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and not {"current_cage", "project", "colony"}.intersection(update_fields):
        return
    updates: list[str] = []
    cage = Cage.objects.filter(pk=instance.current_cage_id).select_related("colony").first()
    if cage is None:
        return
    if instance.project_id and not cage.project_id:
        cage.project_id = instance.project_id
        updates.append("project")
    if instance.colony_id and not cage.colony_id:
        cage.colony_id = instance.colony_id
        updates.append("colony")
    if updates:
        updates.append("updated_at")
        cage.save(update_fields=updates)
