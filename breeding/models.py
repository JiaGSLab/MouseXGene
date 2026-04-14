from django.db import models
from django.core.exceptions import ValidationError
from datetime import timedelta

from colony.models import Cage, Mouse


class Breeding(models.Model):
    class BreedingType(models.TextChoices):
        PAIR = "pair", "Pair"
        TRIO = "trio", "Trio"

    class Status(models.TextChoices):
        SETUP = "setup", "Setup"
        PLUGGED = "plugged", "Plugged"
        PREGNANT = "pregnant", "Pregnant"
        LITTERED = "littered", "Littered"
        CLOSED = "closed", "Closed"

    breeding_code = models.CharField(max_length=64, unique=True)
    cage = models.ForeignKey(Cage, on_delete=models.PROTECT, related_name="breedings", null=True, blank=True)
    breeding_type = models.CharField(max_length=20, choices=BreedingType.choices, default=BreedingType.PAIR)
    male = models.ForeignKey(Mouse, on_delete=models.PROTECT, related_name="sired_breedings")
    female_1 = models.ForeignKey(Mouse, on_delete=models.PROTECT, related_name="maternal_breedings_primary")
    female_2 = models.ForeignKey(
        Mouse,
        on_delete=models.PROTECT,
        related_name="maternal_breedings_secondary",
        null=True,
        blank=True,
    )
    start_date = models.DateField()
    plug_date = models.DateField(null=True, blank=True)
    expected_birth_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SETUP)
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("-start_date", "breeding_code")

    def clean(self) -> None:
        if not self.cage_id:
            raise ValidationError("cage is required.")
        if self.male and self.male.sex != Mouse.Sex.MALE:
            raise ValidationError("male must be a male mouse.")
        if self.female_1 and self.female_1.sex != Mouse.Sex.FEMALE:
            raise ValidationError("female_1 must be a female mouse.")
        if self.female_2:
            if self.female_2.sex != Mouse.Sex.FEMALE:
                raise ValidationError("female_2 must be a female mouse.")
            if self.female_1_id and self.female_2_id and self.female_1_id == self.female_2_id:
                raise ValidationError("female_1 and female_2 cannot be the same mouse.")
        if self.plug_date and not self.expected_birth_date:
            self.expected_birth_date = self.plug_date + timedelta(days=19)

    def __str__(self) -> str:
        return self.breeding_code


class Litter(models.Model):
    breeding = models.ForeignKey(Breeding, on_delete=models.CASCADE, related_name="litters")
    litter_code = models.CharField(max_length=64, unique=True, null=True, blank=True)
    birth_date = models.DateField()
    total_born = models.PositiveIntegerField(null=True, blank=True)
    alive_count = models.PositiveIntegerField(null=True, blank=True)
    dead_count = models.PositiveIntegerField(null=True, blank=True)
    wean_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-birth_date", "litter_code")

    def clean(self) -> None:
        for field_name in ("total_born", "alive_count", "dead_count"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValidationError(f"{field_name} must be >= 0.")
        if (
            self.total_born is not None
            and self.alive_count is not None
            and self.dead_count is not None
            and self.alive_count + self.dead_count > self.total_born
        ):
            raise ValidationError("alive_count + dead_count must not exceed total_born.")

    def __str__(self) -> str:
        return self.litter_code or f"{self.breeding.breeding_code} - {self.birth_date}"
