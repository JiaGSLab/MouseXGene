from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("genotypes", "0002_gene_display_name_gene_is_active_gene_key_name"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="mousegenotype",
            name="uniq_mouse_gene_locus_genotype",
        ),
        migrations.AddConstraint(
            model_name="mousegenotype",
            constraint=models.UniqueConstraint(
                fields=("mouse", "gene", "locus_name"),
                condition=Q(gene__isnull=False),
                name="uniq_mouse_gene_locus_genotype",
            ),
        ),
        migrations.AddConstraint(
            model_name="mousegenotype",
            constraint=models.UniqueConstraint(
                fields=("mouse", "locus_name"),
                condition=Q(gene__isnull=True),
                name="uniq_mouse_locus_null_gene_genotype",
            ),
        ),
    ]
