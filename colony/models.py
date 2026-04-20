import re

from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.core.exceptions import ValidationError


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class StrainLine(TimeStampedModel):
    class LocusType(models.TextChoices):
        STANDARD_AUTOSOMAL = "standard_autosomal", "Standard autosomal"
        FLOX = "flox", "Flox"
        CRE_TRANSGENE = "cre_transgene", "Cre transgene"
        REPORTER_KI = "reporter_ki", "Reporter KI"
        CUSTOM = "custom", "Custom"

    class ChromosomeType(models.TextChoices):
        AUTOSOMAL = "autosomal", "Autosomal"
        X_LINKED = "x_linked", "X-linked"
        Y_LINKED = "y_linked", "Y-linked"

    class Category(models.TextChoices):
        CRE = "cre", "Cre"
        CRE_ERT2 = "creERT2", "CreERT2"
        FLOX = "flox", "Flox"
        KO = "ko", "KO"
        KI = "ki", "KI"
        REPORTER = "reporter", "Reporter"
        TRANSGENE = "transgene", "Transgene"
        OTHER = "other", "Other"

    class Species(models.TextChoices):
        MOUSE = "mouse", "Mouse"
        RAT = "rat", "Rat"
        OTHER = "other", "Other"

    line_name = models.CharField(max_length=128, unique=True)
    key_name = models.CharField(max_length=64, unique=True, null=True, blank=True)
    display_name = models.CharField(max_length=255, blank=True)
    name = models.CharField(max_length=255, blank=True)
    short_name = models.CharField(max_length=128, blank=True)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    gene_or_locus = models.CharField(max_length=255, blank=True)
    species = models.CharField(max_length=20, choices=Species.choices, default=Species.MOUSE)
    background = models.CharField(max_length=128, blank=True)
    source = models.CharField(max_length=128, blank=True)
    expected_loci_template = models.TextField(blank=True)
    expected_loci_config = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("line_name",)

    def save(self, *args, **kwargs):
        # Backward-compatible synchronization between legacy and new naming fields.
        if not self.name:
            self.name = self.display_name or self.line_name
        if not self.short_name:
            self.short_name = self.key_name or self.display_name or self.line_name
        if not self.line_name:
            self.line_name = self.short_name or self.name
        if not self.display_name:
            self.display_name = self.name
        if not self.key_name and self.short_name:
            self.key_name = self.short_name
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.short_name or self.display_name or self.name or self.line_name

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

    def expected_loci_entries(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        seen: set[str] = set()

        if isinstance(self.expected_loci_config, list) and self.expected_loci_config:
            for raw in self.expected_loci_config:
                if not isinstance(raw, dict):
                    continue
                name = self.normalize_locus_name(str(raw.get("locus_name", "")).strip())
                if not name:
                    continue
                key = name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                raw_locus_type = str(raw.get("locus_type", self.LocusType.CUSTOM)).strip()
                chromosome_type = str(raw.get("chromosome_type", self.ChromosomeType.AUTOSOMAL)).strip()

                # Backward-compat: old config may have locus_type=x_linked.
                if raw_locus_type == "x_linked":
                    raw_locus_type = self.LocusType.CUSTOM
                    chromosome_type = self.ChromosomeType.X_LINKED

                if raw_locus_type not in self.LocusType.values:
                    raw_locus_type = self.LocusType.CUSTOM
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
            normalized = self.normalize_locus_name(token)
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "locus_name": normalized,
                    "locus_type": self.LocusType.CUSTOM,
                    "chromosome_type": self.ChromosomeType.AUTOSOMAL,
                }
            )
        return out

    def expected_loci_list(self) -> list[str]:
        return [entry["locus_name"] for entry in self.expected_loci_entries()]


class Cage(TimeStampedModel):
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
    archived_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("cage_id",)

    def __str__(self) -> str:
        return self.cage_id


class Mouse(TimeStampedModel):
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

    def rebuild_genotype_summary(self, *, save: bool = True) -> str:
        components = self.genotype_components.select_related("strain_line").order_by("sort_order", "id")
        parts: list[str] = []
        for component in components:
            fallback_label = (
                component.strain_line.short_name
                or component.strain_line.display_name
                or component.strain_line.name
                or component.strain_line.line_name
            )
            label = (component.locus_name or "").strip() or fallback_label
            allele_1 = (component.allele_display_1 or "").strip()
            allele_2 = (component.allele_display_2 or "").strip()
            genotype_part = ""
            if allele_1 and allele_2:
                genotype_part = f"{allele_1}/{allele_2}"
            elif component.zygosity:
                genotype_part = component.zygosity.strip()
            if genotype_part:
                parts.append(f"{label}{genotype_part}")
        summary = "; ".join(parts)
        self.genotype_summary = summary
        if save:
            self.save(update_fields=["genotype_summary", "updated_at"])
        return summary

    def ensure_template_genotype_components(
        self,
        *,
        extra_loci: list[str] | None = None,
        include_strain_template: bool = True,
    ) -> int:
        loci = self.strain_line.expected_loci_list() if (include_strain_template and self.strain_line_id) else []
        loci = [self.strain_line.normalize_locus_name(l) for l in loci if self.strain_line.normalize_locus_name(l)]
        if extra_loci:
            seen = {l.casefold() for l in loci}
            for locus in extra_loci:
                text = self.strain_line.normalize_locus_name((locus or "").strip())
                if not text:
                    continue
                key = text.casefold()
                if key in seen:
                    continue
                seen.add(key)
                loci.append(text)
        if not loci:
            return 0
        existing: set[str] = set()
        for c in self.genotype_components.all():
            raw = (c.locus_name or "").strip()
            if not raw:
                continue
            normalized = self.strain_line.normalize_locus_name(raw)
            if normalized and raw != normalized:
                c.locus_name = normalized
                c.save(update_fields=["locus_name", "updated_at"])
            if normalized:
                existing.add(normalized.casefold())
        current_max_sort = self.genotype_components.aggregate(models.Max("sort_order")).get("sort_order__max") or 0
        to_create: list["MouseGenotypeComponent"] = []
        next_sort = current_max_sort + 1
        for locus in loci:
            if locus.casefold() in existing:
                continue
            to_create.append(
                MouseGenotypeComponent(
                    mouse=self,
                    strain_line=self.strain_line,
                    locus_name=locus,
                    chromosome_type=MouseGenotypeComponent.ChromosomeType.UNKNOWN,
                    zygosity_class=MouseGenotypeComponent.ZygosityClass.UNKNOWN,
                    sort_order=next_sort,
                )
            )
            next_sort += 1
        if not to_create:
            return 0
        MouseGenotypeComponent.objects.bulk_create(to_create)
        self.rebuild_genotype_summary(save=True)
        return len(to_create)


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

    def clean(self) -> None:
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError("end_date cannot be earlier than start_date.")
        if self.is_current and self.end_date:
            raise ValidationError("Current cage membership cannot have an end_date.")

    def __str__(self) -> str:
        return f"{self.mouse} in {self.cage}"


@receiver(post_save, sender=MouseGenotypeComponent)
def _sync_mouse_genotype_summary_on_save(sender, instance: MouseGenotypeComponent, **kwargs) -> None:
    instance.mouse.rebuild_genotype_summary(save=True)


@receiver(post_delete, sender=MouseGenotypeComponent)
def _sync_mouse_genotype_summary_on_delete(sender, instance: MouseGenotypeComponent, **kwargs) -> None:
    instance.mouse.rebuild_genotype_summary(save=True)
