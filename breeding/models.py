from django.db import OperationalError, ProgrammingError, models
from django.core.exceptions import ValidationError

from colony.models import Cage, Mouse
from core.models import ActorStampedModel, TimeStampedModel
from .dates import expected_birth_date_for


class Breeding(ActorStampedModel):
    class MemberRole(models.TextChoices):
        SIRE = "sire", "Sire"
        DAM = "dam", "Dam"

    class BreedingType(models.TextChoices):
        PAIR = "pair", "Pair"
        TRIO = "trio", "Trio"
        CUSTOM = "custom", "Custom"

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
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-start_date", "breeding_code")
        indexes = [
            models.Index(fields=["active", "start_date"], name="breeding_active_start"),
            models.Index(fields=["status", "active"], name="breeding_status_active"),
            models.Index(fields=["active", "male"], name="breeding_active_male"),
            models.Index(fields=["active", "female_1"], name="breeding_active_f1"),
            models.Index(fields=["active", "female_2"], name="breeding_active_f2"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(active=False) | ~models.Q(status="closed"),
                name="breeding_active_status_consistent",
            ),
        ]

    def clean(self) -> None:
        if not self.cage_id and not getattr(self, "_allow_pending_auto_cage", False):
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
        if not self.expected_birth_date:
            self.expected_birth_date = expected_birth_date_for(
                start_date=self.start_date,
                plug_date=self.plug_date,
            )

    def __str__(self) -> str:
        return self.breeding_code

    def member_mice(self) -> list[Mouse]:
        """All sire/dam mice on this breeding, including extra females."""
        try:
            members = [row.mouse for row in self.breeding_members.select_related("mouse").all()]
        except (OperationalError, ProgrammingError):
            members = []
        if members:
            return members
        fallback = [
            self.male,
            self.female_1,
            self.female_2,
            *[row.mouse for row in self.extra_female_links.select_related("mouse").all()],
        ]
        return [mouse for mouse in fallback if mouse is not None]

    def sync_members_from_legacy_fields(self) -> None:
        """Keep flexible breeding members synced from legacy fixed fields."""
        entries: list[tuple[str, int, Mouse]] = []
        if self.male_id:
            entries.append((self.MemberRole.SIRE, 1, self.male))
        if self.female_1_id:
            entries.append((self.MemberRole.DAM, 1, self.female_1))
        if self.female_2_id:
            entries.append((self.MemberRole.DAM, 2, self.female_2))
        for idx, link in enumerate(self.extra_female_links.select_related("mouse").order_by("mouse__mouse_uid"), start=3):
            entries.append((self.MemberRole.DAM, idx, link.mouse))

        BreedingMember.objects.filter(breeding=self).exclude(mouse_id__in=[m.pk for _, _, m in entries]).delete()
        existing = {(m.mouse_id, m.role): m for m in BreedingMember.objects.filter(breeding=self)}
        to_create: list[BreedingMember] = []
        to_update: list[BreedingMember] = []
        for role, sort_order, mouse in entries:
            key = (mouse.pk, role)
            found = existing.get(key)
            if found:
                if found.sort_order != sort_order:
                    found.sort_order = sort_order
                    to_update.append(found)
            else:
                to_create.append(BreedingMember(breeding=self, mouse=mouse, role=role, sort_order=sort_order))
        if to_create:
            BreedingMember.objects.bulk_create(to_create)
        if to_update:
            BreedingMember.objects.bulk_update(to_update, ["sort_order"])


