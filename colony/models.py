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
        on_delete=models.SET_NULL,
        related_name="mice",
        null=True,
        blank=True,
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
            label = (
                component.strain_line.short_name
                or component.strain_line.display_name
                or component.strain_line.name
                or component.strain_line.line_name
            )
            if component.zygosity:
                parts.append(f"{label}{component.zygosity}")
            else:
                parts.append(label)
        summary = "; ".join(parts)
        self.genotype_summary = summary
        if save:
            self.save(update_fields=["genotype_summary", "updated_at"])
        return summary


class MouseGenotypeComponent(TimeStampedModel):
    mouse = models.ForeignKey(Mouse, on_delete=models.CASCADE, related_name="genotype_components")
    strain_line = models.ForeignKey(StrainLine, on_delete=models.PROTECT, related_name="mouse_components")
    zygosity = models.CharField(max_length=32, blank=True)
    allele_display_1 = models.CharField(max_length=64, blank=True)
    allele_display_2 = models.CharField(max_length=64, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("sort_order", "id")

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
