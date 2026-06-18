from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from colony.models import Mouse
from .models import Breeding


def breeding_litter_timing_alert(
    *,
    start_date: date | None,
    latest_litter_date: date | None,
    litter_count: int,
    is_active: bool,
    status: str,
    today: date,
) -> dict | None:
    if not is_active or status == Breeding.Status.CLOSED:
        return None
    reference = latest_litter_date or start_date
    if reference is None:
        return None
    days_without = (today - reference).days
    if days_without <= 21:
        return None
    if days_without > 42:
        level = "review"
        label = "Review pair"
    elif days_without > 35:
        level = "overdue"
        label = "Breeding overdue"
    else:
        level = "warning"
        label = "No litter yet" if litter_count == 0 else "No recent litter"
    return {
        "level": level,
        "label": label,
        "days_without_litter": days_without,
        "latest_litter_date": latest_litter_date,
        "has_litter": litter_count > 0,
    }


def _parse_genotype_pair(text: str) -> tuple[str, str] | None:
    cleaned = (text or "").replace(" ", "")
    if "/" not in cleaned:
        return None
    a1, a2 = cleaned.split("/", 1)
    if not a1 or not a2:
        return None
    return a1, a2


def _gamete_probabilities(a1: str, a2: str) -> dict[str, float]:
    if a1 == a2:
        return {a1: 1.0}
    return {a1: 0.5, a2: 0.5}


def _canonical_genotype(a1: str, a2: str) -> str:
    left, right = sorted([a1, a2], key=lambda s: (s != "+", s))
    return f"{left}/{right}"


def _expected_ratio_from_parents(parent1: str, parent2: str) -> dict[str, float] | None:
    p1 = _parse_genotype_pair(parent1)
    p2 = _parse_genotype_pair(parent2)
    if not p1 or not p2:
        return None
    if p1[1].upper() == "Y" or p2[1].upper() == "Y":
        return None
    g1 = _gamete_probabilities(*p1)
    g2 = _gamete_probabilities(*p2)
    ratio: dict[str, float] = defaultdict(float)
    for a, pa in g1.items():
        for b, pb in g2.items():
            ratio[_canonical_genotype(a, b)] += pa * pb
    return dict(sorted(ratio.items()))


def mendelian_single_locus_review_for_breeding(
    breeding: Breeding,
    offspring: list[Mouse],
    *,
    sire: Mouse | None = None,
    dams: list[Mouse] | None = None,
    min_sample_size: int = 8,
) -> list[dict]:
    sire = sire or breeding.male
    dams = dams if dams is not None else [breeding.female_1]
    dams = [dam for dam in dams if dam is not None]
    if sire is None or len(dams) != 1:
        return []
    dam = dams[0]
    sire_components = {c.locus_name: c for c in sire.genotype_components.all() if c.locus_name}
    dam_components = {c.locus_name: c for c in dam.genotype_components.all() if c.locus_name}
    common_loci = sorted(set(sire_components.keys()) & set(dam_components.keys()))
    if not common_loci:
        return []

    offspring_locus_counts: dict[str, Counter] = defaultdict(Counter)
    for mouse in offspring:
        for comp in mouse.genotype_components.all():
            locus = (comp.locus_name or "").strip()
            if not locus:
                continue
            z = (comp.zygosity or "").strip()
            if not _parse_genotype_pair(z):
                continue
            if "/Y" in z.upper():
                continue
            offspring_locus_counts[locus][_canonical_genotype(*_parse_genotype_pair(z))] += 1

    results: list[dict] = []
    for locus in common_loci:
        sire_disp = (sire_components[locus].zygosity or "").strip()
        dam_disp = (dam_components[locus].zygosity or "").strip()
        expected = _expected_ratio_from_parents(sire_disp, dam_disp)
        if not expected:
            continue
        observed_counter = offspring_locus_counts.get(locus, Counter())
        observed_total = sum(observed_counter.values())
        if observed_total == 0:
            continue

        observed_pct = {
            k: (observed_counter.get(k, 0) / observed_total) * 100.0
            for k in sorted(set(expected.keys()) | set(observed_counter.keys()))
        }
        expected_pct = {k: v * 100.0 for k, v in expected.items()}

        if observed_total < min_sample_size:
            status = "insufficient"
            message = "Sample size is small; avoid strong conclusions."
            differs = False
        else:
            max_diff = 0.0
            for k in set(expected_pct.keys()) | set(observed_pct.keys()):
                max_diff = max(max_diff, abs(observed_pct.get(k, 0.0) - expected_pct.get(k, 0.0)))
            differs = max_diff >= 20.0
            if differs:
                status = "review"
                message = "Observed ratio differs from expectation. Review genotyping or breeding outcome."
            else:
                status = "ok"
                message = "Observed ratio is broadly aligned with expectation."

        results.append(
            {
                "locus": locus,
                "parent_genotype": {"sire": sire_disp, "dam": dam_disp},
                "expected_ratio": expected,
                "expected_pct": expected_pct,
                "observed_counts": dict(observed_counter),
                "observed_pct": observed_pct,
                "observed_total": observed_total,
                "status": status,
                "differs": differs,
                "message": message,
            }
        )
    return results