class Litter(models.Model):
    """Intermediate colony stage between breeding and fully tracked individual mice."""

    class LitterStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        WEANED = "weaned", "Weaned"
        TAIL_TAGGED = "tail_tagged", "Tail tagged"
        ENDED = "ended", "Ended"
        ARCHIVED = "archived", "Archived"

    class EndOutcome(models.TextChoices):
        WEANED_COMPLETE = "weaned_complete", "Weaning and pup registration complete"
        ALL_PUPS_DIED = "all_pups_died", "All pups died before weaning"
        RECORD_ERROR = "record_error", "Litter record was entered in error"
        TRANSFERRED = "transferred", "Pups transferred outside this colony"
        NOT_TRACKED = "not_tracked", "Pups intentionally not tracked individually"
        OTHER = "other", "Other documented outcome"

    breeding = models.ForeignKey(Breeding, on_delete=models.CASCADE, related_name="litters")
    litter_code = models.CharField(max_length=64, unique=True, null=True, blank=True)
    birth_date = models.DateField()
    total_born = models.PositiveIntegerField(null=True, blank=True)
    alive_count = models.PositiveIntegerField(null=True, blank=True)
    dead_count = models.PositiveIntegerField(null=True, blank=True)
    male_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Optional count of male pups (can be derived from pup records when present).",
    )
    female_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Optional count of female pups (can be derived from pup records when present).",
    )
    wean_date = models.DateField(null=True, blank=True)
    tail_tag_date = models.DateField(
        null=True,
        blank=True,
        help_text="Lab-wide tail-tag event date for this litter (optional).",
    )
    litter_status = models.CharField(
        max_length=20,
        choices=LitterStatus.choices,
        default=LitterStatus.ACTIVE,
        db_index=True,
    )
    is_archived = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    end_outcome = models.CharField(max_length=32, choices=EndOutcome.choices, blank=True)
    end_notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-birth_date", "litter_code")
        indexes = [
            models.Index(fields=["birth_date"], name="breeding_litter_birth"),
            models.Index(fields=["wean_date"], name="breeding_litter_wean"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(is_archived=False)
                    & ~models.Q(litter_status__in=["ended", "archived"])
                ) | (
                    models.Q(is_archived=True)
                    & models.Q(litter_status__in=["ended", "archived"])
                ),
                name="litter_archive_status_consistent",
            ),
        ]

    def clean(self) -> None:
        for field_name in ("total_born", "alive_count", "dead_count", "male_count", "female_count"):
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
        if (
            self.male_count is not None
            and self.female_count is not None
            and self.total_born is not None
            and (self.male_count + self.female_count) > self.total_born
        ):
            raise ValidationError("male_count + female_count must not exceed total_born when total_born is set.")

    def __str__(self) -> str:
        return self.litter_code or f"{self.breeding.breeding_code} - {self.birth_date}"

    @property
    def litter_id_display(self) -> str:
        """Human-facing litter identifier (code or stable synthetic id)."""
        if self.litter_code:
            return self.litter_code
        return f"L-{self.pk}"


class LitterPup(TimeStampedModel):
    """Per-pup row on a litter before or after promotion to a Mouse record."""

    litter = models.ForeignKey(Litter, on_delete=models.CASCADE, related_name="pups")
    sort_order = models.PositiveSmallIntegerField(default=0)
    sex = models.CharField(max_length=1, choices=Mouse.Sex.choices, default=Mouse.Sex.UNKNOWN)
    ear_tag = models.CharField(max_length=64, blank=True)
    toe_tag = models.CharField(max_length=64, blank=True)
    coat_color = models.CharField(max_length=64, blank=True)
    tail_tag_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    mouse = models.OneToOneField(
        Mouse,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="litter_pup_origin",
    )

    class Meta:
        ordering = ("litter", "sort_order", "id")

    def __str__(self) -> str:
        return f"Pup {self.sort_order or self.pk} on {self.litter}"


class BreedingExtraFemale(models.Model):
    """Optional additional female breeders for flexible mating setups."""

    breeding = models.ForeignKey(Breeding, on_delete=models.CASCADE, related_name="extra_female_links")
    mouse = models.ForeignKey(Mouse, on_delete=models.PROTECT, related_name="extra_female_breedings")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["breeding", "mouse"], name="uq_breeding_extra_female"),
        ]
        ordering = ("breeding", "mouse__mouse_uid")

    def clean(self) -> None:
        if self.mouse and self.mouse.sex != Mouse.Sex.FEMALE:
            raise ValidationError("extra breeder mouse must be female.")

    def __str__(self) -> str:
        return f"{self.breeding.breeding_code} + {self.mouse.mouse_uid}"


class BreedingMember(models.Model):
    """Flexible breeder membership model (transition from fixed legacy fields)."""

    breeding = models.ForeignKey(Breeding, on_delete=models.CASCADE, related_name="breeding_members")
    mouse = models.ForeignKey(Mouse, on_delete=models.PROTECT, related_name="breeding_memberships")
    role = models.CharField(max_length=12, choices=Breeding.MemberRole.choices)
    sort_order = models.PositiveSmallIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["breeding", "mouse"], name="uq_breeding_member_mouse"),
        ]
        ordering = ("breeding", "role", "sort_order", "mouse__mouse_uid")

    def clean(self) -> None:
        if self.role == Breeding.MemberRole.SIRE and self.mouse.sex != Mouse.Sex.MALE:
            raise ValidationError("Sire role requires a male mouse.")
        if self.role == Breeding.MemberRole.DAM and self.mouse.sex != Mouse.Sex.FEMALE:
            raise ValidationError("Dam role requires a female mouse.")

    def __str__(self) -> str:
        return f"{self.breeding.breeding_code} {self.get_role_display()} {self.mouse.mouse_uid}"
