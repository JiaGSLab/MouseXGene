from django.db import models

from colony.models import Mouse


class Breeding(models.Model):
    code = models.CharField(max_length=64, unique=True)
    male = models.ForeignKey(Mouse, on_delete=models.PROTECT, related_name="sired_breedings")
    female = models.ForeignKey(Mouse, on_delete=models.PROTECT, related_name="maternal_breedings")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.code


class Litter(models.Model):
    breeding = models.ForeignKey(Breeding, on_delete=models.CASCADE, related_name="litters")
    litter_date = models.DateField()
    size = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"{self.breeding.code} - {self.litter_date}"
