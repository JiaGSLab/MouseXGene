from datetime import date

from django.core.exceptions import ValidationError

from .models import Mouse


TERMINAL_STATUSES = {
    Mouse.Status.DEAD,
    Mouse.Status.EUTHANIZED,
    Mouse.Status.CULLED,
}


def apply_terminal_status(
    mouse: Mouse,
    *,
    status: str,
    end_date: date,
    reason: str,
) -> None:
    """Apply one consistent date convention for every terminal mouse workflow."""
    if status not in TERMINAL_STATUSES:
        raise ValidationError("Choose Dead, Euthanized, or Culled as the terminal status.")
    mouse.status = status
    mouse.death_reason = (reason or "").strip()
    if status == Mouse.Status.DEAD:
        mouse.death_date = end_date
        mouse.euthanasia_date = None
    else:
        mouse.death_date = None
        mouse.euthanasia_date = end_date
    mouse.save(
        update_fields=[
            "status",
            "death_date",
            "euthanasia_date",
            "death_reason",
            "updated_at",
        ]
    )
