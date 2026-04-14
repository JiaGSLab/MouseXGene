from django.db import models
from django.core.exceptions import ValidationError

from colony.models import Mouse


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Gene(TimeStampedModel):
    symbol = models.CharField(max_length=64, unique=True)
    full_name = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("symbol",)

    def __str__(self) -> str:
        return self.symbol


class Allele(TimeStampedModel):
    class AlleleType(models.TextChoices):
        WILD_TYPE = "wt", "Wild type"
        KNOCKOUT = "ko", "Knockout"
        KNOCKIN = "ki", "Knock-in"
        TRANSGENE = "tg", "Transgene"
        CONDITIONAL = "conditional", "Conditional"
        OTHER = "other", "Other"

    gene = models.ForeignKey(Gene, on_delete=models.CASCADE, related_name="alleles")
    allele_name = models.CharField(max_length=128)
    allele_type = models.CharField(max_length=20, choices=AlleleType.choices, default=AlleleType.OTHER)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("gene", "allele_name")
        ordering = ("gene__symbol", "allele_name")

    def __str__(self) -> str:
        return f"{self.gene.symbol}:{self.allele_name}"


class MouseGenotype(TimeStampedModel):
    mouse = models.ForeignKey(Mouse, on_delete=models.CASCADE, related_name="genotypes")
    gene = models.ForeignKey(
        Gene,
        on_delete=models.PROTECT,
        related_name="mouse_genotypes",
        null=True,
        blank=True,
    )
    locus_name = models.CharField(max_length=128, blank=True)
    allele_1 = models.CharField(max_length=128, blank=True)
    allele_2 = models.CharField(max_length=128, blank=True)
    zygosity_display = models.CharField(max_length=128, blank=True)
    is_confirmed = models.BooleanField(default=False)
    assay_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-assay_date", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["mouse", "gene", "locus_name"],
                name="uniq_mouse_gene_locus_genotype",
            )
        ]

    def clean(self) -> None:
        if not self.gene and not self.locus_name:
            raise ValidationError("Either gene or locus_name must be provided.")

    def __str__(self) -> str:
        label = self.gene.symbol if self.gene else self.locus_name or "unknown-locus"
        return f"{self.mouse.mouse_uid} - {label}"
