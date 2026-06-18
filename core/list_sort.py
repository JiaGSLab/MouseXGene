"""Shared GET-based column sorting for list views."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from django.db.models import Case, CharField, Count, F, Min, Q, QuerySet, Value, When
from django.db.models.functions import Coalesce, Lower
from django.http import HttpRequest
from django.urls import reverse


class SortKind(Enum):
    TEXT = "text"
    DATE = "date"
    AGE = "age"
    NUMBER = "number"
    ENUM = "enum"


@dataclass(frozen=True)
class SortColumn:
    key: str
    kind: SortKind
    fields: tuple[str, ...]
    tie_breaker: tuple[str, ...] = ()
    prepare: Callable[[QuerySet], QuerySet] | None = None


@dataclass
class ListSortRegistry:
    columns: dict[str, SortColumn]
    default: tuple[str, ...] = ()

    def keys(self) -> list[str]:
        return list(self.columns.keys())


def parse_list_sort(request: HttpRequest, registry: ListSortRegistry) -> tuple[str | None, str | None]:
    sort = (request.GET.get("sort") or "").strip()
    direction = (request.GET.get("dir") or "").strip().lower()
    if sort not in registry.columns:
        sort = None
    if sort is None:
        return None, None
    if direction not in {"desc", "asc"}:
        direction = "desc"
    return sort, direction


def _order_exprs(col: SortColumn, *, descending: bool) -> list[Any]:
    if col.kind is SortKind.AGE:
        descending = not descending
    orders: list[Any] = []
    for name in col.fields:
        if col.kind in {SortKind.DATE, SortKind.AGE}:
            expr = F(name)
            orders.append(expr.desc(nulls_last=True) if descending else expr.asc(nulls_last=True))
        elif col.kind is SortKind.NUMBER:
            expr = F(name)
            orders.append(expr.desc(nulls_last=True) if descending else expr.asc(nulls_last=True))
        elif col.kind is SortKind.TEXT:
            expr = Lower(name)
            orders.append(expr.desc(nulls_last=True) if descending else expr.asc(nulls_last=True))
        else:
            orders.append(f"-{name}" if descending else name)
    for tie in col.tie_breaker:
        orders.append(tie)
    return orders


def apply_list_sort(queryset: QuerySet, request: HttpRequest, registry: ListSortRegistry) -> QuerySet:
    sort, direction = parse_list_sort(request, registry)
    if sort is None:
        return queryset.order_by(*registry.default)
    col = registry.columns[sort]
    qs = queryset
    if col.prepare is not None:
        qs = col.prepare(qs)
    descending = direction == "desc"
    return qs.order_by(*_order_exprs(col, descending=descending))


def build_list_sort_context(
    request: HttpRequest,
    viewname: str,
    registry: ListSortRegistry,
    *,
    extra_query_pop: tuple[str, ...] = ("export", "page"),
) -> dict[str, Any]:
    sort, direction = parse_list_sort(request, registry)
    sort_links: dict[str, dict[str, Any]] = {}
    for key in registry.columns:
        q = request.GET.copy()
        for param in extra_query_pop:
            q.pop(param, None)
        if sort == key:
            new_dir = "asc" if direction == "desc" else "desc"
        else:
            new_dir = "desc"
        q["sort"] = key
        q["dir"] = new_dir
        qs = q.urlencode()
        base = reverse(viewname)
        sort_links[key] = {
            "href": f"{base}?{qs}" if qs else base,
            "active": sort == key,
            "dir": direction if sort == key else None,
        }
    return {
        "list_sort": sort,
        "list_sort_dir": direction,
        "sort_links": sort_links,
    }


def _owner_label(prefix: str) -> Coalesce:
    return Coalesce(
        Lower(f"{prefix}__profile__display_name"),
        Lower(f"{prefix}__username"),
        Value(""),
    )


def _prepare_cage_list_sort(qs: QuerySet) -> QuerySet:
    active_mice = Q(current_mice__status="active")
    return qs.annotate(
        _sort_mouse_count=Count("current_mice", distinct=True),
        _sort_project=Min("current_mice__project__name", filter=active_mice),
        _sort_owner=Min(
            Coalesce(
                Lower("current_mice__project__owner__profile__display_name"),
                Lower("current_mice__project__owner__username"),
                Value(""),
            ),
            filter=active_mice,
        ),
    )


def _prepare_cage_use_sort(qs: QuerySet) -> QuerySet:
    return qs.annotate(
        _sort_cage_use=Case(
            When(purpose="retired", then=Value("retired")),
            When(Q(purpose="breeding") | Q(cage_type="breeding"), then=Value("breeding")),
            When(cage_type="weaning", then=Value("weaning")),
            When(purpose="experiment", then=Value("experiment")),
            When(cage_type="quarantine", then=Value("quarantine")),
            default=Value("holding"),
            output_field=CharField(),
        )
    )


def _prepare_mouse_breeding_sort(qs: QuerySet) -> QuerySet:
    active = Q(sired_breedings__active=True)
    active_dam = Q(maternal_breedings_primary__active=True)
    active_member = Q(breeding_memberships__breeding__active=True)
    return qs.annotate(
        _sort_breed_s=Min("sired_breedings__breeding_code", filter=active),
        _sort_breed_d=Min("maternal_breedings_primary__breeding_code", filter=active_dam),
        _sort_breed_member=Min("breeding_memberships__breeding__breeding_code", filter=active_member),
    ).annotate(_sort_breeding=Coalesce("_sort_breed_s", "_sort_breed_d", "_sort_breed_member", Value("")))


def _prepare_breeding_alert_sort(qs: QuerySet) -> QuerySet:
    return qs.annotate(_sort_alert_ref=Coalesce(F("latest_litter_date"), F("start_date")))


CAGE_LIST_SORT = ListSortRegistry(
    columns={
        "cage_id": SortColumn("cage_id", SortKind.TEXT, ("cage_id",), tie_breaker=("pk",)),
        "room": SortColumn("room", SortKind.TEXT, ("room",), tie_breaker=("cage_id",)),
        "rack": SortColumn("rack", SortKind.TEXT, ("rack",), tie_breaker=("cage_id",)),
        "position": SortColumn("position", SortKind.TEXT, ("position",), tie_breaker=("cage_id",)),
        "cage_use": SortColumn(
            "cage_use",
            SortKind.ENUM,
            ("_sort_cage_use",),
            tie_breaker=("cage_id",),
            prepare=_prepare_cage_use_sort,
        ),
        "cage_type": SortColumn("cage_type", SortKind.ENUM, ("cage_type",), tie_breaker=("cage_id",)),
        "purpose": SortColumn("purpose", SortKind.ENUM, ("purpose",), tie_breaker=("cage_id",)),
        "mouse_count": SortColumn(
            "mouse_count",
            SortKind.NUMBER,
            ("_sort_mouse_count",),
            tie_breaker=("cage_id",),
            prepare=_prepare_cage_list_sort,
        ),
        "project": SortColumn(
            "project",
            SortKind.TEXT,
            ("_sort_project",),
            tie_breaker=("cage_id",),
            prepare=_prepare_cage_list_sort,
        ),
        "owner": SortColumn(
            "owner",
            SortKind.TEXT,
            ("_sort_owner",),
            tie_breaker=("cage_id",),
            prepare=_prepare_cage_list_sort,
        ),
        "status": SortColumn("status", SortKind.ENUM, ("status",), tie_breaker=("cage_id",)),
    },
    default=("cage_id",),
)

MICE_LIST_SORT = ListSortRegistry(
    columns={
        "mouse_uid": SortColumn("mouse_uid", SortKind.TEXT, ("mouse_uid",)),
        "genotype": SortColumn("genotype", SortKind.TEXT, ("genotype_summary",), tie_breaker=("mouse_uid",)),
        "sex": SortColumn("sex", SortKind.ENUM, ("sex",), tie_breaker=("mouse_uid",)),
        "birth_date": SortColumn("birth_date", SortKind.DATE, ("birth_date",), tie_breaker=("mouse_uid",)),
        "age": SortColumn("age", SortKind.AGE, ("birth_date",), tie_breaker=("mouse_uid",)),
        "status": SortColumn("status", SortKind.ENUM, ("status",), tie_breaker=("mouse_uid",)),
        "breeding": SortColumn(
            "breeding",
            SortKind.TEXT,
            ("_sort_breeding",),
            tie_breaker=("mouse_uid",),
            prepare=_prepare_mouse_breeding_sort,
        ),
        "strain_line": SortColumn(
            "strain_line", SortKind.TEXT, ("strain_line__line_name",), tie_breaker=("mouse_uid",)
        ),
        "cage": SortColumn("cage", SortKind.TEXT, ("current_cage__cage_id",), tie_breaker=("mouse_uid",)),
        "project": SortColumn("project", SortKind.TEXT, ("project__name",), tie_breaker=("mouse_uid",)),
        "owner": SortColumn(
            "owner",
            SortKind.TEXT,
            ("_sort_owner",),
            tie_breaker=("mouse_uid",),
            prepare=lambda qs: qs.annotate(_sort_owner=_owner_label("project__owner")),
        ),
    },
    default=("-birth_date", "mouse_uid"),
)

BREEDING_LIST_SORT = ListSortRegistry(
    columns={
        "setup_by": SortColumn(
            "setup_by",
            SortKind.TEXT,
            ("created_by__profile__display_name", "created_by__username"),
            tie_breaker=("breeding_code",),
            prepare=lambda qs: qs.select_related("created_by", "created_by__profile"),
        ),
        "alert": SortColumn(
            "alert",
            SortKind.AGE,
            ("_sort_alert_ref",),
            tie_breaker=("breeding_code",),
            prepare=_prepare_breeding_alert_sort,
        ),
        "breeding_code": SortColumn("breeding_code", SortKind.TEXT, ("breeding_code",)),
        "cage": SortColumn("cage", SortKind.TEXT, ("cage__cage_id",), tie_breaker=("breeding_code",)),
        "breeding_type": SortColumn("breeding_type", SortKind.ENUM, ("breeding_type",), tie_breaker=("breeding_code",)),
        "sire": SortColumn("sire", SortKind.TEXT, ("male__mouse_uid",), tie_breaker=("breeding_code",)),
        "dams": SortColumn("dams", SortKind.TEXT, ("female_1__mouse_uid",), tie_breaker=("breeding_code",)),
        "start_date": SortColumn("start_date", SortKind.DATE, ("start_date",), tie_breaker=("breeding_code",)),
        "plug_date": SortColumn("plug_date", SortKind.DATE, ("plug_date",), tie_breaker=("breeding_code",)),
        "expected_birth_date": SortColumn(
            "expected_birth_date", SortKind.DATE, ("expected_birth_date",), tie_breaker=("breeding_code",)
        ),
        "status": SortColumn("status", SortKind.ENUM, ("status",), tie_breaker=("breeding_code",)),
    },
    default=("-start_date", "breeding_code"),
)

LITTER_LIST_SORT = ListSortRegistry(
    columns={
        "owner": SortColumn(
            "owner",
            SortKind.TEXT,
            ("_sort_owner",),
            tie_breaker=("litter_code",),
            prepare=lambda qs: qs.select_related("breeding__male__project__owner__profile").annotate(
                _sort_owner=_owner_label("breeding__male__project__owner")
            ),
        ),
        "litter_code": SortColumn("litter_code", SortKind.TEXT, ("litter_code",), tie_breaker=("pk",)),
        "breeding": SortColumn("breeding", SortKind.TEXT, ("breeding__breeding_code",), tie_breaker=("litter_code",)),
        "birth_date": SortColumn("birth_date", SortKind.DATE, ("birth_date",), tie_breaker=("litter_code",)),
        "age": SortColumn("age", SortKind.AGE, ("birth_date",), tie_breaker=("litter_code",)),
        "pups": SortColumn("pups", SortKind.NUMBER, ("_pup_total",), tie_breaker=("litter_code",)),
        "wean_due": SortColumn("wean_due", SortKind.DATE, ("birth_date",), tie_breaker=("litter_code",)),
        "weaning_status": SortColumn("weaning_status", SortKind.ENUM, ("wean_date",), tie_breaker=("litter_code",)),
        "tagging_status": SortColumn("tagging_status", SortKind.DATE, ("tail_tag_date",), tie_breaker=("litter_code",)),
        "mice_created": SortColumn(
            "mice_created", SortKind.NUMBER, ("_created_mouse_count",), tie_breaker=("litter_code",)
        ),
        "status": SortColumn("litter_status", SortKind.ENUM, ("litter_status",), tie_breaker=("litter_code",)),
        "parent_lines": SortColumn(
            "parent_lines",
            SortKind.TEXT,
            ("breeding__male__strain_line__line_name",),
            tie_breaker=("litter_code",),
        ),
    },
    default=("-birth_date", "litter_code"),
)

PROJECT_LIST_SORT = ListSortRegistry(
    columns={
        "name": SortColumn("name", SortKind.TEXT, ("name",)),
        "owner": SortColumn(
            "owner",
            SortKind.TEXT,
            ("_sort_owner",),
            tie_breaker=("name",),
            prepare=lambda qs: qs.select_related("owner", "owner__profile").annotate(
                _sort_owner=_owner_label("owner")
            ),
        ),
        "active": SortColumn("active", SortKind.ENUM, ("is_active",), tie_breaker=("name",)),
    },
    default=("name",),
)

STRAIN_LINE_LIST_SORT = ListSortRegistry(
    columns={
        "name": SortColumn("name", SortKind.TEXT, ("name", "line_name")),
        "owner": SortColumn(
            "owner",
            SortKind.TEXT,
            ("_sort_owner",),
            tie_breaker=("name",),
            prepare=lambda qs: qs.select_related("owner", "owner__profile").annotate(
                _sort_owner=_owner_label("owner")
            ),
        ),
        "loci": SortColumn("loci", SortKind.TEXT, ("expected_loci_template",), tie_breaker=("name",)),
        "pdf_count": SortColumn("pdf_count", SortKind.NUMBER, ("pdf_count",), tie_breaker=("name",)),
        "active_mice": SortColumn("active_mice", SortKind.NUMBER, ("active_mice_count",), tie_breaker=("name",)),
        "active_cages": SortColumn("active_cages", SortKind.NUMBER, ("active_cages_count",), tie_breaker=("name",)),
        "active_breedings": SortColumn(
            "active_breedings", SortKind.NUMBER, ("active_breedings_count",), tie_breaker=("name",)
        ),
        "active_litters": SortColumn(
            "active_litters", SortKind.NUMBER, ("active_litters_count",), tie_breaker=("name",)
        ),
        "active": SortColumn("active", SortKind.ENUM, ("is_active",), tie_breaker=("name",)),
    },
    default=("name", "line_name"),
)

FAMILY_TREE_SORT = ListSortRegistry(
    columns={
        "mouse_uid": SortColumn("mouse_uid", SortKind.TEXT, ("mouse_uid",)),
        "sire": SortColumn("sire", SortKind.TEXT, ("sire__mouse_uid",), tie_breaker=("mouse_uid",)),
        "breeding_cage": SortColumn(
            "breeding_cage",
            SortKind.TEXT,
            ("source_breeding__cage__cage_id",),
            tie_breaker=("mouse_uid",),
        ),
        "dam": SortColumn("dam", SortKind.TEXT, ("dam__mouse_uid",), tie_breaker=("mouse_uid",)),
        "cage": SortColumn("cage", SortKind.TEXT, ("current_cage__cage_id",), tie_breaker=("mouse_uid",)),
        "strain_line": SortColumn(
            "strain_line", SortKind.TEXT, ("strain_line__line_name",), tie_breaker=("mouse_uid",)
        ),
        "genotype": SortColumn("genotype", SortKind.TEXT, ("genotype_summary",), tie_breaker=("mouse_uid",)),
        "project": SortColumn("project", SortKind.TEXT, ("project__name",), tie_breaker=("mouse_uid",)),
        "owner": SortColumn(
            "owner",
            SortKind.TEXT,
            ("_sort_owner",),
            tie_breaker=("mouse_uid",),
            prepare=lambda qs: qs.annotate(_sort_owner=_owner_label("project__owner")),
        ),
    },
    default=("-birth_date", "mouse_uid"),
)
