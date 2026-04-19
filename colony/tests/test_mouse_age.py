from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from colony.mouse_age import (
    DAY_LIST_ONE_YEAR,
    DAY_LIST_SIX_MONTH,
    DAY_TIER_CAUTION,
    DAY_TIER_ELEVATED,
    DAY_TIER_HIGH,
    BreedingAgeTier,
    breeding_age_tier,
    mouse_list_age_band,
)


class BreedingAgeTierTests(TestCase):
    def test_tier_boundaries(self):
        today = timezone.localdate()
        self.assertEqual(
            breeding_age_tier(today - timedelta(days=DAY_TIER_ELEVATED - 1), today),
            BreedingAgeTier.NONE,
        )
        self.assertEqual(
            breeding_age_tier(today - timedelta(days=DAY_TIER_ELEVATED), today),
            BreedingAgeTier.ELEVATED,
        )
        self.assertEqual(
            breeding_age_tier(today - timedelta(days=DAY_TIER_CAUTION - 1), today),
            BreedingAgeTier.ELEVATED,
        )
        self.assertEqual(
            breeding_age_tier(today - timedelta(days=DAY_TIER_CAUTION), today),
            BreedingAgeTier.CAUTION,
        )
        self.assertEqual(
            breeding_age_tier(today - timedelta(days=DAY_TIER_HIGH - 1), today),
            BreedingAgeTier.CAUTION,
        )
        self.assertEqual(
            breeding_age_tier(today - timedelta(days=DAY_TIER_HIGH), today),
            BreedingAgeTier.HIGH,
        )

    def test_no_birth_date(self):
        self.assertEqual(breeding_age_tier(None), BreedingAgeTier.NONE)


class MouseListAgeBandTests(TestCase):
    def test_list_bands(self):
        today = timezone.localdate()
        self.assertEqual(mouse_list_age_band(None, today), "unknown")
        self.assertEqual(mouse_list_age_band(today - timedelta(days=100), today), "")
        self.assertEqual(mouse_list_age_band(today - timedelta(days=DAY_LIST_SIX_MONTH), today), "6mo")
        self.assertEqual(mouse_list_age_band(today - timedelta(days=DAY_LIST_ONE_YEAR - 1), today), "6mo")
        self.assertEqual(mouse_list_age_band(today - timedelta(days=DAY_LIST_ONE_YEAR), today), "1yr")
