from django import template

register = template.Library()


@register.inclusion_tag("includes/sortable_th.html", takes_context=True)
def sort_th(context, column_key: str, label: str, *, extra_class: str = ""):
    links = context.get("sort_links") or {}
    info = links.get(column_key, {})
    return {
        "label": label,
        "href": info.get("href"),
        "active": info.get("active"),
        "dir": info.get("dir"),
        "extra_class": extra_class,
    }
