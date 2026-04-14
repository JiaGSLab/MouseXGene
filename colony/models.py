from django.db import models
from django.core.exceptions import ValidationError


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class StrainLine(TimeStampedModel):
    class Species(models.TextChoices):
        MOUSE = "mouse", "Mouse"
        RAT = "rat", "Rat"
        OTHER = "other", "Other"

    line_name = models.CharField(max_length=128, unique=True)
    species = models.CharField(max_length=20, choices=Species.choices, default=Species.MOUSE)
    background = models.CharField(max_length=128, blank=True)
    source = models.CharField(max_length=128, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("line_name",)

    def __str__(self) -> str:
        return self.line_name


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

    cage_id = models.CharField(max_length=64, unique=True)
    room = models.CharField(max_length=64, blank=True)
    rack = models.CharField(max_length=64, blank=True)
    position = models.CharField(max_length=64, blank=True)
    cage_type = models.CharField(max_length=20, choices=CageType.choices, default=CageType.STANDARD)
    purpose = models.CharField(max_length=20, choices=Purpose.choices, default=Purpose.HOLDING)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
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

    mouse_uid = models.CharField(max_length=64, unique=True)
    sex = models.CharField(max_length=1, choices=Sex.choices, default=Sex.UNKNOWN)
    birth_date = models.DateField(null=True, blank=True)
    death_date = models.DateField(null=True, blank=True)
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
    coat_color = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-birth_date", "mouse_uid")

    def clean(self) -> None:
        if self.death_date and self.birth_date and self.death_date < self.birth_date:
            raise ValidationError("death_date cannot be earlier than birth_date.")
        if self.sire_id and self.sire_id == self.id:
            raise ValidationError("A mouse cannot be its own sire.")
        if self.dam_id and self.dam_id == self.id:
            raise ValidationError("A mouse cannot be its own dam.")

    def __str__(self) -> str:
        return self.mouse_uid


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
