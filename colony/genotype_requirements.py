from django.db.models import Q

from colony.models import StrainLine


PLAIN_WT_NAME_ALIASES = (
    "WT",
    "Wild type",
    "Wild-type",
    "Wildtype",
    "C57BL/6",
    "C57BL/6J",
    "B6",
    "BALB/c",
    "BALB/cJ",
    "NSG",
    "NOD-SCID",
)


def plain_wt_strain_line_q(*, prefix: str = "strain_line__") -> Q:
    """Strain lines that should not require per-mouse genotype rows unless loci are configured."""
    q = Q(**{f"{prefix}category__in": [StrainLine.Category.WILD_TYPE, StrainLine.Category.INBRED_STRAIN]})
    for field_name in ("line_name", "name", "display_name", "short_name", "key_name"):
        for alias in PLAIN_WT_NAME_ALIASES:
            q |= Q(**{f"{prefix}{field_name}__iexact": alias})
    return q


def strain_line_has_expected_loci_q(*, prefix: str = "strain_line__") -> Q:
    return ~Q(**{f"{prefix}expected_loci_template": ""}) | ~Q(**{f"{prefix}expected_loci_config": []})


def mouse_requires_genotype_q(*, prefix: str = "strain_line__") -> Q:
    """Mouse rows needing genotype QA: loci configured, or a non-WT/non-inbred line."""
    return strain_line_has_expected_loci_q(prefix=prefix) | ~plain_wt_strain_line_q(prefix=prefix)
