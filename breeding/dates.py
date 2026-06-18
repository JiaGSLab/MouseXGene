from __future__ import annotations

from datetime import date, timedelta


def expected_birth_date_for(
    *,
    start_date: date | None = None,
    plug_date: date | None = None,
    manual_date: date | None = None,
) -> date | None:
    if manual_date:
        return manual_date
    if plug_date:
        return plug_date + timedelta(days=19)
    if start_date:
        return start_date + timedelta(days=21)
    return None
